from __future__ import annotations

import re
from typing import Any

from ..llm import SYSTEM_GUARD, llm
from ..resume_service import apply_changes, flatten, get_path, set_path
from ..schemas import ProposedChange, ResumeContent, TranslationResult, ValidationResult
from .common import summarize_resume_for_prompt
from .section_enhancement import _change_id


class TranslationAgent:
    name = "translation"

    async def translate(
        self,
        *,
        content: dict[str, Any],
        facts: list[dict[str, Any]],
        language: str,
        market: str,
    ) -> tuple[dict[str, Any] | None, list[ProposedChange], ValidationResult, dict[str, Any]]:
        language = (language or "").strip()
        market = (market or "").strip()
        meta: dict[str, Any] = {"mode": "llm", "provider": "openai", "language": language, "market": market}
        if not language:
            return None, [], ValidationResult(
                valid=False, errors=["A target language is required for translation."], requires_confirmation=True,
                clarification_question="Which language and market should the resume be localized for?",
            ), meta
        if not llm.enabled:
            return None, [], ValidationResult(
                valid=False,
                errors=["Translation requires an LLM provider. Configure OPENAI_API_KEY and retry."],
                requires_confirmation=False,
            ), {"mode": "unavailable", "provider": "local", "language": language, "market": market}

        result = await llm.structured(
            system=SYSTEM_GUARD,
            prompt=(
                f"Translate and localize this resume into {language}"
                + (f" for the {market} market" if market else "")
                + ". Preserve the exact JSON structure and every proper noun (employers, institutions, product "
                "names), certification name, date, number, percentage and metric. Preserve technical terms in "
                "their standard industry form when a literal translation would be confusing. Adapt section "
                "headings naturally, but do not invent local qualifications, degree equivalencies, employers, "
                "skills or achievements. Keep bullets concise.\n\n"
                f"<resume_json>\n{summarize_resume_for_prompt(content, max_chars=14000)}\n</resume_json>"
            ),
            schema=TranslationResult,
            temperature=0.1,
            max_tokens=6000,
        )
        if not result.ok:
            return None, [], ValidationResult(valid=False, errors=[f"Translation failed: {result.error}"]), {
                "mode": "failed", "provider": "openai", "error": result.error,
            }

        translated = result.data.translated_content
        try:
            validated = ResumeContent.model_validate(translated).model_dump(mode="json")
        except Exception as exc:
            return None, [], ValidationResult(valid=False, errors=[f"Translated resume structure was invalid: {exc}"]), meta

        base_flat = flatten(content)
        translated_flat = flatten(validated)
        missing_paths = [p for p in base_flat if p not in translated_flat]
        if missing_paths:
            return None, [], ValidationResult(
                valid=False,
                errors=[f"Translation dropped {len(missing_paths)} fields; it was rejected to prevent data loss."],
            ), meta

        base_metrics = _metric_multiset("\n".join(base_flat.values()))
        translated_metrics = _metric_multiset("\n".join(translated_flat.values()))
        lost = sorted(set(base_metrics) - set(translated_metrics))
        if lost:
            return None, [], ValidationResult(
                valid=False,
                errors=[f"Translation did not preserve metric(s) {lost}; rejected to avoid altering achievements."],
            ), meta

        warnings: list[str] = []
        for label, value in (
            ("name", content.get("personal_information", {}).get("full_name")),
            ("company", " ".join(e.get("company", "") for e in content.get("experience", []) or [])),
        ):
            if value and value.strip() and value.strip() not in "\n".join(translated_flat.values()):
                warnings.append(f"Review the translated {label}: the original proper noun was not found verbatim.")

        changes: list[ProposedChange] = []
        index = 0
        for path, before in base_flat.items():
            after = translated_flat.get(path, "")
            if after == before:
                continue
            fact = next((f for f in facts if f.get("path") == path), None)
            changes.append(
                ProposedChange(
                    id=_change_id(path, after, index),
                    field_path=path,
                    before=before,
                    after=after,
                    reason=f"Localized to {language}{(' for the ' + market + ' market') if market else ''}; facts and metrics preserved.",
                    supporting_fact_ids=[fact["fact_id"]] if fact else [],
                    requires_user_confirmation=False,
                    agent=self.name,
                    section=path.split(".")[0],
                    status="proposed",
                )
            )
            index += 1
        if not changes:
            return validated, [], ValidationResult(valid=False, errors=["Translation produced no changes."]), meta

        validation = ValidationResult(valid=True, warnings=warnings[:10])
        meta.update({"mode": "llm", "changed_fields": len(changes)})
        return validated, changes, validation, meta


def _metric_multiset(text: str) -> list[str]:
    from ..resume_service import extract_metrics

    return sorted(extract_metrics(text))


translation_agent = TranslationAgent()
