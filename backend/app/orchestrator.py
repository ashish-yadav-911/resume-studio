from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from .agents.company_research import company_research_agent
from .agents.job_matching import job_matching_agent, parse_jd_heuristic
from .agents.section_enhancement import (
    build_candidate,
    normalize_sections,
    section_enhancement_agent,
)
from .agents.translation import translation_agent
from .config import settings
from .db import SessionLocal
from .events import bus
from .llm import SYSTEM_GUARD, llm
from .models import (
    Conversation,
    JobDescription,
    Message,
    Proposal,
    Resume,
    ResumeVersion,
    Run,
    utcnow,
)
from .resume_service import diff_contents, latest_version, redo, supersede_pending_proposals, undo
from .schemas import (
    AGENT_ENHANCEMENT,
    AGENT_MATCHING,
    AGENT_RESEARCH,
    AGENT_TRANSLATION,
    CompanyResearchResult,
    Intent,
    IntentResult,
    JobMatchResult,
    ResumeContent,
    ValidationResult,
)
from .security import detect_prompt_injection

logger = logging.getLogger("resume_studio.orchestrator")


class GraphState(TypedDict, total=False):
    run_id: str
    user_id: str
    conversation_id: str
    resume_id: str | None
    base_version_id: str | None
    content: dict[str, Any]
    facts: list[dict[str, Any]]
    user_message: str
    conversation_summary: str
    has_jd: bool
    intent: dict[str, Any]
    job_description: str | None
    target_company: str | None
    target_role: str | None
    research: dict[str, Any] | None
    matching: dict[str, Any] | None
    enhancement: dict[str, Any] | None
    translation: dict[str, Any] | None
    changes: list[dict[str, Any]]
    validation: dict[str, Any] | None
    proposal_id: str | None
    final_response: str | None
    status: str
    error: str | None
    notes: list[str]


SECTION_WORDS = {
    "professional_summary": ["summary", "profile", "objective", "about me"],
    "experience": ["experience", "work history", "employment", "jobs"],
    "skills": ["skills", "skill section", "technologies", "tech stack", "competencies"],
    "projects": ["projects", "portfolio"],
    "education": ["education", "academic"],
    "certifications": ["certifications", "certificates", "licenses"],
    "personal_information": ["name", "personal information", "contact info", "headline"],
}

LANGUAGE_MAP = {
    "spanish": ("Spanish", ""),
    "español": ("Spanish", ""),
    "espanol": ("Spanish", ""),
    "french": ("French", ""),
    "français": ("French", ""),
    "german": ("German", ""),
    "deutsch": ("German", ""),
    "portuguese": ("Portuguese", ""),
    "português": ("Portuguese", ""),
    "italian": ("Italian", ""),
    "japanese": ("Japanese", ""),
    "korean": ("Korean", ""),
    "mandarin": ("Mandarin Chinese", ""),
    "chinese": ("Chinese", ""),
    "dutch": ("Dutch", ""),
    "arabic": ("Arabic", ""),
    "hindi": ("Hindi", ""),
}

MARKETS = {
    "mexico": "Mexico",
    "mexican": "Mexico",
    "spain": "Spain",
    "spanish market": "Spain",
    "france": "France",
    "germany": "Germany",
    "brazil": "Brazil",
    "canada": "Canada",
    "uk": "United Kingdom",
    "united kingdom": "United Kingdom",
    "us": "United States",
    "usa": "United States",
    "united states": "United States",
    "japan": "Japan",
}

STOP_COMPANY = {
    "the", "a", "an", "this", "that", "my", "our", "your", "their", "role", "position",
    "job", "company", "resume", "cv", "it", "them", "me", "senior", "junior", "staff",
    "principal", "lead", "ai", "data", "software", "product", "engineering", "about",
}

SPECIAL_INTENTS = {
    Intent.GENERAL_CHAT, Intent.RESUME_EXPORT, Intent.VERSION_COMPARISON, Intent.UNDO, Intent.REDO
}


def _extract_company(message: str) -> str | None:
    message = (message or "")[:800]
    patterns = [
        r"(?:target|dream)\s+(?:company|employer)\s*(?:is|:)?\s*([A-Z][\w&.+'-]*(?:[ \t]+[A-Z][\w&.+'-]*){0,2})",
        r"(?:company|employer)\s*:\s*([A-Z][\w&.+'-]*(?:[ \t]+[A-Z][\w&.+'-]*){0,2})",
        r"\b(?:for|at|with)\s+([A-Z][\w&.+'-]*(?:[ \t]+[A-Z][\w&.+'-]*){0,2})",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, message):
            candidate = match.group(1).strip(" .,!?:;")
            candidate = re.split(
                r"\s+(?:role|position|job|opening|as|to|and|in|for)\b", candidate, maxsplit=1, flags=re.I
            )[0].strip(" .,!?:;")
            words = candidate.split()
            while words and words[-1].lower() in STOP_COMPANY:
                words.pop()
            candidate = " ".join(words)
            if candidate and candidate.lower() not in STOP_COMPANY and len(candidate) <= 60:
                return candidate
    return None


