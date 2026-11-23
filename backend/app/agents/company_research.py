from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..llm import SYSTEM_GUARD, llm
from ..research import ResearchBundle, research_service
from ..schemas import CompanyResearchResult, Source
from .common import extract_skills, summarize_resume_for_prompt


class CompanyResearchAgent:
    name = "company_research"

    async def research(
        self,
        *,
        company: str,
        role: str,
        content: dict[str, Any] | None,
        facts: list[dict[str, Any]],
        job_description: str | None = None,
    ) -> tuple[CompanyResearchResult, dict[str, Any]]:
        bundle = await research_service.research(company, role)
        meta: dict[str, Any] = {
            "provider_status": bundle.provider_status,
            "retrieved_at": bundle.retrieved_at,
            "source_count": len(bundle.sources),
            "job_count": len(bundle.job_listings),
        }
        if not llm.enabled:
            result = self._fallback(bundle, content or {}, facts, job_description)
            meta.update({"mode": "heuristic", "provider": "local"})
            return result, meta

        source_text = "\n\n".join(
            f"[source {index + 1}] title: {s.title}\nurl: {s.url}\ndomain: {s.domain}\n"
            f"kind: {s.kind}\nretrieved: {s.retrieved_at}\ncontent: {s.snippet[:2500]}"
            for index, s in enumerate(bundle.sources[:10])
        ) or "(no live sources were retrieved)"
        listing_text = "\n".join(
            f"- {job['title']} ({job.get('location', '')}) — {job['url']}" for job in bundle.job_listings[:8]
        ) or "(no published roles were found)"
        fact_text = "\n".join(f"{f['fact_id']}: {f['value'][:220]}" for f in facts[:60]) or "(no resume facts)"
        jd_section = f"\n<job_description>\n{job_description[:6000]}\n</job_description>" if job_description else "\n(no job description supplied)"

        prompt = (
            f"Company: {company}\nTarget role: {role}\n\n"
            "Using ONLY the retrieved sources, published roles and resume facts below, produce a concise, "
            "evidence-grounded research brief for tailoring this candidate's resume. "
            "Every claim must be supported by a source. Copy source URLs exactly; do not invent URLs. "
            "relevant_experience_ids must be fact IDs from the list. If evidence is thin, say so in limitations. "
            "Do not assert internal hiring preferences or company culture that is not documented.\n\n"
            f"<retrieved_sources>\n{source_text}\n</retrieved_sources>\n\n"
            f"<published_roles>\n{listing_text}\n</published_roles>\n\n"
            f"<resume_facts>\n{fact_text}\n</resume_facts>\n"
            f"{jd_section}"
        )
        result = await llm.structured(
            system=SYSTEM_GUARD, prompt=prompt, schema=CompanyResearchResult, temperature=0.2, max_tokens=3500
        )
        if not result.ok:
            fallback = self._fallback(bundle, content or {}, facts, job_description)
            meta.update({"mode": "heuristic_fallback", "provider": "local", "error": result.error})
            return fallback, meta

        brief: CompanyResearchResult = result.data
        # Application-level trust boundary: only retrieved sources may be shown.
        allowed = {s.url.rstrip("/"): s for s in bundle.sources}
        cited: list[Source] = []
        for source in brief.sources:
            key = source.url.rstrip("/")
            if key in allowed:
                actual = allowed[key]
                # Preserve the model's relevance snippet but provenance comes from retrieval.
                merged = actual.model_copy(update={"snippet": (source.snippet or actual.snippet)[:4000]})
                cited.append(merged)
        brief.sources = cited or bundle.sources[:10]
        valid_fact_ids = {f["fact_id"] for f in facts}
        brief.relevant_experience_ids = [fid for fid in brief.relevant_experience_ids if fid in valid_fact_ids][:12]
        brief.company = company
        brief.target_role = role
        brief.research_timestamp = datetime.now(timezone.utc).isoformat()
        brief.limitations = list(dict.fromkeys(list(brief.limitations) + bundle.limitations))[:10]
        if not bundle.sources:
            brief.limitations.append("No live sources were retrieved; research findings are unavailable.")
            brief.optimization_recommendations = [
                "No verified company information was retrieved. Retry later or provide official sources before tailoring."
            ]
        meta.update({"mode": "llm", "provider": "openai", "cited_sources": len(brief.sources)})
        return brief, meta

    def _fallback(
        self,
        bundle: ResearchBundle,
        content: dict[str, Any],
        facts: list[dict[str, Any]],
        job_description: str | None,
    ) -> CompanyResearchResult:
        combined_sources = "\n".join(source.snippet for source in bundle.sources)
        combined_jobs = "\n".join(
            f"{job.get('title', '')} {job.get('location', '')}" for job in bundle.job_listings
        )
        skills = extract_skills(combined_sources + "\n" + combined_jobs + "\n" + (job_description or ""))
        resume_text_all = " ".join(fact["value"] for fact in facts)
        resume_skills = set(extract_skills(resume_text_all))
        key_skills = skills[:10]
        gaps = [skill for skill in key_skills if skill not in resume_skills]
        relevant_ids: list[str] = []
        for skill in key_skills:
            for fact in facts:
                if skill.lower() in fact["value"].lower() and fact["fact_id"] not in relevant_ids:
                    relevant_ids.append(fact["fact_id"])
                    break

        summary = [
            f"{source.title}: {re.sub(r'\\s+', ' ', source.snippet)[:220]}"
            for source in bundle.sources
            if source.snippet
        ][:4]
        if bundle.job_listings:
            summary.append(
                "Published roles found: " + "; ".join(job["title"] for job in bundle.job_listings[:4]) + "."
            )
        recommendations: list[str] = []
        if key_skills:
            recommendations.append(
                "Use the terminology that appears in verified sources and published roles: "
                + ", ".join(key_skills[:6])
                + "."
            )
        if gaps:
            recommendations.append(
                "Do not add missing technologies unless the candidate has used them: " + ", ".join(gaps[:6]) + "."
            )
        recommendations.append(
            "Emphasize evidenced experience that overlaps with the target role; no unsupported culture claims are made."
        )
        return CompanyResearchResult(
            company=bundle.company,
            target_role=bundle.target_role,
            research_summary=summary,
            key_skills=key_skills,
            technical_priorities=key_skills[:5],
            language_recommendations=["Mirror concrete, evidence-backed terminology from official job postings."],
            relevant_experience_ids=relevant_ids[:10],
            optimization_recommendations=recommendations[:6],
            sources=bundle.sources[:10],
            research_timestamp=datetime.now(timezone.utc).isoformat(),
            limitations=bundle.limitations[:8],
        )


company_research_agent = CompanyResearchAgent()
