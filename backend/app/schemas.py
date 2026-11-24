from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Canonical resume
# ---------------------------------------------------------------------------


class PersonalInformation(BaseModel):
    full_name: str = ""
    headline: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    links: list[str] = Field(default_factory=list)


class ExperienceItem(BaseModel):
    company: str = ""
    role: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    current: bool = False
    bullets: list[str] = Field(default_factory=list)


class EducationItem(BaseModel):
    institution: str = ""
    degree: str = ""
    field_of_study: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    details: list[str] = Field(default_factory=list)


class SkillGroup(BaseModel):
    category: str = "Skills"
    items: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str = ""
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)
    link: str = ""


class CertificationItem(BaseModel):
    name: str = ""
    issuer: str = ""
    date: str = ""


class LanguageItem(BaseModel):
    language: str = ""
    proficiency: str = ""


class CustomSection(BaseModel):
    title: str = ""
    items: list[str] = Field(default_factory=list)


class ResumeContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    personal_information: PersonalInformation = Field(default_factory=PersonalInformation)
    professional_summary: str = ""
    experience: list[ExperienceItem] = Field(default_factory=list)
    education: list[EducationItem] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    certifications: list[CertificationItem] = Field(default_factory=list)
    languages: list[LanguageItem] = Field(default_factory=list)
    custom_sections: list[CustomSection] = Field(default_factory=list)


class Fact(BaseModel):
    """A provenance-bearing unit of candidate evidence."""

    fact_id: str
    value: str
    source_section: str
    source_location: str
    kind: str = "text"
    path: str = ""


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class Intent(str, Enum):
    GENERAL_CHAT = "GENERAL_CHAT"
    RESUME_ANALYSIS = "RESUME_ANALYSIS"
    COMPANY_OPTIMIZATION = "COMPANY_OPTIMIZATION"
    JOB_MATCHING = "JOB_MATCHING"
    SECTION_ENHANCEMENT = "SECTION_ENHANCEMENT"
    TRANSLATION = "TRANSLATION"
    RESUME_EXPORT = "RESUME_EXPORT"
    VERSION_COMPARISON = "VERSION_COMPARISON"
    UNDO = "UNDO"
    REDO = "REDO"


AGENT_RESEARCH = "company_research"
AGENT_MATCHING = "job_matching"
AGENT_ENHANCEMENT = "section_enhancement"
AGENT_TRANSLATION = "translation"
ALL_AGENTS = {AGENT_RESEARCH, AGENT_MATCHING, AGENT_ENHANCEMENT, AGENT_TRANSLATION}


class IntentResult(BaseModel):
    intent: Intent
    agents: list[str] = Field(default_factory=list)
    requires_job_description: bool = False
    requires_company: bool = False
    requires_resume: bool = True
    # True when no job description was supplied and the requirements come from
    # live research of the named company/role instead.
    requirements_research: bool = False
    target_sections: list[str] = Field(default_factory=list)
    target_company: str | None = None
    target_role: str | None = None
    translation_language: str | None = None
    translation_market: str | None = None
    execution_strategy: Literal["single", "sequential", "parallel_then_sequential"] = "single"
    clarification_required: bool = False
    clarification_question: str | None = None
    confidence: float = 0.0

    @field_validator("agents")
    @classmethod
    def _known_agents(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for agent in value:
            if agent in ALL_AGENTS and agent not in cleaned:
                cleaned.append(agent)
        return cleaned

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return max(0.0, min(1.0, float(value)))


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------


class Source(BaseModel):
    title: str = ""
    url: str = ""
    domain: str = ""
    published_date: str | None = None
    retrieved_at: str = ""
    snippet: str = ""
    kind: str = "web"
    official: bool = False

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        if value and not re.match(r"^https?://", value, re.I):
            return ""
        return value


class CompanyResearchResult(BaseModel):
    company: str = ""
    target_role: str = ""
    research_summary: list[str] = Field(default_factory=list)
    key_skills: list[str] = Field(default_factory=list)
    technical_priorities: list[str] = Field(default_factory=list)
    language_recommendations: list[str] = Field(default_factory=list)
    relevant_experience_ids: list[str] = Field(default_factory=list)
    optimization_recommendations: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    research_timestamp: str = ""
    limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Job matching
# ---------------------------------------------------------------------------


class Requirement(BaseModel):
    requirement: str
    importance: Literal["required", "preferred", "nice_to_have"] = "required"
    category: str = "technical"
    evidence: list[str] = Field(default_factory=list)
    evidence_fact_ids: list[str] = Field(default_factory=list)
    status: Literal["demonstrated", "partial", "not_demonstrated"] = "not_demonstrated"
    suggested_action: str = ""


class ScoreBreakdown(BaseModel):
    required_technical_skills: float = 0.0
    responsibilities_alignment: float = 0.0
    relevant_experience: float = 0.0
    preferred_skills: float = 0.0
    demonstrated_impact: float = 0.0


class JobMatchResult(BaseModel):
    match_score: int = 0
    requirements: list[Requirement] = Field(default_factory=list)
    matched_skills: list[str] = Field(default_factory=list)
    partial_matches: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    experience_gaps: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    sections_to_update: list[str] = Field(default_factory=list)
    section_requirements: dict[str, list[str]] = Field(default_factory=dict)
    score_explanation: str = ""
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)
    limitations: list[str] = Field(default_factory=list)


