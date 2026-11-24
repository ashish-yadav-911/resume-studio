from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..llm import SYSTEM_GUARD, llm
from ..resume_service import extract_metrics, resume_text
from ..schemas import JobMatchResult, ParsedJobDescription, Requirement, ScoreBreakdown
from ..vector_store import VectorIndex
from .common import extract_skills, quote_in_resume, skill_status, summarize_resume_for_prompt

WEIGHTS = {
    "required_technical_skills": 0.35,
    "responsibilities_alignment": 0.25,
    "relevant_experience": 0.20,
    "preferred_skills": 0.10,
    "demonstrated_impact": 0.10,
}

CATEGORY_LABELS = {
    "required_technical_skills": "Required technical skills",
    "responsibilities_alignment": "Responsibility alignment",
    "relevant_experience": "Relevant experience",
    "preferred_skills": "Preferred skills",
    "demonstrated_impact": "Demonstrated impact",
}

EXPERIENCE_WORDS = re.compile(r"\b(years?|experience|track record|background in)\b", re.I)
DOMAIN_WORDS = re.compile(r"\b(industry|domain|fintech|healthcare|e-?commerce|saas|marketplace|regulated)\b", re.I)
IMPACT_WORDS = re.compile(r"\b(impact|revenue|growth|retention|conversion|latency|scale|cost|efficien\w*)\b", re.I)
RESPONSIBILITY_STARTS = re.compile(
    r"^(build|design|develop|own|lead|drive|deliver|collaborate|partner|implement|maintain|scale|"
    r"manage|create|improve|support|define|architect|ship|operate|mentor|translate|analyze|analyse)\b",
    re.I,
)


TITLE_ROLE_WORDS = re.compile(
    r"\b(engineer|developer|scientist|manager|analyst|designer|architect|lead|director|"
    r"consultant|specialist|administrator|researcher|intern)\b",
    re.I,
)
HEADER_LINE = re.compile(
    r"^(company|organization|organisation|employer|location|department|team|position|job title|"
    r"title|role|employment type|salary|compensation|posted|job id|req(?:uisition)?(?: id)?)\s*[:\-]",
    re.I,
)
META_LINE = re.compile(
    r"^(remote|hybrid|on-?site|full[- ]time|part[- ]time|contract(?:or)?|temporary|internship)\b",
    re.I,
)


