from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from ..llm import LLMResult, SYSTEM_GUARD, llm
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


# Common LLM "improvements" that change no meaning. Canonicalising them lets validation drop
# synonym swaps that would otherwise look like real tailoring.
_SYNONYM_MAP = {
    "engineered": "built", "developed": "built", "created": "built", "designed": "built",
    "constructed": "built", "implemented": "built", "producing": "produce", "produces": "produce",
    "produced": "produce", "generating": "produce", "generates": "produce", "generated": "produce",
    "comprehensive": "complete", "detailed": "complete", "thorough": "complete",
    "utilizing": "using", "utilized": "using", "leveraging": "using", "leveraged": "using",
    "enhancing": "improve", "enhanced": "improve", "improving": "improve",
    "including": "include", "includes": "include", "facilitating": "support", "enabling": "support",
    "ensuring": "ensure", "ensured": "ensure", "robust": "reliable", "scalable": "scale",
}
_CREDENTIAL_TERMS = (
    "phd", "doctorate", "master", "bachelor", "b.tech", "m.tech", "b.e.", "m.e.",
    "bsc", "msc", "b.sc", "m.sc", "mba", "pg certification", "postgraduate", "post-graduate",
    "diploma", "associate degree", "certificate", "certification",
)


def _content_signature(text: str) -> tuple[str, ...]:
    words = re.findall(r"[a-z0-9+#.%-]+", (text or "").lower())
    return tuple(_SYNONYM_MAP.get(word, word) for word in words if word)


def _looks_like_skill(value: str) -> bool:
    """A skills item is a short label ("Django", "REST APIs"), not a requirement sentence.

    Researched and pasted job descriptions phrase requirements as sentences such as
    "Experience shipping production code in Python."; those must stay requirements and
    never become a bullet in the candidate's skills list.
    """
    text = (value or "").strip()
    if not text or len(text) > 60:
        return False
    if text.endswith(".") or len(text.split()) > 4:
        return False
    if text.split()[-1].lower() in {
        "skill", "skills", "architectures", "solutions", "applications", "systems",
        "processes", "practices", "experience", "knowledge", "abilities", "stakeholders",
    }:
        return False
    if re.match(
        r"(?i)^(experience|proven|strong|ability|comfortable|familiar|knowledge|hands[- ]on|solid|excellent|working|deep)\b",
        text,
    ):
        return False
    return True


def _is_cosmetic_swap(before: str, after: str) -> bool:
    """True when the only difference is synonym/filler wording, not content."""
    return _content_signature(before) == _content_signature(after)