class ParsedJobDescription(BaseModel):
    title: str = ""
    company: str = ""
    required_qualifications: list[str] = Field(default_factory=list)
    preferred_qualifications: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    technical_skills: list[str] = Field(default_factory=list)
    years_experience: str = ""
    domain_requirements: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Enhancement / proposals
# ---------------------------------------------------------------------------


class ProposedChange(BaseModel):
    id: str = ""
    field_path: str
    before: str = ""
    after: str = ""
    # replace: overwrite an existing string field (default).
    # add: append `after` to the list at field_path (e.g. skills.0.items).
    # remove: delete the existing list item at field_path.
    operation: str = "replace"
    reason: str = ""
    supporting_fact_ids: list[str] = Field(default_factory=list)
    requires_user_confirmation: bool = False
    agent: str = AGENT_ENHANCEMENT
    section: str = ""
    status: str = "proposed"
    validation_notes: list[str] = Field(default_factory=list)


class SectionEnhancementResult(BaseModel):
    section: str = ""
    proposed_changes: list[ProposedChange] = Field(default_factory=list)
    consistency_warnings: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    summary: str = ""


class ValidationResult(BaseModel):
    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dropped_change_ids: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
    clarification_question: str | None = None


class ChangeProposal(BaseModel):
    base_version_id: str
    changes: list[ProposedChange] = Field(default_factory=list)
    candidate_content: dict[str, Any] = Field(default_factory=dict)
    validation: ValidationResult = Field(default_factory=ValidationResult)
    summary: str = ""
    clarification_question: str | None = None


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


class TranslationResult(BaseModel):
    language: str = ""
    market: str = ""
    translated_content: dict[str, Any] = Field(default_factory=dict)
    preserved_terms: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# API payloads
# ---------------------------------------------------------------------------


class UserOut(BaseModel):
    id: str
    email: str | None = None
    display_name: str | None = None


class ResumeSummary(BaseModel):
    id: str
    title: str
    original_filename: str
    active_version_id: str | None = None
    version_count: int = 0
    created_at: str


class VersionOut(BaseModel):
    id: str
    resume_id: str
    version_number: int
    parent_version_id: str | None = None
    change_summary: str
    changed_fields: list[Any]
    agent: str
    kind: str
    approval_status: str
    created_at: str
    is_active: bool = False
    content: dict[str, Any] | None = None


class ResumeOut(BaseModel):
    id: str
    title: str
    original_filename: str
    mime_type: str
    size_bytes: int
    extraction_meta: dict[str, Any]
    active_version_id: str | None
    active_version: VersionOut | None
    created_at: str


class ConversationOut(BaseModel):
    id: str
    title: str
    resume_id: str | None
    target_company: str | None
    target_role: str | None
    message_count: int
    updated_at: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    run_id: str | None
    meta: dict[str, Any]
    created_at: str


class RunOut(BaseModel):
    id: str
    conversation_id: str
    resume_id: str | None
    base_version_id: str | None
    intent: str
    status: str
    plan: dict[str, Any]
    final_response: str | None
    clarification_question: str | None
    error: str | None
    created_at: str
    updated_at: str


class ProposalOut(BaseModel):
    id: str
    run_id: str
    resume_id: str
    base_version_id: str
    changes: list[dict[str, Any]]
    validation: dict[str, Any]
    summary: str
    status: str
    created_version_id: str | None
    created_at: str


class ApproveRequest(BaseModel):
    accepted_change_ids: list[str] | None = None  # None = accept all valid changes
    idempotency_key: str | None = None
    expected_base_version_id: str | None = None


class RejectRequest(BaseModel):
    reason: str = ""
    idempotency_key: str | None = None


class CompareRequest(BaseModel):
    version_a: str
    version_b: str


class RestoreRequest(BaseModel):
    version_id: str


class ConversationCreate(BaseModel):
    resume_id: str | None = None
    title: str = "New conversation"


class ChatRequest(BaseModel):
    content: str = Field(min_length=1)
    resume_id: str | None = None
    job_description: str | None = None
    base_version_id: str | None = None

    @field_validator("content")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message must not be empty")
        return value