def parse_jd_heuristic(text: str) -> ParsedJobDescription:
    lines = [re.sub(r"\s+", " ", line).strip(" -•*\t") for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    parsed = ParsedJobDescription()
    if not lines:
        return parsed
    parsed.title = lines[0][:180]
    # "Senior AI Engineer at Lumen Systems" / "Lumen Systems — Senior AI Engineer"
    title_company = re.search(r"\b(?:at|@)\s+(.+)$", parsed.title, re.I)
    if title_company:
        candidate = re.split(r"\s*[|—–]\s*", title_company.group(1))[0].strip(" .,!?:;")
        if 1 < len(candidate) <= 80:
            parsed.company = candidate
    if len(lines) > 1 and re.search(r"\b(at|@)\s+\w", lines[1], re.I) and len(lines[1]) < 120:
        match = re.search(r"\b(?:at|@)\s+(.+)$", lines[1], re.I)
        if match:
            parsed.company = match.group(1).strip()[:120]

    title_lower = parsed.title.lower()
    skip_values = {title_lower}
    if parsed.company:
        skip_values.add(parsed.company.lower())

    section = "required"
    preferred_headers = {
        "preferred", "preferred qualifications", "nice to have", "nice to haves", "bonus",
        "bonus points", "desired", "desired qualifications",
    }
    required_headers = {
        "requirements", "required", "required qualifications", "qualifications", "must have",
        "must haves", "what you bring", "what we are looking for", "what we're looking for",
    }
    responsibility_headers = {
        "responsibilities", "what you'll do", "what you will do", "the role", "about the role",
        "your impact", "day to day", "day-to-day",
    }
    for line in lines[1:]:
        lower = line.lower().strip(":")
        if lower in skip_values:
            continue
        bare = lower.strip()
        if bare in preferred_headers:
            section = "preferred"
            continue
        if bare in required_headers:
            section = "required"
            continue
        if bare in responsibility_headers:
            section = "responsibilities"
            continue
        header = HEADER_LINE.match(line)
        if header:
            if header.group(1).lower() in {"company", "organization", "organisation", "employer"}:
                parsed.company = parsed.company or re.split(r"[:\-]", line, maxsplit=1)[-1].strip()[:120]
            continue
        if META_LINE.match(line):
            continue
        # A second title-like line ("AI Platform Engineer at Lumen Systems") is metadata, not a requirement.
        if (
            len(line) < 120
            and TITLE_ROLE_WORDS.search(line)
            and re.search(r"\b(?:at|@)\s+\w", line, re.I)
            and not RESPONSIBILITY_STARTS.search(line)
        ):
            continue
        if len(line) < 20 or len(line) > 500:
            continue
        if section == "responsibilities" or RESPONSIBILITY_STARTS.search(line):
            parsed.responsibilities.append(line)
        elif section == "preferred":
            parsed.preferred_qualifications.append(line)
        else:
            parsed.required_qualifications.append(line)

    parsed.technical_skills = extract_skills(text)
    years = re.search(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?years?", text, re.I)
    if years:
        parsed.years_experience = f"{years.group(1)}+ years"
    parsed.domain_requirements = [
        line for line in parsed.required_qualifications if DOMAIN_WORDS.search(line)
    ][:6]
    return parsed


def _years_from_resume(content: dict[str, Any]) -> float | None:
    current_year = datetime.now(timezone.utc).year
    total_months = 0
    found = False
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    def parse_year_month(value: str, default_month: int) -> tuple[int, int] | None:
        value = (value or "").strip().lower()
        if not value:
            return None
        if value in {"present", "current", "now"}:
            return current_year, datetime.now(timezone.utc).month
        year_match = re.search(r"(19|20)\d{2}", value)
        if not year_match:
            return None
        year = int(year_match.group(0))
        month = default_month
        for name, number in month_map.items():
            if value.startswith(name):
                month = number
                break
        numeric = re.match(r"(\d{1,2})/(\d{4})", value)
        if numeric:
            month = max(1, min(12, int(numeric.group(1))))
        return year, month

    for exp in content.get("experience", []) or []:
        start = parse_year_month(exp.get("start_date", ""), 1)
        end = parse_year_month(exp.get("end_date", ""), 12) or parse_year_month("present", 12)
        if start and end:
            found = True
            months = (end[0] - start[0]) * 12 + (end[1] - start[1])
            if months > 0:
                total_months += months
    if not found:
        return None
    return round(total_months / 12, 1)


def compute_match_score(
    requirements: list[Requirement], content: dict[str, Any], parsed: ParsedJobDescription
) -> tuple[int, ScoreBreakdown, str]:
    demonstrated = partial = 0
    for req in requirements:
        if req.status == "demonstrated":
            demonstrated += 1
        elif req.status == "partial":
            partial += 1
    buckets: dict[str, tuple[float, list[Requirement]]] = {
        "required_technical_skills": (
            WEIGHTS["required_technical_skills"],
            [r for r in requirements if r.category == "technical" and r.importance != "preferred"],
        ),
        "responsibilities_alignment": (
            WEIGHTS["responsibilities_alignment"],
            [r for r in requirements if r.category == "responsibility"],
        ),
        "relevant_experience": (
            WEIGHTS["relevant_experience"],
            [r for r in requirements if r.category in {"experience", "domain"} and r.importance != "preferred"],
        ),
        "preferred_skills": (
            WEIGHTS["preferred_skills"],
            [r for r in requirements if r.importance == "preferred" and r.category != "technical"],
        ),
        "demonstrated_impact": (
            WEIGHTS["demonstrated_impact"],
            [r for r in requirements if r.category == "impact"],
        ),
    }
    if not buckets["required_technical_skills"][1]:
        buckets["required_technical_skills"] = (
            WEIGHTS["required_technical_skills"],
            [r for r in requirements if r.category == "technical"],
        )
    if not buckets["preferred_skills"][1]:
        buckets["preferred_skills"] = (
            WEIGHTS["preferred_skills"],
            [r for r in requirements if r.importance == "preferred"],
        )

    fractions: dict[str, float] = {}
    active_weight = 0.0
    weighted_points = 0.0
    for key, (weight, items) in buckets.items():
        if items:
            fraction = sum(1.0 if r.status == "demonstrated" else 0.5 if r.status == "partial" else 0.0 for r in items) / len(items)
            fractions[key] = round(fraction * 100)
            active_weight += weight
            weighted_points += weight * fraction
        elif key == "demonstrated_impact":
            bullets = [b for exp in content.get("experience", []) or [] for b in exp.get("bullets", []) or []]
            if bullets:
                with_metrics = sum(1 for b in bullets if extract_metrics(b))
                fraction = min(1.0, with_metrics / max(1, min(5, len(bullets))))
                fractions[key] = round(fraction * 100)
                active_weight += weight
                weighted_points += weight * fraction

    score = int(round(100 * weighted_points / active_weight)) if active_weight else 0
    breakdown = ScoreBreakdown(
        required_technical_skills=fractions.get("required_technical_skills", 0),
        responsibilities_alignment=fractions.get("responsibilities_alignment", 0),
        relevant_experience=fractions.get("relevant_experience", 0),
        preferred_skills=fractions.get("preferred_skills", 0),
        demonstrated_impact=fractions.get("demonstrated_impact", 0),
    )
    total_reqs = len(requirements)
    explanation = (
        f"Heuristic alignment score {score}/100 from {total_reqs} extracted requirements "
        f"({demonstrated} demonstrated, {partial} partial). "
        + ", ".join(f"{CATEGORY_LABELS.get(k, k)} {v}%" for k, v in fractions.items())
        + ". Weights were re-normalized over the categories that had evidence-bearing requirements. "
        "This is resume-to-job-description alignment, not a probability of being hired and not a vendor ATS score."
    )
    return max(0, min(100, score)), breakdown, explanation


def fallback_requirements(content: dict[str, Any], parsed: ParsedJobDescription, jd_text: str) -> list[Requirement]:
    facts = []
    for exp in content.get("experience", []) or []:
        header = f"{exp.get('role', '')} at {exp.get('company', '')}".strip(" at")
        for bullet in exp.get("bullets", []) or []:
            facts.append(f"{header}. {bullet}".strip(". "))
    for group in content.get("skills", []) or []:
        facts.append(f"{group.get('category', 'Skills')}: {', '.join(group.get('items', []) or [])}")
    for project in content.get("projects", []) or []:
        facts.append(f"{project.get('name', '')}. {project.get('description', '')} {' '.join(project.get('bullets', []) or [])}".strip())

    candidates: list[tuple[str, str]] = []
    candidates.extend((item, "required") for item in parsed.required_qualifications)
    candidates.extend((item, "preferred") for item in parsed.preferred_qualifications)
    candidates.extend((item, "required") for item in parsed.responsibilities)
    if not candidates:
        candidates = [
            (line.strip(" -•*"), "required")
            for line in jd_text.splitlines()
            if 25 <= len(line.strip()) <= 400
        ][:12]

    requirements: list[Requirement] = []
    for text, importance in candidates[:16]:
        skills = extract_skills(text)
        category = "other"
        if skills:
            category = "technical"
        elif IMPACT_WORDS.search(text):
            category = "impact"
        elif EXPERIENCE_WORDS.search(text):
            category = "experience"
        elif DOMAIN_WORDS.search(text):
            category = "domain"
        elif RESPONSIBILITY_STARTS.search(text):
            category = "responsibility"

        evidence: list[str] = []
        if skills:
            present = [s for s in skills if skill_status(s, resume_text(content)) == "demonstrated"]
            if len(present) == len(skills):
                status = "demonstrated"
            elif present:
                status = "partial"
            else:
                status = "not_demonstrated"
            for skill in present:
                for fact in facts:
                    if skill.lower() in fact.lower():
                        evidence.append(fact[:300])
                        break
        else:
            content_words = [w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in STOPWORDS]
            index = VectorIndex()
            index.extend([(str(position), fact, None) for position, fact in enumerate(facts)])
            hits = index.search(text, top_k=3)
            candidates_for_match = [record.text for _, record in hits] or facts
            best_ratio = 0.0
            best_fact = ""
            for fact in candidates_for_match:
                fact_lower = fact.lower()
                ratio = sum(1 for w in content_words if w in fact_lower) / max(1, len(content_words))
                if ratio > best_ratio:
                    best_ratio, best_fact = ratio, fact
            if best_ratio >= 0.6:
                status = "demonstrated"
                evidence = [best_fact[:300]]
            elif best_ratio >= 0.3:
                status = "partial"
                evidence = [best_fact[:300]]
            else:
                status = "not_demonstrated"

        requirements.append(
            Requirement(
                requirement=text[:400],
                importance="preferred" if importance == "preferred" else "required",
                category=category,
                evidence=evidence,
                status=status,
                suggested_action="Emphasize directly relevant evidence." if status == "demonstrated" else "Add only if the candidate has this experience; otherwise treat as a gap.",
            )
        )
    return requirements


STOPWORDS = {
    "with", "that", "this", "from", "have", "will", "your", "you", "our", "their", "they",
    "and", "for", "the", "are", "into", "using", "work", "team", "teams", "role", "about",
    "experience", "including", "ability", "strong", "working", "across", "other", "such",
}


class JobMatchingAgent:
    name = "job_matching"

    async def parse_job_description(self, jd_text: str) -> tuple[ParsedJobDescription, dict[str, Any]]:
        heuristic = parse_jd_heuristic(jd_text)
        if not llm.enabled:
            return heuristic, {"mode": "heuristic", "provider": "local"}
        result = await llm.structured(
            system=SYSTEM_GUARD,
            prompt=(
                "Extract a structured job description. Do not add requirements that are not present. "
                "technical_skills should only contain concrete technologies/tools explicitly named.\n\n"
                f"<job_description>\n{jd_text[:20000]}\n</job_description>"
            ),
            schema=ParsedJobDescription,
            temperature=0.0,
        )
        if not result.ok:
            return heuristic, {"mode": "heuristic_fallback", "error": result.error}
        parsed: ParsedJobDescription = result.data
        # Application-level safety: never accept skills that are not literally in the JD.
        jd_skills = set(extract_skills(jd_text))
        parsed.technical_skills = [s for s in parsed.technical_skills if s in jd_skills]
        if not parsed.technical_skills:
            parsed.technical_skills = list(jd_skills)
        if not parsed.title:
            parsed.title = heuristic.title
        return parsed, {"mode": "llm", "provider": "openai"}

    async def analyze(
        self,
        *,
        jd_text: str,
        parsed: ParsedJobDescription,
        content: dict[str, Any],
        target_role: str | None = None,
    ) -> tuple[JobMatchResult, dict[str, Any]]:
        resume_json = summarize_resume_for_prompt(content)
        prompt = (
            "Compare this structured resume against the job description requirements. "
            "For each requirement copy a short evidence quote VERBATIM from the resume JSON. "
            "Use status 'demonstrated' only when the quote directly proves the requirement; 'partial' when "
            "related evidence exists; otherwise 'not_demonstrated'. Never infer unstated skills. "
            "Categories must be one of: technical, responsibility, experience, domain, impact, education, other. "
            "Importance must be required, preferred or nice_to_have. The application computes the score, so set "
            "match_score to 0 and leave score_explanation empty.\n\n"
            f"Target role: {target_role or parsed.title or 'unspecified'}\n"
            f"<job_description>\n{jd_text[:12000]}\n</job_description>\n\n"
            f"<resume_json>\n{resume_json}\n</resume_json>"
        )
        result = await llm.structured(
            system=SYSTEM_GUARD, prompt=prompt, schema=JobMatchResult, temperature=0.1, max_tokens=5000
        )
        meta: dict[str, Any] = {"mode": "llm", "provider": "openai"}
        if result.ok:
            match: JobMatchResult = result.data
            requirements = match.requirements
        else:
            requirements = fallback_requirements(content, parsed, jd_text)
            match = JobMatchResult(requirements=requirements)
            meta = {
                "mode": "heuristic_fallback",
                "provider": "local",
                "error": result.error,
                "disclosure": "Requirements were matched deterministically because the LLM was unavailable.",
            }

        # --- application-level evidence validation -------------------------
        limitations: list[str] = list(match.limitations)
        validated: list[Requirement] = []
        for req in requirements[:25]:
            evidence = [q for q in req.evidence if quote_in_resume(q, content)]
            original_status = req.status
            if not evidence:
                status = "not_demonstrated"
                if original_status != "not_demonstrated":
                    limitations.append(
                        f"Downgraded '{req.requirement[:80]}' because no verbatim resume evidence supported "
                        f"the model's '{original_status}' claim."
                    )
            elif original_status == "not_demonstrated":
                status = "partial"
            else:
                status = original_status
            validated.append(
                Requirement(
                    requirement=req.requirement.strip()[:500],
                    importance=req.importance,
                    category=req.category if req.category in {
                        "technical", "responsibility", "experience", "domain", "impact", "education", "other"
                    } else "other",
                    evidence=evidence[:4],
                    status=status,
                    suggested_action=req.suggested_action,
                )
            )

        # --- deterministic skill ledger ------------------------------------
        full_resume_text = resume_text(content)
        matched_skills: list[str] = []
        missing_skills: list[str] = []
        for skill in parsed.technical_skills:
            if skill_status(skill, full_resume_text) == "demonstrated":
                matched_skills.append(skill)
            else:
                missing_skills.append(skill)
        partial_skills = sorted(
            {
                skill
                for req in validated
                if req.category == "technical" and req.status == "partial"
                for skill in extract_skills(req.requirement)
                if skill not in matched_skills and skill not in missing_skills
            }
        )

        # --- experience gap (deterministic) --------------------------------
        experience_gaps: list[str] = []
        years_match = re.search(r"(\d{1,2})", parsed.years_experience or jd_text[:3000])
        resume_years = _years_from_resume(content)
        if years_match:
            required_years = int(years_match.group(1))
            if resume_years is not None and resume_years + 0.5 < required_years:
                experience_gaps.append(
                    f"Job asks for {required_years}+ years; dated resume experience totals about {resume_years} years."
                )
            elif resume_years is None:
                limitations.append(
                    "The job specifies a years-of-experience requirement but the resume dates were not machine-readable; no gap was inferred."
                )
        for req in validated:
            if req.category in {"domain", "experience"} and req.status == "not_demonstrated":
                experience_gaps.append(req.requirement[:220])
        experience_gaps = list(dict.fromkeys(experience_gaps))[:8]

        score, breakdown, explanation = compute_match_score(validated, content, parsed)
        match.match_score = score
        match.requirements = validated
        match.matched_skills = matched_skills
        match.partial_matches = partial_skills
        match.missing_skills = missing_skills
        match.experience_gaps = experience_gaps
        match.breakdown = breakdown
        match.score_explanation = explanation
        match.limitations = list(dict.fromkeys(limitations))[:12]

        # Per-section comparison: group requirements by the resume section they most
        # naturally inform, so enhancement can tailor section-by-section instead of
        # producing generic rewrites.
        section_groups: dict[str, list[str]] = {
            "professional_summary": [],
            "skills": [],
            "experience": [],
            "projects": [],
        }
        for req in validated:
            text = req.requirement.strip()
            if not text:
                continue
            cat = (req.category or "other").lower()
            low = text.lower()
            if cat == "technical" or any(
                k in low for k in ("skill", "tool", "framework", "language", "library", "stack")
            ):
                section_groups["skills"].append(text)
            elif cat in {"experience", "domain"} or any(
                k in low for k in ("experience", "years", "project", "built", "shipped", "product")
            ):
                if "project" in low:
                    section_groups["projects"].append(text)
                else:
                    section_groups["experience"].append(text)
            else:
                section_groups["professional_summary"].append(text)
        # Make every technical skill the job asks for explicitly visible to skills reconciliation,
        # including ones mentioned only in the parsed skills list.
        if missing_skills:
            section_groups["skills"].extend(f"Required skill: {skill}" for skill in missing_skills)
        match.section_requirements = {
            k: v for k, v in section_groups.items() if v
        }
        valid_sections = {
            "professional_summary", "experience", "skills", "projects", "education",
            "certifications", "languages", "custom_sections", "personal_information",
        }
        match.sections_to_update = [s for s in match.sections_to_update if s in valid_sections][:6]
        match.recommendations = [r.strip()[:300] for r in match.recommendations if r.strip()][:8]
        return match, meta


job_matching_agent = JobMatchingAgent()
