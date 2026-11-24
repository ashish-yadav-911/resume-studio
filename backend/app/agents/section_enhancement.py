from __future__ import annotations

import hashlib
import re
from typing import Any

from ..llm import SYSTEM_GUARD, llm
from ..resume_service import (
    apply_changes,
    extract_metrics,
    flatten,
    get_path,
    normalize_text,
    resume_text,
    section_for_path,
)
from ..schemas import (
    ProposedChange,
    SectionEnhancementResult,
    ValidationResult,
)
from .common import extract_skills, summarize_resume_for_prompt

WEAK_VERBS = {
    "worked on": "Developed",
    "worked with": "Collaborated with",
    "helped": "Supported",
    "helped to": "Supported",
    "responsible for": "Led",
    "was responsible for": "Led",
    "assisted with": "Supported",
    "participated in": "Contributed to",
    "did": "Delivered",
    "made": "Built",
    "used": "Applied",
    "utilized": "Used",
    "handled": "Managed",
    "involved in": "Contributed to",
    "tasked with": "Led",
}

SECTION_PATHS = {
    "professional_summary": ["professional_summary"],
    "skills": ["skills"],
    "experience": ["experience"],
    "projects": ["projects"],
    "education": ["education"],
    "certifications": ["certifications"],
    "languages": ["languages"],
    "custom_sections": ["custom_sections"],
    "personal_information": ["personal_information"],
}

SECTION_ALIASES = {
    "summary": "professional_summary",
    "professional summary": "professional_summary",
    "profile": "professional_summary",
    "work experience": "experience",
    "employment": "experience",
    "technical skills": "skills",
    "skills": "skills",
    "projects": "projects",
    "education": "education",
    "certifications": "certifications",
    "languages": "languages",
}


def normalize_sections(sections: list[str], content: dict[str, Any]) -> list[str]:
    output: list[str] = []
    for section in sections or []:
        key = SECTION_ALIASES.get(section.strip().lower(), section.strip().lower())
        if key in SECTION_PATHS and key not in output:
            output.append(key)
    if not output:
        for section in ("experience", "skills", "professional_summary", "education"):
            if content.get(section):
                output.append(section)
    return output[:6]


def _change_id(path: str, after: str, index: int) -> str:
    digest = hashlib.sha1(f"{path}|{after}|{index}".encode()).hexdigest()[:10]
    return f"chg_{digest}"