def _extract_role(message: str) -> str | None:
    message = (message or "")[:800]
    patterns = [
        r"(?:target|desired)\s+(?:role|position|job)\s*(?:is|:)?\s*([A-Za-z][\w /&+.-]{2,60})",
        r"\bfor\s+(?:a|an)?\s*([A-Za-z][\w /&+.-]{2,60}?)\s+(?:role|position|job)\b",
        r"\bas\s+(?:a|an)\s+([A-Za-z][\w /&+.-]{2,60}?)(?:[,.]|$)",
        r"\b(?:a|an)\s+((?:Senior|Staff|Principal|Lead|Junior|Mid[- ]level)?\s*[A-Za-z]*(?:Engineer|Developer|Scientist|Manager|Analyst|Designer|Architect))\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, message, re.I)
        if match:
            role = match.group(1).strip(" .,!?:;")
            if role and role.lower() not in {"my resume", "this job", "the role"}:
                return role[:80]
    return None


def _extract_sections(message: str) -> list[str]:
    lower = message.lower()
    found: list[str] = []
    for section, keywords in SECTION_WORDS.items():
        if any(keyword in lower for keyword in keywords):
            found.append(section)
    return found


def _extract_language_market(message: str) -> tuple[str | None, str | None]:
    lower = message.lower()
    language = None
    for key, (name, _) in LANGUAGE_MAP.items():
        if re.search(rf"\b{re.escape(key)}\b", lower):
            language = name
            break
    market = None
    for key, name in MARKETS.items():
        if re.search(rf"\b{re.escape(key)}\b", lower):
            market = name
            break
    return language, market


_JD_MARKER = re.compile(
    r"(?:job description|job posting|jd)\s*(?:is below|below|:|-)\s*",
    re.I,
)


def _extract_jd_from_message(message: str) -> str | None:
    marker = _JD_MARKER.search(message)
    if marker:
        candidate = message[marker.end():].strip()
        if len(candidate) > 120:
            return candidate
    lines = [line for line in message.splitlines() if line.strip()]
    keyword_hits = sum(
        1
        for keyword in ("responsibilit", "requirement", "qualification", "experience", "skills", "about the role")
        if keyword in message.lower()
    )
    if len(message) > 300 and len(lines) >= 5 and keyword_hits >= 2:
        return message.strip()
    return None




def _wants_changes(message: str) -> bool:
    return bool(
        re.search(
            r"\b(optimi[sz]e|tailor|update|rewrite|improve|enhance|modify|adjust|apply|change|rename|make .* (better|impactful|stronger))\b",
            message,
            re.I,
        )
    )


def heuristic_intent(
    message: str,
    *,
    has_resume: bool,
    has_jd: bool,
    conversation_company: str | None,
    conversation_role: str | None,
) -> IntentResult:
    lower = message.lower()
    company = _extract_company(message) or conversation_company
    role = _extract_role(message) or conversation_role
    sections = _extract_sections(message)
    language, market = _extract_language_market(message)
    jd_in_message = _extract_jd_from_message(message) is not None

    if re.search(r"\b(undo|revert|roll ?back|restore (the )?previous)\b", lower):
        return IntentResult(intent=Intent.UNDO, agents=[], requires_resume=True, confidence=0.9)
    if re.search(r"\bredo\b", lower):
        return IntentResult(intent=Intent.REDO, agents=[], requires_resume=True, confidence=0.9)
    if re.search(r"\b(export|download)\b", lower):
        return IntentResult(intent=Intent.RESUME_EXPORT, agents=[], requires_resume=True, confidence=0.9)
    if re.search(r"\b(compare|diff)\b", lower) and re.search(r"\bversion", lower):
        return IntentResult(intent=Intent.VERSION_COMPARISON, agents=[], requires_resume=True, confidence=0.85)
    if re.search(r"\b(translate|localiz|localis)\b", lower) or (
        language and re.search(r"\b(resume|cv)\b", lower) and re.search(r"\b(into|to|for)\b", lower)
    ):
        return IntentResult(
            intent=Intent.TRANSLATION,
            agents=[AGENT_TRANSLATION],
            requires_resume=True,
            target_sections=sections,
            translation_language=language,
            translation_market=market,
            execution_strategy="single",
            requires_company=False,
            requires_job_description=False,
            target_company=company,
            target_role=role,
            confidence=0.85,
        )

    job_language = bool(
        re.search(r"\b(job description|jd|match|fit|ats|requirements|qualified|qualifications|compare my resume|how well)\b", lower)
    )
    optimize_language = bool(re.search(r"\b(optimi[sz]e|tailor|research|target(ing)?|suitable)\b", lower))
    enhance_language = _wants_changes(message) or bool(
        re.search(r"\b(improve|rewrite|enhance|strengthen|make .* impactful)\b", lower)
    )
    research_only = bool(re.search(r"\b(research|learn about|tell me about)\b", lower)) and not _wants_changes(message)

    if company and optimize_language:
        agents = [AGENT_RESEARCH]
        if has_jd or jd_in_message or job_language:
            agents.append(AGENT_MATCHING)
        if not research_only:
            agents.append(AGENT_ENHANCEMENT)
        return IntentResult(
            intent=Intent.COMPANY_OPTIMIZATION,
            agents=agents,
            requires_company=True,
            requires_job_description=AGENT_MATCHING in agents,
            requires_resume=True,
            target_sections=sections,
            target_company=company,
            target_role=role,
            execution_strategy="parallel_then_sequential" if len(agents) > 1 else "single",
            confidence=0.8,
        )
    if job_language or jd_in_message:
        agents = [AGENT_MATCHING]
        if _wants_changes(message) and not re.search(r"\b(tell me|just|score|how well|explain)\b", lower):
            agents.append(AGENT_ENHANCEMENT)
        return IntentResult(
            intent=Intent.JOB_MATCHING,
            agents=agents,
            requires_job_description=True,
            requires_resume=True,
            target_sections=sections,
            target_company=company,
            target_role=role,
            execution_strategy="parallel_then_sequential" if len(agents) > 1 else "single",
            confidence=0.8,
        )
    if sections and enhance_language:
        return IntentResult(
            intent=Intent.SECTION_ENHANCEMENT,
            agents=[AGENT_ENHANCEMENT],
            requires_resume=True,
            target_sections=sections,
            target_company=company,
            target_role=role,
            execution_strategy="single",
            confidence=0.82,
        )
    if re.search(r"\b(analy[sz]e|review|audit|feedback|missing skills|strengths|weakness|improve my resume)\b", lower):
        return IntentResult(
            intent=Intent.RESUME_ANALYSIS,
            agents=[AGENT_MATCHING] if (has_jd or jd_in_message) else [],
            requires_resume=True,
            requires_job_description=bool(has_jd or jd_in_message),
            target_sections=sections,
            target_company=company,
            target_role=role,
            execution_strategy="single",
            confidence=0.7,
        )
    if enhance_language and has_resume:
        return IntentResult(
            intent=Intent.SECTION_ENHANCEMENT,
            agents=[AGENT_ENHANCEMENT],
            requires_resume=True,
            target_sections=sections or ["experience"],
            target_company=company,
            target_role=role,
            execution_strategy="single",
            confidence=0.6,
        )
    return IntentResult(
        intent=Intent.GENERAL_CHAT,
        agents=[],
        requires_resume=False,
        target_company=company,
        target_role=role,
        confidence=0.5,
    )


def validate_intent(
    raw: IntentResult,
    *,
    message: str,
    has_resume: bool,
    has_jd: bool,
    content: dict[str, Any] | None,
    conversation_company: str | None,
    conversation_role: str | None,
) -> IntentResult:
    """Deterministic guardrails around the LLM classification."""
    intent = raw.intent
    message_lower = message.lower()
    company = raw.target_company or _extract_company(message) or conversation_company
    role = raw.target_role or _extract_role(message) or conversation_role
    job_language = bool(
        re.search(r"\b(job description|jd|match|fit|ats|requirements|qualified|qualifications|compare my resume|how well)\b", message.lower())
    ) or has_jd
    # Coerce JD-driven tailoring to job matching when no company is named.
    if has_jd and not company and intent in {Intent.SECTION_ENHANCEMENT, Intent.RESUME_ANALYSIS} and (
        _wants_changes(message) or job_language
    ):
        intent = Intent.JOB_MATCHING
    agents = set(raw.agents)
    language, market = _extract_language_market(message)
    language = raw.translation_language or language
    market = raw.translation_market or market
    explicit_sections = _extract_sections(message)
    sections = [s for s in (raw.target_sections or explicit_sections) if s in SECTION_WORDS]
    if not sections:
        sections = explicit_sections

    # Remove hallucinated companies that do not appear in the message or prior context.
    if raw.target_company:
        context_ok = conversation_company and conversation_company.lower() == raw.target_company.lower()
        if raw.target_company.lower() not in message_lower and not context_ok:
            company = _extract_company(message) or conversation_company

    if intent in SPECIAL_INTENTS:
        agents = set()
    elif intent == Intent.RESUME_ANALYSIS:
        agents.discard(AGENT_ENHANCEMENT)
        agents.discard(AGENT_TRANSLATION)
        agents.discard(AGENT_RESEARCH)
        if has_jd:
            agents.add(AGENT_MATCHING)
        else:
            agents.discard(AGENT_MATCHING)
    elif intent == Intent.TRANSLATION:
        agents = {AGENT_TRANSLATION}
    elif intent == Intent.SECTION_ENHANCEMENT:
        agents.add(AGENT_ENHANCEMENT)
    elif intent == Intent.COMPANY_OPTIMIZATION:
        agents.add(AGENT_RESEARCH)
        if not company:
            agents.discard(AGENT_RESEARCH)
        if has_jd:
            agents.add(AGENT_MATCHING)
        else:
            agents.discard(AGENT_MATCHING)
        if _wants_changes(message) or "optimi" in message_lower or "tailor" in message_lower:
            agents.add(AGENT_ENHANCEMENT)
    elif intent == Intent.JOB_MATCHING:
        agents.add(AGENT_MATCHING)
        if not has_jd:
            agents.discard(AGENT_MATCHING)
        if _wants_changes(message) and not re.search(r"\b(tell me|just|score|how well|explain)\b", message_lower):
            agents.add(AGENT_ENHANCEMENT)
    agents.discard(AGENT_TRANSLATION)
    if not company:
        agents.discard(AGENT_RESEARCH)
    if not has_jd:
        agents.discard(AGENT_MATCHING)

    if not has_resume:
        agents = set()
    requires_company = intent == Intent.COMPANY_OPTIMIZATION or AGENT_RESEARCH in agents
    requires_jd = intent == Intent.JOB_MATCHING or AGENT_MATCHING in agents
    requires_language = intent == Intent.TRANSLATION

    if not sections:
        if intent in {Intent.COMPANY_OPTIMIZATION, Intent.JOB_MATCHING}:
            sections = ["experience", "skills", "professional_summary", "education"]
        elif intent == Intent.SECTION_ENHANCEMENT:
            sections = ["personal_information"] if re.search(r"\bname\b", message_lower) else ["experience"]
        else:
            sections = []
    # For JD-driven tailoring, default to all core sections only when the user did not name a
    # specific section. Explicit requests such as "skills only" are honored.
    if intent in {Intent.COMPANY_OPTIMIZATION, Intent.JOB_MATCHING} and has_jd and not explicit_sections and not raw.target_sections:
        sections = ["experience", "skills", "professional_summary", "education"]

    clarification = None
    if intent not in {Intent.GENERAL_CHAT, Intent.UNDO, Intent.REDO} and not has_resume:
        clarification = "Upload a resume first so I can work with your actual experience."
    elif requires_company and not company:
        clarification = "Which company should I research and tailor your resume for?"
    elif requires_jd and not has_jd:
        clarification = (
            "Please paste the job description (or attach it). I need the actual requirements before I can "
            "compare or tailor your resume."
        )
    elif requires_language and not language:
        clarification = "Which language and market should I localize the resume for?"
    elif intent == Intent.SECTION_ENHANCEMENT and content is not None:
        available = [s for s in sections if content.get(s)]
        if not available:
            clarification = (
                "I could not find that section in your resume. Which of these should I improve: "
                + ", ".join(s for s in SECTION_WORDS if content.get(s))
                + "?"
            )
            sections = []

    execution = (
        "parallel_then_sequential"
        if {AGENT_RESEARCH, AGENT_MATCHING}.issubset(agents) and AGENT_ENHANCEMENT in agents
        else "sequential" if len(agents) > 1
        else "single"
    )
    intent_result = IntentResult(
        intent=intent,
        agents=sorted(agents),
        requires_job_description=requires_jd,
        requires_company=requires_company,
        requires_resume=intent != Intent.GENERAL_CHAT,
        target_sections=sections,
        target_company=company,
        target_role=role,
        translation_language=language,
        translation_market=market,
        execution_strategy=execution,
        clarification_required=bool(clarification),
        clarification_question=clarification,
        confidence=raw.confidence,
    )
    return intent_result


class IntentClassifier:
    async def classify(
        self,
        *,
        message: str,
        resume_present: bool,
        has_jd: bool,
        conversation_company: str | None,
        conversation_role: str | None,
        recent_context: str,
        resume_stats: dict[str, Any] | None,
    ) -> tuple[IntentResult, dict[str, Any]]:
        fallback = heuristic_intent(
            message,
            has_resume=resume_present,
            has_jd=has_jd,
            conversation_company=conversation_company,
            conversation_role=conversation_role,
        )
        if not llm.enabled:
            return fallback, {"mode": "heuristic", "provider": "local"}

        prompt = (
            "Classify the user's request in this resume-optimization workspace. Use the strict schema. "
            "Intents: GENERAL_CHAT, RESUME_ANALYSIS, COMPANY_OPTIMIZATION, JOB_MATCHING, "
            "SECTION_ENHANCEMENT, TRANSLATION, RESUME_EXPORT, VERSION_COMPARISON, UNDO, REDO.\n"
            "Choose only the agents actually needed. Available agents: company_research, job_matching, "
            "section_enhancement, translation. Research and matching are independent and may run together; "
            "section enhancement depends on their output. Do NOT select agents for undo/redo/export/compare. "
            "Do not invent a company or role that the user did not mention. If the request is ambiguous or "
            "missing a required target, set clarification_required and ask one concise question. "
            "For translation, extract language and market. target_sections must use canonical names: "
            "professional_summary, experience, skills, projects, education, certifications, languages.\n\n"
            f"Resume uploaded: {resume_present}\nJob description available: {has_jd}\n"
            f"Known company: {conversation_company or '(none)'}\nKnown role: {conversation_role or '(none)'}\n"
            f"Resume section stats: {resume_stats or '(none)'}\n"
            f"Recent conversation (bounded):\n{recent_context[-4000:]}\n\n"
            f"<user_message>\n{message[: settings.max_message_chars]}\n</user_message>"
        )
        result = await llm.structured(
            system=SYSTEM_GUARD, prompt=prompt, schema=IntentResult, temperature=0.0, max_tokens=900
        )
        if not result.ok:
            return fallback, {"mode": "heuristic_fallback", "provider": "local", "error": result.error}
        return result.data, {"mode": "llm", "provider": "openai"}


class Orchestrator:
    def __init__(self) -> None:
        self.classifier = IntentClassifier()
        self.graph = self._build_graph()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    async def _emit_async(run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> None:
        with SessionLocal() as db:
            await bus.emit(db, run_id, event_type, payload or {})

    @staticmethod
    def _load_run(run_id: str) -> tuple[Run, Conversation]:
        db = SessionLocal()
        try:
            run = db.get(Run, run_id)
            if not run:
                raise ValueError("Run not found")
            conversation = db.get(Conversation, run.conversation_id)
            if not conversation:
                raise ValueError("Conversation not found")
            db.expunge(run)
            db.expunge(conversation)
            return run, conversation
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Graph nodes
    # ------------------------------------------------------------------
    async def load_context(self, state: GraphState) -> dict[str, Any]:
        run, conversation = self._load_run(state["run_id"])
        content: dict[str, Any] = {}
        facts: list[dict[str, Any]] = []
        base_version_id = run.base_version_id
        with SessionLocal() as db:
            if run.resume_id:
                resume = db.get(Resume, run.resume_id)
                if resume and resume.deleted_at is None:
                    version = db.get(ResumeVersion, base_version_id) if base_version_id else latest_version(db, resume)
                    if version:
                        content = version.content
                        facts = version.facts or []
                        base_version_id = version.id
            messages = db.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.desc())
                .limit(12)
            ).all()
            jd = db.get(JobDescription, conversation.job_description_id) if conversation.job_description_id else None
            jd_text = jd.raw_text if jd else None
        context = "\n".join(f"{m.role}: {m.content[:500]}" for m in reversed(messages) if m.content)[-6000:]
        return {
            "user_id": run.user_id,
            "conversation_id": conversation.id,
            "resume_id": run.resume_id,
            "base_version_id": base_version_id,
            "content": content,
            "facts": facts,
            "user_message": run.user_message,
            "conversation_summary": context,
            "has_jd": bool(jd_text),
            "job_description": jd_text,
            "target_company": conversation.target_company,
            "target_role": conversation.target_role,
            "notes": [],
        }

    async def classify(self, state: GraphState) -> dict[str, Any]:
        message = state["user_message"]
        await self._emit_async(state["run_id"], "status", {"message": "Understanding your request…", "stage": "intent"})
        jd_text = state.get("job_description")
        # A newly pasted job description becomes durable task context and overrides older context.
        # Never let a long chat message silently replace an explicitly supplied JD: without an
        # explicit "job description:" marker we only adopt detected text when no JD exists yet.
        extracted = _extract_jd_from_message(message)
        if extracted and (not jd_text or _JD_MARKER.search(message)):
            with SessionLocal() as db:
                run = db.get(Run, state["run_id"])
                conversation = db.get(Conversation, state["conversation_id"])
                parsed_jd = parse_jd_heuristic(extracted)
                jd = JobDescription(
                    user_id=run.user_id,
                    resume_id=run.resume_id,
                    title=parsed_jd.title or "Pasted job description",
                    company=None,
                    raw_text=extracted[: settings.max_jd_chars],
                    parsed=parsed_jd.model_dump(),
                )
                db.add(jd)
                db.flush()
                conversation.job_description_id = jd.id
                db.commit()
                jd_text = jd.raw_text
        resume_stats = {
            "experience_entries": len(state.get("content", {}).get("experience", []) or []),
            "skill_groups": len(state.get("content", {}).get("skills", []) or []),
            "has_summary": bool(state.get("content", {}).get("professional_summary")),
            "has_projects": bool(state.get("content", {}).get("projects")),
        }
        raw_intent, mode = await self.classifier.classify(
            message=message,
            resume_present=bool(state.get("content")),
            has_jd=bool(jd_text),
            conversation_company=state.get("target_company"),
            conversation_role=state.get("target_role"),
            recent_context=state.get("conversation_summary", ""),
            resume_stats=resume_stats if state.get("content") else None,
        )
        intent = validate_intent(
            raw_intent,
            message=message,
            has_resume=bool(state.get("content")),
            has_jd=bool(jd_text),
            content=state.get("content") or None,
            conversation_company=state.get("target_company"),
            conversation_role=state.get("target_role"),
        )
        with SessionLocal() as db:
            run = db.get(Run, state["run_id"])
            conversation = db.get(Conversation, state["conversation_id"])
            run.intent = intent.intent.value
            run.plan = intent.model_dump()
            if intent.target_company:
                conversation.target_company = intent.target_company
            if intent.target_role:
                conversation.target_role = intent.target_role
            db.commit()
        await self._emit_async(
            state["run_id"],
            "intent.classified",
            {
                "intent": intent.intent.value,
                "agents": intent.agents,
                "execution_strategy": intent.execution_strategy,
                "target_sections": intent.target_sections,
                "company": intent.target_company,
                "role": intent.target_role,
                "mode": mode.get("mode"),
            },
        )
        notes = list(state.get("notes", []))
        if mode.get("mode") in {"heuristic", "heuristic_fallback"}:
            notes.append(
                "Intent routing used the deterministic local classifier"
                + (" because the LLM call failed." if mode.get("error") else " (LLM not configured).")
            )
        injection_flags = detect_prompt_injection(message)
        if injection_flags:
            notes.extend(injection_flags)
            await self._emit_async(
                state["run_id"],
                "security.notice",
                {"message": "Instruction-like text in your message was treated as data, not commands."},
            )
        return {
            "intent": intent.model_dump(),
            "target_company": intent.target_company,
            "target_role": intent.target_role,
            "job_description": jd_text,
            "has_jd": bool(jd_text),
            "status": "clarification" if intent.clarification_required else "running",
            "notes": notes,
        }

    async def clarify(self, state: GraphState) -> dict[str, Any]:
        intent = IntentResult.model_validate(state["intent"])
        question = intent.clarification_question or "Could you provide more detail?"
        if intent.target_sections == [] and intent.intent == Intent.SECTION_ENHANCEMENT:
            available = [s for s in SECTION_WORDS if state.get("content", {}).get(s)]
            question += " Available sections: " + ", ".join(available) + "."
        response = f"I need a little more information before I can continue.\n\n{question}"
        return {"final_response": response, "status": "clarification"}

    async def analyze(self, state: GraphState) -> dict[str, Any]:
        intent = IntentResult.model_validate(state["intent"])
        agents = set(intent.agents)
        tasks: list[asyncio.Task] = []
        labels: list[str] = []
        notes = list(state.get("notes", []))

        if AGENT_RESEARCH in agents and intent.target_company:
            labels.append(AGENT_RESEARCH)
            await self._emit_async(
                state["run_id"], "agent.started",
                {"agent": AGENT_RESEARCH, "message": f"Researching {intent.target_company}…"},
            )
            tasks.append(
                asyncio.create_task(
                    company_research_agent.research(
                        company=intent.target_company,
                        role=intent.target_role or "target role",
                        content=state.get("content"),
                        facts=state.get("facts", []),
                        job_description=state.get("job_description"),
                    )
                )
            )
        if AGENT_MATCHING in agents and state.get("job_description"):
            labels.append(AGENT_MATCHING)
            await self._emit_async(
                state["run_id"], "agent.started",
                {"agent": AGENT_MATCHING, "message": "Analyzing job requirements and resume evidence…"},
            )
            tasks.append(asyncio.create_task(self._run_matching(state, intent)))

        results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
        update: dict[str, Any] = {}
        failures: list[str] = []
        for label, result in zip(labels, results):
            if isinstance(result, Exception):
                failures.append(label)
                logger.warning(
                    "Agent %s failed for run %s: %s",
                    label, state["run_id"], type(result).__name__,
                    exc_info=(type(result), result, result.__traceback__),
                )
                await self._emit_async(
                    state["run_id"], "agent.failed",
                    {"agent": label, "message": "This step could not be completed.", "retryable": True},
                )
                notes.append(f"{label.replace('_', ' ').title()} failed; no unsupported output was used.")
                continue
            payload, meta = result
            update[label] = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload
            await self._emit_async(
                state["run_id"], "agent.completed",
                {"agent": label, "summary": self._agent_summary(label, update[label]), "mode": meta.get("mode")},
            )
            if meta.get("mode") in {"heuristic", "heuristic_fallback"}:
                notes.append(
                    f"{label.replace('_', ' ').title()} used deterministic local logic"
                    + (" after the LLM call failed." if meta.get("error") else " because no LLM key is configured.")
                )

        # Primary-agent failure is fatal to the run so we never produce a half-informed proposal.
        primary = {
            Intent.COMPANY_OPTIMIZATION: AGENT_RESEARCH,
            Intent.JOB_MATCHING: AGENT_MATCHING,
            Intent.RESUME_ANALYSIS: AGENT_MATCHING,
        }.get(intent.intent)
        if primary and primary in failures:
            return {
                "error": f"The required {primary.replace('_', ' ')} step failed. No resume changes were proposed.",
                "status": "failed",
                "notes": notes,
            }
        if AGENT_RESEARCH in update:
            update["research"] = update[AGENT_RESEARCH]
        if AGENT_MATCHING in update:
            update["matching"] = update[AGENT_MATCHING]
        update["notes"] = notes
        if AGENT_ENHANCEMENT in intent.agents or AGENT_TRANSLATION in intent.agents:
            update["status"] = "running"
        else:
            merged: GraphState = {**state, **update}  # type: ignore[assignment]
            if AGENT_MATCHING in intent.agents:
                update["final_response"] = self._analysis_response(merged)
            else:
                update["final_response"] = self._no_change_response(merged, ValidationResult())
            update["status"] = "completed"
        return update

    async def _run_matching(self, state: GraphState, intent: IntentResult) -> tuple[Any, dict[str, Any]]:
        jd_text = state["job_description"] or ""
        with SessionLocal() as db:
            conversation = db.get(Conversation, state["conversation_id"])
            jd = db.get(JobDescription, conversation.job_description_id) if conversation.job_description_id else None
            parsed = None
            if jd and jd.parsed:
                try:
                    from .schemas import ParsedJobDescription

                    parsed = ParsedJobDescription.model_validate(jd.parsed)
                except Exception:
                    parsed = None
            if parsed is None:
                parsed, parse_meta = await job_matching_agent.parse_job_description(jd_text)
                if jd:
                    jd.parsed = parsed.model_dump()
                    jd.title = parsed.title or jd.title
                    jd.company = parsed.company or jd.company
                    db.commit()
        result, meta = await job_matching_agent.analyze(
            jd_text=jd_text,
            parsed=parsed,
            content=state.get("content", {}),
            target_role=intent.target_role,
        )
        return result, meta

    async def enhance(self, state: GraphState) -> dict[str, Any]:
        intent = IntentResult.model_validate(state["intent"])
        content = state.get("content", {})
        facts = state.get("facts", [])
        notes = list(state.get("notes", []))

        if intent.intent == Intent.TRANSLATION:
            await self._emit_async(
                state["run_id"], "agent.started",
                {"agent": AGENT_TRANSLATION, "message": f"Localizing resume into {intent.translation_language}…"},
            )
            translated, changes, validation, meta = await translation_agent.translate(
                content=content,
                facts=facts,
                language=intent.translation_language or "",
                market=intent.translation_market or "",
            )
            await self._emit_async(
                state["run_id"],
                "agent.completed" if translated else "agent.failed",
                {
                    "agent": AGENT_TRANSLATION,
                    "summary": f"{len(changes)} localized fields proposed." if translated else "Translation could not be validated.",
                    "mode": meta.get("mode"),
                },
            )
            return {
                "translation": {"language": intent.translation_language, "market": intent.translation_market, "meta": meta},
                "changes": [c.model_dump() for c in changes],
                "validation": validation.model_dump(),
                "status": "running" if changes else "failed",
                "error": None if changes else (validation.errors[0] if validation.errors else "Translation failed."),
                "notes": notes,
            }

        sections = normalize_sections(intent.target_sections, content)
        await self._emit_async(
            state["run_id"], "agent.started",
            {"agent": AGENT_ENHANCEMENT, "message": f"Strengthening the {', '.join(sections)} section(s)…"},
        )
        matching = state.get("matching") or {}
        research = state.get("research") or {}
        enhancement, validation, meta = await section_enhancement_agent.enhance(
            content=content,
            facts=facts,
            target_sections=sections,
            user_request=state["user_message"],
            target_role=intent.target_role,
            jd_requirements=matching.get("requirements", []) if isinstance(matching, dict) else [],
            research_recommendations=research.get("optimization_recommendations", []) if isinstance(research, dict) else [],
        )
        if meta.get("mode") in {"heuristic", "heuristic_fallback"}:
            notes.append(
                "Section enhancement used deterministic safe rewrites"
                + (" after the LLM call failed." if meta.get("error") else " because no LLM key is configured.")
            )
        if meta.get("mode") == "heuristic_fallback":
            notes.append("LLM error: " + str(meta.get("error"))[:200])
        await self._emit_async(
            state["run_id"], "agent.completed",
            {
                "agent": AGENT_ENHANCEMENT,
                "summary": f"{meta.get('changes_valid', 0)} change(s) passed validation, {meta.get('changes_blocked', 0)} blocked.",
                "mode": meta.get("mode"),
            },
        )
        return {
            "enhancement": enhancement.model_dump(),
            "changes": [c.model_dump() for c in enhancement.proposed_changes],
            "validation": validation.model_dump(),
            "notes": notes,
            "status": "running",
        }

    async def propose(self, state: GraphState) -> dict[str, Any]:
        changes = state.get("changes") or []
        applicable = [c for c in changes if c.get("status") == "proposed"]
        validation = ValidationResult.model_validate(state.get("validation") or {})
        notes = list(state.get("notes", []))
        if not applicable:
            intent = IntentResult.model_validate(state["intent"])
            response = self._no_change_response(state, validation)
            return {"final_response": response, "status": "completed", "notes": notes}

        with SessionLocal() as db:
            run = db.get(Run, state["run_id"])
            resume = db.get(Resume, state["resume_id"])
            base_id = state.get("base_version_id")
            if not resume:
                return {"error": "Resume not found.", "status": "failed", "notes": notes}
            if not base_id or resume.active_version_id != base_id:
                return {
                    "error": "Your resume changed while this run was in progress. No changes were committed; please retry.",
                    "status": "failed",
                    "notes": notes,
                }
            base_version = db.get(ResumeVersion, base_id)
            if not base_version:
                return {"error": "Base resume version is missing.", "status": "failed", "notes": notes}
            existing = db.scalar(select(Proposal).where(Proposal.run_id == run.id))
            if existing:
                proposal = existing
                run.status = "awaiting_approval"
                db.commit()
            else:
                try:
                    candidate = build_candidate(base_version.content, [
                        _change_model(c) for c in applicable
                    ])
                    ResumeContent.model_validate(candidate)
                except Exception as exc:
                    return {
                        "error": f"Proposed changes could not produce a valid resume: {exc}",
                        "status": "failed",
                        "notes": notes,
                    }
                proposal = Proposal(
                    run_id=run.id,
                    resume_id=resume.id,
                    base_version_id=base_id,
                    changes=changes,
                    candidate_content=candidate,
                    validation=validation.model_dump(),
                    summary=self._proposal_summary(state),
                    status="pending",
                )
                db.add(proposal)
                run.status = "awaiting_approval"
                db.commit()
                db.refresh(proposal)
        await self._emit_async(
            state["run_id"], "validation.completed",
            {
                "valid": True,
                "warnings": validation.warnings[:10],
                "blocked": len(changes) - len(applicable),
                "unsupported_claims": validation.unsupported_claims[:10],
            },
        )
        await self._emit_async(
            state["run_id"], "proposal.created",
            {"proposal_id": proposal.id, "change_count": len(applicable), "resume_id": proposal.resume_id},
        )
        response = self._proposal_response(state, validation, len(applicable))
        return {
            "proposal_id": proposal.id,
            "final_response": response,
            "status": "awaiting_approval",
            "notes": notes,
        }

    async def special(self, state: GraphState) -> dict[str, Any]:
        intent = IntentResult.model_validate(state["intent"])
        notes = list(state.get("notes", []))
        message = state["user_message"]
        if intent.intent == Intent.UNDO:
            with SessionLocal() as db:
                resume = db.get(Resume, state["resume_id"]) if state.get("resume_id") else None
                if not resume:
                    return {"final_response": "There is no resume to undo.", "status": "completed", "notes": notes}
                try:
                    supersede_pending_proposals(db, resume.id, commit=False)
                    version = undo(db, resume, state["user_id"])
                    await_emit = (
                        f"Reverted the last change. A new immutable version (v{version.version_number}) now restores "
                        "the previous content. Your version history was preserved."
                    )
                except ValueError as exc:
                    await_emit = f"Nothing to undo: {exc}"
            await self._emit_async(state["run_id"], "resume.version_restored", {"message": await_emit})
            return {"final_response": await_emit, "status": "completed", "notes": notes}
        if intent.intent == Intent.REDO:
            with SessionLocal() as db:
                resume = db.get(Resume, state["resume_id"]) if state.get("resume_id") else None
                if not resume:
                    return {"final_response": "There is no resume to redo changes for.", "status": "completed", "notes": notes}
                try:
                    supersede_pending_proposals(db, resume.id, commit=False)
                    version = redo(db, resume, state["user_id"])
                    response = f"Re-applied the previously undone revision as v{version.version_number}."
                except ValueError as exc:
                    response = f"Nothing to redo: {exc}"
            return {"final_response": response, "status": "completed", "notes": notes}
        if intent.intent == Intent.RESUME_EXPORT:
            version_id = state.get("base_version_id")
            response = (
                "Choose an export format from the resume panel. Download links for the current version:\n\n"
                f"- DOCX: `/api/resumes/{state.get('resume_id')}/export?format=docx&version_id={version_id}`\n"
                f"- PDF: `/api/resumes/{state.get('resume_id')}/export?format=pdf&version_id={version_id}`\n\n"
                "Exports use the selected immutable version and never modify it."
            )
            return {"final_response": response, "status": "completed", "notes": notes}
        if intent.intent == Intent.VERSION_COMPARISON:
            response = self._compare_response(state, message)
            return {"final_response": response, "status": "completed", "notes": notes}
        if intent.intent == Intent.RESUME_ANALYSIS:
            response = self._analysis_response(state)
            return {"final_response": response, "status": "completed", "notes": notes}

        # General chat: bounded context, no resume rewrite, explicit limitations.
        response = await self._general_chat(state)
        return {"final_response": response, "status": "completed", "notes": notes}

    async def finalize(self, state: GraphState) -> dict[str, Any]:
        run_id = state["run_id"]
        status = state.get("status") or "completed"
        response = state.get("final_response") or ""
        error = state.get("error")
        if error:
            status = "failed"
            response = (
                "I could not complete this run safely, so no resume changes were saved.\n\n"
                f"Reason: {error}\n\nYou can retry; failed or partial agent output is never committed."
            )
        proposal_id = state.get("proposal_id")
        validation = state.get("validation") or {}
        intent = state.get("intent") or {}

        with SessionLocal() as db:
            run = db.get(Run, run_id)
            if not run:
                return {}
            run.status = status
            run.final_response = response
            run.clarification_question = (
                IntentResult.model_validate(intent).clarification_question if intent else None
            )
            run.agent_outputs = {
                "research": state.get("research"),
                "matching": state.get("matching"),
                "enhancement": state.get("enhancement"),
                "translation": state.get("translation"),
                "validation": validation,
                "notes": state.get("notes", []),
            }
            run.updated_at = utcnow()
            conversation = db.get(Conversation, run.conversation_id)
            assistant_message: Message | None = None
            if conversation:
                existing = db.scalar(
                    select(Message).where(Message.run_id == run_id, Message.role == "assistant")
                )
                if not existing:
                    assistant_message = Message(
                        conversation_id=conversation.id,
                        role="assistant",
                        content=response,
                        run_id=run_id,
                        meta={
                            "status": status,
                            "proposal_id": proposal_id,
                            "intent": run.intent,
                        },
                    )
                    db.add(assistant_message)
                else:
                    assistant_message = existing
                conversation.message_count += 0
                conversation.updated_at = utcnow()
                if conversation.title in {"New conversation", ""}:
                    user_message = db.scalar(
                        select(Message).where(Message.run_id == run_id, Message.role == "user")
                    )
                    if user_message:
                        conversation.title = user_message.content.strip().split("\n")[0][:80] or "Resume session"
            db.commit()
            if assistant_message is not None and conversation is not None:
                db.refresh(assistant_message)
                try:
                    from .firestore_sync import mirror_conversation, mirror_message

                    mirror_message(assistant_message, run.user_id)
                    mirror_conversation(conversation)
                except Exception:  # pragma: no cover - optional mirror
                    pass

        event_type = {
            "awaiting_approval": "run.awaiting_approval",
            "clarification": "run.clarification",
            "failed": "run.failed",
        }.get(status, "run.completed")
        await self._emit_async(
            run_id,
            event_type,
            {
                "run_id": run_id,
                "status": status,
                "final_response": response,
                "proposal_id": proposal_id,
                "notes": state.get("notes", []),
            },
        )
        return {"status": status, "final_response": response}

    # ------------------------------------------------------------------
    # Response composition
    # ------------------------------------------------------------------
    @staticmethod
    def _agent_summary(agent: str, payload: Any) -> str:
        if agent == AGENT_RESEARCH and isinstance(payload, dict):
            return f"Reviewed {len(payload.get('sources', []))} source(s) and {len(payload.get('optimization_recommendations', []))} recommendation(s)."
        if agent == AGENT_MATCHING and isinstance(payload, dict):
            return f"Alignment score {payload.get('match_score', 0)}/100; {len(payload.get('missing_skills', []))} missing skill(s)."
        return "Completed."

    @staticmethod
    def _proposal_summary(state: GraphState) -> str:
        changes = state.get("changes") or []
        sections = sorted({c.get("section", "") for c in changes if c.get("status") == "proposed"})
        return f"Proposed {len([c for c in changes if c.get('status') == 'proposed'])} change(s) to {', '.join(sections)}."

    def _proposal_response(self, state: GraphState, validation: ValidationResult, count: int) -> str:
        matching = state.get("matching") or {}
        research = state.get("research") or {}
        changes = state.get("changes") or []
        parts = [f"### Proposed changes ({count} ready for review)"]
        sections = sorted({c.get("section", "") for c in changes if c.get("status") == "proposed"})
        parts.append(f"I prepared a new resume candidate from your current version. Sections: {', '.join(sections)}.")
        if matching:
            parts.append(
                f"**Job alignment:** {matching.get('match_score', 0)}/100 (heuristic resume alignment). "
                + ("Missing skills: " + ", ".join(matching.get("missing_skills", [])[:8]) + "." if matching.get("missing_skills") else "No JD skills were missing from the resume.")
            )
        if research:
            parts.append(
                f"**Company research:** {len(research.get('sources', []))} live source(s) retained"
                + (f" for {research.get('company')}." if research.get("company") else ".")
            )
        parts.append(
            "**Nothing has been saved yet.** Review the before/after diff and accept or reject individual changes. "
            "Approval creates an immutable new version."
        )
        if validation.unsupported_claims:
            parts.append(
                "**Blocked unsupported claims:**\n"
                + "\n".join(f"- {item}" for item in validation.unsupported_claims[:6])
            )
        if validation.warnings:
            parts.append("**Validation notes:**\n" + "\n".join(f"- {item}" for item in validation.warnings[:6]))
        if validation.clarification_question:
            parts.append("**Need your input:** " + validation.clarification_question)
        notes = state.get("notes", [])
        if notes:
            parts.append("**Mode / limitations:**\n" + "\n".join(f"- {n}" for n in notes[:6]))
        return "\n\n".join(parts)

    def _no_change_response(self, state: GraphState, validation: ValidationResult) -> str:
        matching = state.get("matching") or {}
        research = state.get("research") or {}
        parts: list[str] = []
        if matching:
            parts.append(
                f"### Job alignment: {matching.get('match_score', 0)}/100\n\n"
                + matching.get("score_explanation", "")
            )
            if matching.get("missing_skills"):
                parts.append("**Missing from your resume:** " + ", ".join(matching["missing_skills"]))
            if matching.get("experience_gaps"):
                parts.append("**Gaps:**\n" + "\n".join(f"- {g}" for g in matching["experience_gaps"][:6]))
        if research:
            parts.append(
                f"### Research: {research.get('company', '')}\n\n"
                + ("\n".join(f"- {item}" for item in research.get("research_summary", [])[:5]) or "No verified findings.")
            )
            if research.get("sources"):
                parts.append(
                    "**Sources:**\n"
                    + "\n".join(f"- [{s.get('title', 'source')}]({s.get('url')})" for s in research["sources"][:6] if s.get("url"))
                )
        if validation.unsupported_claims:
            parts.append(
                "I did **not** save changes containing unsupported claims:\n"
                + "\n".join(f"- {item}" for item in validation.unsupported_claims[:8])
            )
        if validation.clarification_question:
            parts.append("**I need your input:** " + validation.clarification_question)
        if not parts:
            parts.append(
                "I could not produce a safe, evidence-backed change from the available resume content. "
                "No modifications were saved."
            )
        notes = state.get("notes", [])
        if notes:
            parts.append("**Notes:**\n" + "\n".join(f"- {n}" for n in notes[:6]))
        return "\n\n".join(parts)

    def _analysis_response(self, state: GraphState) -> str:
        content = state.get("content", {})
        matching = state.get("matching") or {}
        if matching:
            parts = [f"### Job alignment: {matching.get('match_score', 0)}/100", matching.get("score_explanation", "")]
            reqs = matching.get("requirements", [])
            if reqs:
                parts.append(
                    "**Requirements:**\n"
                    + "\n".join(
                        f"- {r.get('status', '').replace('_', ' ').title()}: {r.get('requirement', '')[:180]}"
                        for r in reqs[:12]
                    )
                )
            if matching.get("missing_skills"):
                parts.append("**Missing skills:** " + ", ".join(matching["missing_skills"]))
            if matching.get("limitations"):
                parts.append("**Limitations:**\n" + "\n".join(f"- {x}" for x in matching["limitations"][:5]))
            return "\n\n".join(parts)
        stats = self._resume_stats(content)
        recommendations = [
            "Add measurable outcomes only where you can verify the numbers.",
            "Lead bullets with strong action verbs and keep one achievement per bullet.",
            "Mirror concrete terminology from a specific job description before tailoring.",
        ]
        return (
            "### Resume analysis\n\n"
            + "\n".join(f"- {line}" for line in stats)
            + "\n\n**Suggestions**\n"
            + "\n".join(f"- {r}" for r in recommendations)
            + "\n\nNo resume changes were made. Paste a job description for requirement-level matching, or ask me to improve a specific section."
        )

    @staticmethod
    def _resume_stats(content: dict[str, Any]) -> list[str]:
        from .resume_service import extract_metrics, resume_text

        exp = content.get("experience", []) or []
        bullets = [b for e in exp for b in e.get("bullets", []) or []]
        metrics = [b for b in bullets if extract_metrics(b)]
        skills = [item for group in content.get("skills", []) or [] for item in group.get("items", []) or []]
        return [
            f"{len(exp)} experience entr{'y' if len(exp) == 1 else 'ies'}, {len(bullets)} bullet(s).",
            f"{len(metrics)} of {len(bullets)} bullets contain a measurable value.",
            f"{len(skills)} listed skill(s) across {len(content.get('skills', []) or [])} group(s).",
            f"Professional summary present: {'yes' if content.get('professional_summary') else 'no'}.",
            f"Education entries: {len(content.get('education', []) or [])}; projects: {len(content.get('projects', []) or [])}.",
            f"Resume text length: {len(resume_text(content))} characters.",
        ]

    def _compare_response(self, state: GraphState, message: str) -> str:
        numbers = [int(n) for n in re.findall(r"\b(?:v|version\s*)?(\d{1,2})\b", message, re.I)]
        numbers = [n for n in numbers if n > 0][:2]
        with SessionLocal() as db:
            resume = db.get(Resume, state["resume_id"]) if state.get("resume_id") else None
            if not resume:
                return "There is no resume to compare."
            versions = db.scalars(
                select(ResumeVersion).where(ResumeVersion.resume_id == resume.id).order_by(ResumeVersion.version_number)
            ).all()
        by_number = {v.version_number: v for v in versions}
        if len(numbers) < 2 or numbers[0] not in by_number or numbers[1] not in by_number:
            listed = ", ".join(f"v{v.version_number}" for v in versions[-10:])
            return (
                "Tell me which two versions to compare, for example: `compare version 2 and 4`.\n\n"
                f"Available versions: {listed or 'none'}."
            )
        a, b = by_number[numbers[0]], by_number[numbers[1]]
        diff = diff_contents(a.content, b.content)
        if not diff:
            return f"Versions v{a.version_number} and v{b.version_number} have identical content."
        lines = [f"### v{a.version_number} → v{b.version_number} ({len(diff)} changed field(s))"]
        for item in diff[:15]:
            before = item["before"][:150] or "—"
            after = item["after"][:150] or "—"
            lines.append(f"- **{item['field_path']}**\n  - before: {before}\n  - after: {after}")
        return "\n".join(lines)

    async def _general_chat(self, state: GraphState) -> str:
        if not llm.enabled:
            return (
                "I can upload and structure a resume, research a company, match a job description, propose "
                "evidence-backed changes, and manage versions. Upload a resume or paste a job description to begin.\n\n"
                "Note: no LLM key is configured, so I am operating in deterministic local mode. Intent routing, "
                "scoring and safe rewrites still work; free-form conversation is limited."
            )
        context = state.get("conversation_summary", "")[-3000:]
        stats = self._resume_stats(state["content"]) if state.get("content") else ["No resume uploaded yet."]
        result = await llm.text(
            system=(
                SYSTEM_GUARD
                + " You are the concise workspace assistant. Answer in at most 180 words. Do not claim to have "
                "changed or saved a resume in general chat. Point the user to concrete available actions."
            ),
            prompt=(
                f"Resume stats: {'; '.join(stats)}\nConversation:\n{context}\n\n"
                f"User: {state['user_message'][:2000]}"
            ),
            max_tokens=400,
        )
        if result.ok:
            return str(result.data)
        return (
            "I can help optimize a resume against a company or job description, improve a specific section, "
            "compare versions, or undo/redo changes. Upload a resume or paste a job description to begin."
        )

    # ------------------------------------------------------------------
    # Graph construction / execution
    # ------------------------------------------------------------------
    def _build_graph(self):
        graph = StateGraph(GraphState)
        graph.add_node("load", self.load_context)
        graph.add_node("classify", self.classify)
        graph.add_node("clarify", self.clarify)
        graph.add_node("analyze", self.analyze)
        graph.add_node("enhance", self.enhance)
        graph.add_node("propose", self.propose)
        graph.add_node("special", self.special)
        graph.add_node("finalize", self.finalize)
        graph.set_entry_point("load")
        graph.add_edge("load", "classify")
        graph.add_conditional_edges(
            "classify",
            self._route_classify,
            {"clarify": "clarify", "analyze": "analyze", "enhance": "enhance", "special": "special"},
        )
        graph.add_edge("clarify", "finalize")
        graph.add_edge("special", "finalize")
        graph.add_conditional_edges(
            "analyze",
            self._route_analyze,
            {"enhance": "enhance", "propose": "propose", "finalize": "finalize"},
        )
        graph.add_conditional_edges(
            "enhance",
            self._route_enhance,
            {"propose": "propose", "finalize": "finalize"},
        )
        graph.add_edge("propose", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=MemorySaver())

    @staticmethod
    def _route_classify(state: GraphState) -> str:
        if state.get("status") == "clarification":
            return "clarify"
        intent = IntentResult.model_validate(state["intent"])
        if intent.intent in SPECIAL_INTENTS:
            return "special"
        if intent.intent == Intent.RESUME_ANALYSIS and not state.get("has_jd"):
            return "special"
        if intent.intent == Intent.TRANSLATION:
            return "enhance"
        if intent.intent == Intent.SECTION_ENHANCEMENT:
            return "enhance"
        if AGENT_RESEARCH in intent.agents or AGENT_MATCHING in intent.agents:
            return "analyze"
        return "special"

    @staticmethod
    def _route_analyze(state: GraphState) -> str:
        if state.get("status") == "failed" or state.get("error"):
            return "finalize"
        intent = IntentResult.model_validate(state["intent"])
        if intent.intent == Intent.RESUME_ANALYSIS:
            return "finalize"
        if AGENT_ENHANCEMENT in intent.agents:
            return "enhance"
        if AGENT_TRANSLATION in intent.agents:
            return "enhance"
        return "finalize"

    @staticmethod
    def _route_enhance(state: GraphState) -> str:
        if state.get("status") == "failed" or state.get("error"):
            return "finalize"
        if state.get("changes"):
            return "propose"
        return "finalize"

    async def execute(self, run_id: str) -> None:
        with SessionLocal() as db:
            run = db.get(Run, run_id)
            if not run:
                return
            run.status = "running"
            run.updated_at = utcnow()
            db.commit()
            conversation = db.get(Conversation, run.conversation_id)
            base_version_id = run.base_version_id
        try:
            await self._emit_async(run_id, "run.started", {"run_id": run_id, "status": "running"})
            final_state = await self.graph.ainvoke(
                {"run_id": run_id, "base_version_id": base_version_id, "status": "running"},
                config={"configurable": {"thread_id": run_id}, "recursion_limit": 30},
            )
            if final_state.get("status") == "failed" and not final_state.get("final_response"):
                # Safety net: ensure the failure is persisted and streamed.
                await self.finalize({**final_state, "run_id": run_id})
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Run %s failed", run_id)
            with SessionLocal() as db:
                run = db.get(Run, run_id)
                if run:
                    run.status = "failed"
                    run.error = type(exc).__name__
                    run.final_response = (
                        "The run failed before a safe proposal could be created. Nothing was saved. "
                        "Please retry; if this persists, check server logs for the request id."
                    )
                    db.commit()
            await self._emit_async(
                run_id,
                "run.failed",
                {
                    "run_id": run_id,
                    "status": "failed",
                    "final_response": "The run failed before a safe proposal could be created. Nothing was saved.",
                },
            )


def _change_model(data: dict[str, Any]):
    from .schemas import ProposedChange

    return ProposedChange.model_validate(data)


orchestrator = Orchestrator()