def _invented_credential(before: str, after: str) -> str | None:
    """Return a credential term added by the change that the original text does not contain."""
    low_before = (before or "").lower()
    low_after = (after or "").lower()
    for term in _CREDENTIAL_TERMS:
        if term in low_after and term not in low_before:
            return term
    return None


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
        section_requirements: dict[str, list[str]] | None = None,
        missing_skills: list[str] | None = None,
        research_recommendations: list[str] | None = None,
    ) -> tuple[SectionEnhancementResult, ValidationResult, dict[str, Any]]:
        sections = normalize_sections(target_sections, content)
        # The match result's skill ledger is the single source of truth for skills the job asks
        # for. Feed it into the per-section requirements even if the section grouping is absent.
        section_requirements = dict(section_requirements or {})
        if missing_skills:
            section_requirements["skills"] = list(
                dict.fromkeys(
                    [*(section_requirements.get("skills") or []), *(f"Required skill: {s}" for s in missing_skills)]
                )
            )
        # A JD-tailoring request implies skills reconciliation: routing sometimes omits the
        # skills section, so fall back to it whenever the request is about the job/role.
        tailoring_request = bool(
            re.search(r"\b(job|jd|role|tailor|match|fit|ats|requirement|qualification|hire|fde)\b", user_request, re.I)
        )
        if (            (jd_requirements or missing_skills) and content.get("skills")
            and "skills" not in sections
            and tailoring_request
            and "personal_information" not in sections
        ):
            sections.append("skills")
        meta: dict[str, Any] = {"mode": "llm", "provider": "openai", "sections": sections}
        if not sections:
            validation = ValidationResult(valid=False, errors=["No editable section was identified."])
            return SectionEnhancementResult(summary="No editable content was found."), validation, meta

        # Agentic guard: if a job description is supplied and none of its requirements are
        # demonstrated, do not propose cosmetic rewrites that would misrepresent the resume.
        if jd_requirements:
            demonstrated = [
                r for r in jd_requirements
                if isinstance(r, dict) and r.get("status") == "demonstrated"
            ]
            if not demonstrated:
                validation = ValidationResult(
                    valid=False,
                    errors=["No job requirement is demonstrated by the resume."],
                    clarification_question=(
                        "The job description's requirements are not evidenced in your resume, so no tailoring "
                        "changes were proposed. Add real experience or choose a closer job description."
                    ),
                )
                meta.update({"mode": "guard", "changes_valid": 0, "changes_blocked": 0})
                return (
                    SectionEnhancementResult(
                        summary="No tailoring changes: job requirements are not evidenced in the resume.",
                        proposed_changes=[],
                    ),
                    validation,
                    meta,
                )

        if llm.enabled:
            result = await self._llm_enhance(
                content=content,
                facts=facts,
                sections=sections,
                user_request=user_request,
                target_role=target_role,
                jd_requirements=jd_requirements or [],
                section_requirements=section_requirements or {},
                research_recommendations=research_recommendations or [],
            )
            if result.ok:
                enhancement: SectionEnhancementResult = result.data
                meta.update({"mode": "llm", "provider": "openai"})
            else:
                enhancement = self._heuristic_enhance(content, facts, sections, user_request, jd_requirements=jd_requirements or [])
                meta.update({"mode": "heuristic_fallback", "provider": "local", "error": result.error})
        else:
            enhancement = self._heuristic_enhance(content, facts, sections, user_request, jd_requirements=jd_requirements or [])
            meta.update({"mode": "heuristic", "provider": "local"})

        # Deterministic JD-to-skills reconciliation: regardless of the LLM, compare the job's
        # requested skills against the skills section and propose concrete additions. This is
        # what makes "update my skills for this JD" actually change the skills section.
        if (jd_requirements or section_requirements.get("skills")) and "skills" in sections:
            skill_changes = self._skill_additions(
                content,
                section_requirements,
                jd_requirements or [],
            )
            if skill_changes:
                merged = list(enhancement.proposed_changes)
                for change in skill_changes:
                    if change.operation == "add" and normalize_text(change.after) in {
                        normalize_text(c.after)
                        for c in merged
                        if c.operation == "add" and c.section == "skills"
                    }:
                        continue
                    merged.append(change)
                enhancement.proposed_changes = merged
                meta["skill_additions"] = len([c for c in skill_changes if c.operation == "add"])

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
        section_requirements: dict[str, list[str]],
        research_recommendations: list[str],
    ) -> Any:
        """One focused comparison pass per section, merged into a single result.

        Each section gets its own prompt containing only that section's resume content and the JD
        requirements mapped to it, so the model compares section-by-section instead of making one
        shallow pass over the whole resume.
        """
        if len(sections) <= 1:
            return await self._llm_enhance_section(
                content=content,
                facts=facts,
                sections=sections,
                user_request=user_request,
                target_role=target_role,
                jd_requirements=jd_requirements,
                section_requirements=section_requirements,
                research_recommendations=research_recommendations,
            )

        async def run_for(section: str) -> Any:
            return await self._llm_enhance_section(
                content=content,
                facts=facts,
                sections=[section],
                user_request=user_request,
                target_role=target_role,
                jd_requirements=jd_requirements,
                section_requirements={
                    section: [r for r in (section_requirements.get(section) or [])][:8]
                },
                research_recommendations=research_recommendations,
            )

        outcomes = await asyncio.gather(*(run_for(section) for section in sections), return_exceptions=True)
        merged = SectionEnhancementResult(section=", ".join(sections))
        summaries: list[str] = []
        errors: list[str] = []
        seen: set[tuple[str, str]] = set()
        succeeded = 0
        for section, outcome in zip(sections, outcomes):
            if isinstance(outcome, BaseException):
                errors.append(f"{section}: {outcome}")
                continue
            if not outcome.ok:
                errors.append(f"{section}: {outcome.error or 'no result'}")
                continue
            succeeded += 1
            part = outcome.data
            summaries.append(f"[{section}] {part.summary}".strip())
            merged.consistency_warnings.extend(part.consistency_warnings or [])
            for change in part.proposed_changes:
                key = (change.field_path, change.after)
                if key in seen:
                    continue
                seen.add(key)
                merged.proposed_changes.append(change)
        merged.summary = " ".join(s for s in summaries if s)
        if succeeded == 0:
            return LLMResult(None, "; ".join(errors) or "LLM section enhancement failed")
        return LLMResult(merged)

    async def _llm_enhance_section(
        self,
        *,
        content: dict[str, Any],
        facts: list[dict[str, Any]],
        sections: list[str],
        user_request: str,
        target_role: str | None,
        jd_requirements: list[dict[str, Any]],
        section_requirements: dict[str, list[str]],
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
        section_req_text = "\n".join(
            f"[{section}]\n" + "\n".join(f"  - {req[:180]}" for req in reqs[:6])
            for section, reqs in (section_requirements or {}).items()
            if reqs
        )
        recommendations = "\n".join(f"- {item}" for item in research_recommendations[:8])
        prompt = (
            f"Compare and improve ONLY these resume sections: {', '.join(sections)}\n"
            f"User request: {user_request}\n"
            f"Target role: {target_role or 'not specified'}\n\n"
            f"<selected_sections_json>\n{summarize_resume_for_prompt(selected, max_chars=9000)}\n</selected_sections_json>\n\n"
            f"<evidence_facts>\n{fact_text}\n</evidence_facts>\n\n"
            f"<job_requirements>\n{requirements or '(none)'}\n</job_requirements>\n\n"
            f"<section_requirements>\n{section_req_text or '(none)'}\n</section_requirements>\n\n"
            f"<company_research_recommendations>\n{recommendations or '(none)'}\n</company_research_recommendations>\n\n"
            "Rules:\n"
            "- Use replace/remove for existing text and operation='add' only to append a skill to a skills "
            "items list. field_path must be a dot path with numeric indices, e.g. experience.0.bullets.1 or "
            "skills.0.items. before must be copied VERBATIM from the section JSON (empty for add).\n"
            "- Do not change any section outside the task's section list.\n"
            "- If the user asks to change a specific field (for example their name), make exactly that change and "
            "leave other fields untouched.\n"
            "- Do not add employers, dates, degrees, tools, responsibilities, team sizes, or outcomes that are "
            "not evidenced.\n"
            "- Metrics: only reuse numbers already present in the resume. Never invent a number. If impact would "
            "benefit from a metric that is absent, explain that in summary instead of inventing one.\n"
            "- Do not add a technology unless it appears in the evidence facts.\n"
            "- supporting_fact_ids must come from evidence_facts. Explain each change in reason.\n"
            "- Compare this section's evidence to the JD requirements that map to it and edit only where alignment "
            "genuinely improves. Reflect the job's terminology only where the evidence already supports it.\n"
            "- Tailor to the target role, not generic wording. For skills: put JD-required skills the candidate "
            "demonstrates at the front, and emit operation='add' changes for JD-required skills that are evidenced "
            "elsewhere in the resume but missing from the skills list. One add change per skill, never bundled.\n"
            "- Do NOT rename, expand, or rephrase skill items into longer/different names (e.g. do not turn "
            "'Conversational Voice AI' into 'Conversational Voice AI Development'). Keep the canonical skill name "
            "unchanged; only reorder items within a group to put JD-required (demonstrated) skills first.\n"
            "- If most job requirements are not demonstrated by the resume, return an EMPTY proposed_changes list "
            "and explain the gaps in summary instead of cosmetic rewrites.\n"
            "- For skills items, never append parenthetical explanations or alternate labels (for example "
            "'vLLM (LLM management)' is invalid). Use the canonical skill name only, or leave the item unchanged.\n"
            "- If a job requirement is missing from the resume, do not add it; mention it as a gap in summary.\n"
            "- For experience bullets, incorporate JD keywords only where supported by evidence; do not just swap "
            "verbs or add filler words.\n"
            "- Rewrite the professional summary to target the role using only evidenced skills and experience.\n"
            "- Do not propose a change whose only effect is renaming or cosmetic rewording; omit it instead.\n"
            "- Avoid keyword stuffing and do not copy sentences from the job description.\n"
            "- Keep bullets under 320 characters and preserve meaning.\n"
            "- To add a missing skill to the skills section, emit a change with operation='add', "
            "field_path='skills.<group_index>.items', before='', after='<skill>'. Only add skills that are "
            "evidenced in the resume, or that the user explicitly said they have. To remove an irrelevant "
            "skill, use operation='remove' with the item's field_path and before set to the item.\n"
            "- When the request concerns skills, produce skills changes (add/reorder/remove) and do not "
            "rewrite education or other unrelated sections.\n"
            "- Never propose a change whose only effect is a synonym swap (e.g. 'including' -> 'such as', "
            "'Created' -> 'Developed'). If a section has nothing JD-relevant to change, leave it empty.\n"
            "Return 0-8 changes across the selected sections. Fewer, evidence-backed changes are better than "
            "cosmetic ones."
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
        jd_requirements: list[dict[str, Any]] | None = None,
    ) -> SectionEnhancementResult:
        jd_requirements = jd_requirements or []
        # When a job description is present, the deterministic heuristic cannot do real tailoring;
        # avoid emitting cosmetic verb swaps. Surface the analysis/gaps instead.
        if jd_requirements:
            return SectionEnhancementResult(
                section=", ".join(sections),
                proposed_changes=[],
                consistency_warnings=[],
                summary=(
                    "No LLM is configured, so no evidence-backed tailoring changes were generated. "
                    "Review the job alignment and gaps above; configure an LLM for targeted rewrites."
                ),
            )
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

    def _skill_additions(
        self,
        content: dict[str, Any],
        section_requirements: dict[str, list[str]],
        jd_requirements: list[dict[str, Any]],
    ) -> list[ProposedChange]:
        """Compare JD-requested skills with the skills section and propose additions.

        Skills already evidenced elsewhere in the resume are added as ready-to-accept
        changes. Skills that are absent everywhere are still proposed, but flagged so the
        user must confirm they genuinely have them before accepting.
        """
        groups = content.get("skills") or []
        group_items: list[list[str]] = []
        for group in groups:
            items = group.get("items") if isinstance(group, dict) else None
            group_items.append([i for i in (items or []) if isinstance(i, str)])
        if not group_items:
            return []

        listed = {
            normalize_text(item)
            for items in group_items
            for item in items
        }
        listed_skills = set(extract_skills("\n".join(" | ".join(items) for items in group_items)))

        requirement_text = "\n".join((section_requirements or {}).get("skills", []) or [])
        if not requirement_text:
            requirement_text = "\n".join(
                str(r.get("requirement", ""))
                for r in jd_requirements
                if isinstance(r, dict)
            )
        jd_skills = extract_skills(requirement_text)
        if not jd_skills:
            return []

        # Evidence is everything except the skills list itself; a skill already listed in
        # the skills section is a user assertion and is treated as supported.
        resume_without_skills = {k: v for k, v in content.items() if k != "skills"}
        evidence = set(extract_skills(resume_text(resume_without_skills))) | listed_skills

        # Pick the group that best matches the job skills; fall back to the group that
        # already contains Python, then to the first group.
        best_index = 0
        best_overlap = -1
        for index, items in enumerate(group_items):
            overlap = len(set(extract_skills(" | ".join(items))) & set(jd_skills))
            if overlap > best_overlap:
                best_overlap = overlap
                best_index = index
        if best_overlap <= 0:
            for index, items in enumerate(group_items):
                if "Python" in extract_skills(" | ".join(items)):
                    best_index = index
                    break

        additions: list[ProposedChange] = []
        for skill in jd_skills:
            canonical = normalize_text(skill)
            if canonical in listed or skill in listed_skills:
                continue
            supported = skill in evidence
            additions.append(
                ProposedChange(
                    field_path=f"skills.{best_index}.items",
                    before="",
                    after=skill,
                    operation="add",
                    reason=(
                        f"Job asks for {skill}"
                        + (" and your experience already evidences it; added to your skills."
                           if supported else
                           ". Confirm you actually have it before accepting; it is not currently evidenced in your resume.")
                    ),
                    supporting_fact_ids=[],
                    requires_user_confirmation=not supported,
                    section="skills",
                    status="proposed",
                    validation_notes=[
                        "Evidenced elsewhere in your resume." if supported
                        else "Not evidenced in your resume; accept only if you genuinely have this skill."
                    ],
                )
            )
            if len(additions) >= 8:
                break
        return additions

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
        pending_additions: set[tuple[str, str]] = set()
        # Skills additions are appended last, so sort them first before the review-size cap.
        ordered = sorted(enhancement.proposed_changes, key=lambda c: 0 if c.operation == "add" else 1)
        for index, change in enumerate(ordered[:12]):
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
            operation = (change.operation or "replace").lower()
            if operation not in {"replace", "add", "remove"}:
                errors.append(f"Dropped change with unknown operation: {change.field_path}")
                dropped.append(change_id)
                continue
            if operation == "replace":
                # A model that targets a list field (e.g. "skills.0.items") means "append";
                # treating it as a replace would silently drop the new skill.
                try:
                    probe = get_path(content, change.field_path)
                except KeyError:
                    probe = None
                if isinstance(probe, list):
                    operation = "add"

            status = "proposed"
            requires_confirmation = False
            actual = ""
            if operation == "remove":
                if not re.search(r"\b(remove|delete|drop|trim|cut|prune)\b", user_request, re.I):
                    errors.append(f"Ignored unrequested removal: {change.field_path}")
                    dropped.append(change_id)
                    continue
                try:
                    target_list = get_path(content, change.field_path)
                except KeyError:
                    errors.append(f"Dropped invalid list path: {change.field_path}")
                    dropped.append(change_id)
                    continue
                if not isinstance(target_list, list):
                    errors.append(f"Dropped remove from a non-list field: {change.field_path}")
                    dropped.append(change_id)
                    continue
                wanted = normalize_text(change.before or change.after)
                match_index = next(
                    (
                        i for i, item in enumerate(target_list)
                        if isinstance(item, str) and normalize_text(item) == wanted
                    ),
                    None,
                )
                if not wanted or match_index is None:
                    errors.append(f"Dropped removal of an item that is not in the list: {change.field_path}")
                    dropped.append(change_id)
                    continue
                actual = target_list[match_index]
                change.field_path = f"{change.field_path}.{match_index}"
                change.before = actual
            elif operation == "replace":
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
            else:
                try:
                    target_list = get_path(content, change.field_path)
                except KeyError:
                    errors.append(f"Dropped invalid list path: {change.field_path}")
                    dropped.append(change_id)
                    continue
                if not isinstance(target_list, list):
                    errors.append(f"Dropped add to a non-list field: {change.field_path}")
                    dropped.append(change_id)
                    continue
                candidate_item = re.sub(r"\s+", " ", change.after or "").strip()
                if not candidate_item:
                    errors.append(f"Dropped empty addition: {change.field_path}")
                    dropped.append(change_id)
                    continue
                if section == "skills" and not _looks_like_skill(candidate_item):
                    errors.append(
                        f"Dropped skills addition that is a requirement sentence, not a skill: {change.field_path}"
                    )
                    dropped.append(change_id)
                    continue
                addition_key = (normalize_text(change.field_path), normalize_text(candidate_item))
                if addition_key in pending_additions or any(
                    isinstance(item, str) and normalize_text(item) == normalize_text(candidate_item)
                    for item in target_list
                ):
                    errors.append(f"Dropped duplicate addition: {change.field_path}")
                    dropped.append(change_id)
                    continue
                pending_additions.add(addition_key)
                # A model that both rewrites a bullet and "adds" the same text would duplicate
                # resume content; the rewrite already carries it.
                if any(
                    other is not change
                    and (other.operation or "replace") != "add"
                    and normalize_text(other.after or "") == normalize_text(candidate_item)
                    for other in enhancement.proposed_changes
                ):
                    errors.append(
                        f"Dropped addition that duplicates a rewritten field: {change.field_path}"
                    )
                    dropped.append(change_id)
                    continue
                if section in {"experience", "projects", "education"}:
                    # A brand-new bullet is a new claim about the candidate's history.
                    requires_confirmation = True
                    notes.append(
                        "This inserts a new entry rather than rewording an existing one; "
                        "confirm it is real before accepting."
                    )

            after = "" if operation == "remove" else re.sub(r"\s+", " ", change.after or "").strip()
            if operation != "remove":
                if not after or (operation == "replace" and normalize_text(after) == normalize_text(actual)):
                    errors.append(f"Dropped empty/no-op change: {change.field_path}")
                    dropped.append(change_id)
                    continue
                if operation == "replace" and _is_cosmetic_swap(actual, after):
                    errors.append(
                        f"Dropped synonym-only rewording with no job-relevant content: {change.field_path}"
                    )
                    dropped.append(change_id)
                    continue
                if operation == "replace" and (
                    section == "education" or change.field_path.endswith(".degree")
                ):
                    invented = _invented_credential(actual, after)
                    if invented:
                        errors.append(
                            f"Dropped education change that adds the credential '{invented}': "
                            f"{change.field_path}. Confirm the real qualification instead."
                        )
                        dropped.append(change_id)
                        continue
                limit = 2200 if section == "professional_summary" else 400
                if len(after) > limit:
                    after = after[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "."
                    notes.append(f"Trimmed to {limit} characters.")
                if normalize_text(after) in existing_texts and normalize_text(after) != normalize_text(actual):
                    warnings.append(f"Proposed text duplicates another resume field: {change.field_path}")

                # Numeric claim validation: only allow numbers already in the resume. A bare skill
                # name such as "GPT-4" is not a metric claim, so skip skill-list additions.
                new_metrics = (
                    set()
                    if operation == "add" and section == "skills"
                    else extract_metrics(after) - extract_metrics(actual) - extract_metrics(resume_all)
                )
                if new_metrics:
                    requires_confirmation = True
                    status = "blocked"
                    claim = f"Unsupported metric(s) {sorted(new_metrics)} in {change.field_path}"
                    unsupported.append(claim)
                    notes.append("Blocked: metric is absent from the resume and evidence. Ask the user for the real value.")

                # Skill validation. For a new skills item the user is the authority: propose it
                # but require explicit confirmation when it is not evidenced elsewhere. For an
                # inline skill mention inside other text, keep the strict block.
                if operation == "add" and section == "skills":
                    if set(extract_skills(after)) - resume_skills:
                        requires_confirmation = True
                        notes.append(
                            "Not evidenced elsewhere in your resume. Accept only if you genuinely have this skill."
                        )
                else:
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
                    operation=operation,
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
        # Keep the review manageable and make sure JD skill additions are never pushed out.
        additions = [c for c in kept if c.operation == "add"]
        remaining = [c for c in kept if c.operation != "add"]
        enhancement.proposed_changes = (additions + remaining)[:12]
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