class SectionEnhancementAgent:
    name = "section_enhancement"

    async def enhance(
        self,
        *,
        content: dict[str, Any],
        facts: list[dict[str, Any]],
        target_sections: list[str],
        user_request: str,
        target_role: str | None = None,
        jd_requirements: list[dict[str, Any]] | None = None,
        research_recommendations: list[str] | None = None,
    ) -> tuple[SectionEnhancementResult, ValidationResult, dict[str, Any]]:
        sections = normalize_sections(target_sections, content)
        meta: dict[str, Any] = {"mode": "llm", "provider": "openai", "sections": sections}
        if not sections:
            validation = ValidationResult(valid=False, errors=["No editable section was identified."])
            return SectionEnhancementResult(summary="No editable content was found."), validation, meta

        if llm.enabled:
            result = await self._llm_enhance(
                content=content,
                facts=facts,
                sections=sections,
                user_request=user_request,
                target_role=target_role,
                jd_requirements=jd_requirements or [],
                research_recommendations=research_recommendations or [],
            )
            if result.ok:
                enhancement: SectionEnhancementResult = result.data
                meta.update({"mode": "llm", "provider": "openai"})
            else:
                enhancement = self._heuristic_enhance(content, facts, sections, user_request)
                meta.update({"mode": "heuristic_fallback", "provider": "local", "error": result.error})
        else:
            enhancement = self._heuristic_enhance(content, facts, sections, user_request)
            meta.update({"mode": "heuristic", "provider": "local"})

        validated, validation, validated_meta = self._validate(
            content=content,
            facts=facts,
            sections=sections,
            enhancement=enhancement,
            user_request=user_request,
        )
        meta.update(validated_meta)
        return validated, validation, meta

    async def _llm_enhance(
        self,
        *,
        content: dict[str, Any],
        facts: list[dict[str, Any]],
        sections: list[str],
        user_request: str,
        target_role: str | None,
        jd_requirements: list[dict[str, Any]],
        research_recommendations: list[str],
    ) -> Any:
        selected = {section: content.get(section) for section in sections}
        relevant_facts = [
            f for f in facts
            if f.get("source_section") in sections and f.get("kind") != "personal"
        ][:40]
        fact_text = "\n".join(f"{f['fact_id']}: {f['value'][:260]}" for f in relevant_facts)
        requirements = "\n".join(
            f"- [{r.get('status', 'unknown')}] {r.get('requirement', '')[:200]}"
            for r in jd_requirements[:15]
        )
        recommendations = "\n".join(f"- {item}" for item in research_recommendations[:8])
        prompt = (
            f"Improve the selected resume sections for this request: {user_request}\n"
            f"Target role: {target_role or 'not specified'}\n\n"
            f"<selected_sections_json>\n{summarize_resume_for_prompt(selected, max_chars=9000)}\n</selected_sections_json>\n\n"
            f"<evidence_facts>\n{fact_text}\n</evidence_facts>\n\n"
            f"<job_requirements>\n{requirements or '(none)'}\n</job_requirements>\n\n"
            f"<company_research_recommendations>\n{recommendations or '(none)'}\n</company_research_recommendations>\n\n"
            "Rules:\n"
            "- Return only replacements for existing string fields. field_path must be a dot path with numeric "
            "indices, e.g. experience.0.bullets.1. before must be copied VERBATIM from the section JSON.\n"
            "- If the user asks to change a specific field (for example their name), make exactly that change and "
            "leave other fields untouched.\n"
            "- Improve specificity, action verbs, clarity and structure. Do not add employers, dates, degrees, "
            "tools, responsibilities, team sizes, or outcomes that are not evidenced.\n"
            "- Metrics: only reuse numbers already present in the resume. Never invent a number. If impact would "
            "benefit from a metric that is absent, explain that in summary instead of inventing one.\n"
            "- Do not add a technology unless it appears in the evidence facts.\n"
            "- supporting_fact_ids must come from evidence_facts. Explain each change in reason.\n"
            "- Tailor to the target role and job requirements, not generic wording. Align skill names and ordering "
            "with the job description when the candidate already demonstrates them; put JD-required skills first. "
            "Prune clearly irrelevant skills by replacing them with relevant evidenced skills; never invent a new "
            "technology. Mention skills to add (missing JD skills) and skills to remove (irrelevant) in summary.\n"
            "- For skills items, never append parenthetical explanations or alternate labels (for example "
            "'vLLM (LLM management)' is invalid). Use the canonical skill name only, or leave the item unchanged.\n"
            "- If a job requirement is missing from the resume, do not add it; mention it as a gap in summary.\n"
            "- For experience bullets, strengthen verbs and incorporate JD keywords only where supported by evidence.\n"
            "- Rewrite the professional summary to target the role using only evidenced skills and experience.\n"
            "- Improve education/certifications clarity without fabricating details.\n"
            "- Avoid keyword stuffing and do not copy sentences from the job description.\n"
            "- Keep bullets under 320 characters and preserve meaning.\n"
            "Return 4-10 changes across the selected sections."
        )
        return await llm.structured(
            system=SYSTEM_GUARD,
            prompt=prompt,
            schema=SectionEnhancementResult,
            temperature=0.3,
            max_tokens=3500,
        )

    def _heuristic_enhance(
        self,
        content: dict[str, Any],
        facts: list[dict[str, Any]],
        sections: list[str],
        user_request: str,
    ) -> SectionEnhancementResult:
        changes: list[ProposedChange] = []
        fact_by_path = {f.get("path"): f for f in facts}
        for section in sections:
            paths: list[str] = []
            if section in {"experience", "projects"}:
                for index, item in enumerate(content.get(section, []) or []):
                    for bullet_index, _ in enumerate(item.get("bullets", []) or []):
                        paths.append(f"{section}.{index}.bullets.{bullet_index}")
            elif section == "professional_summary":
                paths.append("professional_summary")
            elif section == "skills":
                for group_index, group in enumerate(content.get("skills", []) or []):
                    for item_index, _ in enumerate(group.get("items", []) or []):
                        paths.append(f"skills.{group_index}.items.{item_index}")
            elif section in {"certifications", "languages", "education"}:
                continue
            for path in paths[:8]:
                before = get_path(content, path)
                if not isinstance(before, str):
                    continue
                after = self._rewrite_sentence(before)
                if after == before:
                    continue
                fact = fact_by_path.get(path)
                changes.append(
                    ProposedChange(
                        field_path=path,
                        before=before,
                        after=after,
                        reason="Replaced weak phrasing with a stronger, more direct action verb while preserving the original facts.",
                        supporting_fact_ids=[fact["fact_id"]] if fact else [],
                        requires_user_confirmation=False,
                        section=section,
                        agent=self.name,
                    )
                )
        summary = (
            f"Proposed {len(changes)} conservative wording improvements. No new employers, tools, dates or metrics "
            "were introduced. Add measurable outcomes only if you can supply the real numbers."
        )
        selected_text = resume_text({section: content.get(section) for section in sections})
        warnings = (
            ["No measurable outcomes were found in the selected sections. Provide real metrics if you want quantified impact."]
            if changes and not extract_metrics(selected_text)
            else []
        )
        return SectionEnhancementResult(
            section=", ".join(sections),
            proposed_changes=changes,
            consistency_warnings=warnings,
            summary=summary,
        )

    @staticmethod
    def _rewrite_sentence(text: str) -> str:
        result = text.strip()
        lowered = result.lower()
        for weak, strong in WEAK_VERBS.items():
            if lowered.startswith(weak + " "):
                result = strong + result[len(weak):]
                break
        result = re.sub(r"\s+", " ", result)
        if result and result[0].islower():
            result = result[0].upper() + result[1:]
        return result

    def _validate(
        self,
        *,
        content: dict[str, Any],
        facts: list[dict[str, Any]],
        sections: list[str],
        enhancement: SectionEnhancementResult,
        user_request: str,
    ) -> tuple[SectionEnhancementResult, ValidationResult, dict[str, Any]]:
        errors: list[str] = []
        warnings: list[str] = list(enhancement.consistency_warnings)
        dropped: list[str] = []
        unsupported: list[str] = list(enhancement.unsupported_claims)
        valid_facts = {f["fact_id"]: f for f in facts}
        resume_all = resume_text(content)
        resume_skills = set(extract_skills(resume_all))
        existing_texts = {normalize_text(value) for value in flatten(content).values()}

        kept: list[ProposedChange] = []
        for index, change in enumerate(enhancement.proposed_changes[:12]):
            change_id = change.id or _change_id(change.field_path, change.after, index)
            section = section_for_path(change.field_path)
            notes = list(change.validation_notes)

            if section not in sections:
                errors.append(f"Dropped change outside requested sections: {change.field_path}")
                dropped.append(change_id)
                continue
            if section == "personal_information":
                if not (
                    change.field_path == "personal_information.full_name"
                    and re.search(r"\b(name|rename)\b", user_request or "", re.I)
                ):
                    errors.append("Protected personal information cannot be modified automatically.")
                    dropped.append(change_id)
                    continue
            try:
                actual = get_path(content, change.field_path)
            except KeyError:
                errors.append(f"Dropped invalid field path: {change.field_path}")
                dropped.append(change_id)
                continue
            if not isinstance(actual, str):
                errors.append(f"Dropped non-text target: {change.field_path}")
                dropped.append(change_id)
                continue
            before = change.before
            if not before:
                before = actual
                notes.append("Missing 'before' value was filled from the active resume.")
            if normalize_text(before) != normalize_text(actual):
                errors.append(f"Dropped change whose base text no longer matches: {change.field_path}")
                dropped.append(change_id)
                continue
            after = re.sub(r"\s+", " ", change.after or "").strip()
            if not after or normalize_text(after) == normalize_text(actual):
                errors.append(f"Dropped empty/no-op change: {change.field_path}")
                dropped.append(change_id)
                continue
            limit = 2200 if section == "professional_summary" else 400
            if len(after) > limit:
                after = after[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "."
                notes.append(f"Trimmed to {limit} characters.")
            if normalize_text(after) in existing_texts and normalize_text(after) != normalize_text(actual):
                warnings.append(f"Proposed text duplicates another resume field: {change.field_path}")

            # Numeric claim validation: only allow numbers already in the resume.
            new_metrics = extract_metrics(after) - extract_metrics(actual) - extract_metrics(resume_all)
            status = "proposed"
            requires_confirmation = False
            if new_metrics:
                requires_confirmation = True
                status = "blocked"
                claim = f"Unsupported metric(s) {sorted(new_metrics)} in {change.field_path}"
                unsupported.append(claim)
                notes.append("Blocked: metric is absent from the resume and evidence. Ask the user for the real value.")

            # Skill validation: a newly added technology must already be evidenced elsewhere.
            new_skills = set(extract_skills(after)) - set(extract_skills(actual)) - resume_skills
            if new_skills:
                requires_confirmation = True
                status = "blocked"
                claim = f"Unsupported skill(s) {sorted(new_skills)} in {change.field_path}"
                unsupported.append(claim)
                notes.append("Blocked: technology is not evidenced anywhere in the resume.")

            fact_ids = [fid for fid in change.supporting_fact_ids if fid in valid_facts]
            if not fact_ids:
                base_fact = next((f for f in facts if f.get("path") == change.field_path), None)
                if base_fact:
                    fact_ids = [base_fact["fact_id"]]
            if change.supporting_fact_ids and len(fact_ids) < len(change.supporting_fact_ids):
                warnings.append(f"Removed unknown evidence IDs from {change.field_path}.")
            if not fact_ids:
                warnings.append(f"No evidence ID attached to {change.field_path}; base text itself is the evidence.")

            kept.append(
                ProposedChange(
                    id=change_id,
                    field_path=change.field_path,
                    before=actual,
                    after=after,
                    reason=(change.reason or "Improved clarity and impact.")[:600],
                    supporting_fact_ids=fact_ids[:6],
                    requires_user_confirmation=requires_confirmation,
                    agent=enhancement.proposed_changes and (change.agent or self.name) or self.name,
                    section=section,
                    status=status,
                    validation_notes=notes,
                )
            )

        clarification = None
        applied = [c for c in kept if c.status == "proposed"]
        if unsupported:
            clarification = (
                "Some proposed changes were blocked because they introduced metrics or technologies that are not "
                "supported by your resume. Share the real numbers/tools you want to include, or remove them, and I "
                "can regenerate the proposal."
            )
        metric_request = bool(re.search(r"(quantif|metric|number|measur|impact|result)", user_request, re.I))
        if metric_request and not extract_metrics(resume_text(content, include_personal=False)):
            clarification = (
                clarification
                or "Your resume currently has no measurable outcomes. What real metrics (for example latency, "
                "revenue, adoption or team size) can you verify? I will not invent them."
            )
            warnings.append("No quantified achievements exist in the resume; metrics were not invented.")
        validation = ValidationResult(
            valid=bool(applied),
            errors=errors[:20],
            warnings=list(dict.fromkeys(warnings))[:20],
            dropped_change_ids=dropped,
            unsupported_claims=list(dict.fromkeys(unsupported))[:15],
            requires_confirmation=bool(unsupported),
            clarification_question=clarification,
        )
        enhancement.proposed_changes = kept
        enhancement.consistency_warnings = validation.warnings
        enhancement.unsupported_claims = validation.unsupported_claims
        enhancement.summary = enhancement.summary or f"Proposed {len(applied)} changes."
        meta = {
            "changes_received": len(enhancement.proposed_changes),
            "changes_valid": len(applied),
            "changes_blocked": len([c for c in kept if c.status == "blocked"]),
        }
        return enhancement, validation, meta


def build_candidate(content: dict[str, Any], changes: list[ProposedChange]) -> dict[str, Any]:
    applicable = [c.model_dump() for c in changes if c.status == "proposed"]
    return apply_changes(content, applicable)


section_enhancement_agent = SectionEnhancementAgent()
