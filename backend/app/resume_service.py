"""Canonical resume helpers: paths, facts, patches, diffs and versions."""
from __future__ import annotations

import copy
import hashlib
import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Resume, ResumeVersion
from .schemas import Fact, ResumeContent

SECTION_LABELS = {
    "personal_information": "Personal information",
    "professional_summary": "Professional summary",
    "experience": "Experience",
    "education": "Education",
    "skills": "Skills",
    "projects": "Projects",
    "certifications": "Certifications",
    "languages": "Languages",
    "custom_sections": "Custom sections",
}

PATH_PART_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def parse_path(path: str) -> list[str | int]:
    parts: list[str | int] = []
    for match in PATH_PART_RE.finditer(path):
        key, index = match.groups()
        if index is not None:
            parts.append(int(index))
        elif key is not None and key != "":
            parts.append(key)
    return parts


def get_path(content: Any, path: str) -> Any:
    current = content
    for part in parse_path(path):
        if isinstance(current, list):
            if isinstance(part, str) and part.isdigit():
                part = int(part)
            if not isinstance(part, int) or part >= len(current) or part < 0:
                raise KeyError(f"Invalid list index in path: {path}")
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                raise KeyError(f"Unknown field in path: {path}")
            current = current[part]
    return current


def set_path(content: Any, path: str, value: Any) -> None:
    parts = parse_path(path)
    if not parts:
        raise KeyError("Empty field path")
    current = content
    for part in parts[:-1]:
        if isinstance(current, list):
            if isinstance(part, str) and part.isdigit():
                part = int(part)
            if not isinstance(part, int) or part >= len(current) or part < 0:
                raise KeyError(f"Invalid list index in path: {path}")
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                raise KeyError(f"Unknown field in path: {path}")
            current = current[part]
    last = parts[-1]
    if isinstance(current, list):
        if isinstance(last, str) and last.isdigit():
            last = int(last)
        if not isinstance(last, int) or last >= len(current) or last < 0:
            raise KeyError(f"Invalid list index in path: {path}")
        current[last] = value
    else:
        if not isinstance(current, dict) or last not in current:
            raise KeyError(f"Unknown field in path: {path}")
        current[last] = value


def section_for_path(path: str) -> str:
    return parse_path(path)[0] if parse_path(path) else ""


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[\u2010-\u2015]", "-", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def resume_text(content: dict[str, Any], include_personal: bool = False) -> str:
    chunks: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}.{index}")
        elif isinstance(node, str):
            if not include_personal and path.startswith("personal_information"):
                return
            node = node.strip()
            if node:
                chunks.append(node)

    walk(content, "")
    return "\n".join(chunks)


METRIC_RE = re.compile(
    r"(?<![\w.])("
    r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmMbB])?"
    r"|\d[\d,]*(?:\.\d+)?\s?%"
    r"|\d[\d,]*(?:\.\d+)?\s?(?:x|X)\b"
    r"|\b\d[\d,]{2,}(?:\.\d+)?\b"
    r")"
)

YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def extract_metrics(text: str) -> set[str]:
    metrics: set[str] = set()
    for match in METRIC_RE.finditer(text or ""):
        token = re.sub(r"\s+", "", match.group(1)).lower().rstrip(".,;:")
        if YEAR_RE.match(token):
            continue
        metrics.add(token)
    return metrics


def derive_facts(content: dict[str, Any]) -> list[Fact]:
    """Build deterministic, provenance-bearing evidence units from resume JSON."""
    facts: list[Fact] = []

    def add(value: str, path: str, section: str, kind: str = "text") -> None:
        value = (value or "").strip()
        if not value:
            return
        digest = hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]
        facts.append(
            Fact(
                fact_id=f"fact_{digest}",
                value=value,
                source_section=section,
                source_location=f"resume:{path}",
                kind=kind,
                path=path,
            )
        )

    walk_paths: list[tuple[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}.{index}")
        elif isinstance(node, str):
            if path:
                walk_paths.append((path, node))

    walk(content, "")
    for path, value in walk_paths:
        section = section_for_path(path)
        if section == "personal_information":
            kind = "personal"
        elif path.endswith(".bullets") or ".bullets." in path or ".items." in path:
            kind = "bullet"
        else:
            kind = "text"
        add(value, path, section, kind)

    # Composite experience facts make evidence retrieval and display clearer.
    for index, exp in enumerate(content.get("experience", []) or []):
        header = " at ".join(p for p in [exp.get("role", ""), exp.get("company", "")] if p)
        bullets = " ".join(exp.get("bullets", []) or [])
        if header or bullets:
            add(f"{header}. {bullets}".strip(". "), f"experience.{index}", "experience", "experience")
    for index, project in enumerate(content.get("projects", []) or []):
        add(
            f"{project.get('name', '')}. {project.get('description', '')} {' '.join(project.get('bullets', []) or [])}".strip(),
            f"projects.{index}",
            "projects",
            "project",
        )
    return facts


def facts_map(facts: list[dict[str, Any]] | list[Fact]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for fact in facts:
        data = fact.model_dump() if isinstance(fact, Fact) else fact
        out[data["fact_id"]] = data
    return out


def apply_change(content: dict[str, Any], path: str, after: str) -> dict[str, Any]:
    updated = copy.deepcopy(content)
    set_path(updated, path, after)
    return updated


def apply_changes(content: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    updated = copy.deepcopy(content)
    for change in changes:
        set_path(updated, change["field_path"], change["after"])
    return updated


def flatten(content: Any, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}.{index}")
        elif isinstance(node, str):
            if node.strip():
                flat[path] = node

    walk(content, prefix)
    return flat


def diff_contents(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    left = flatten(before)
    right = flatten(after)
    diff: list[dict[str, Any]] = []
    for path in sorted(set(left) | set(right)):
        if left.get(path) != right.get(path):
            diff.append(
                {
                    "field_path": path,
                    "section": section_for_path(path),
                    "before": left.get(path, ""),
                    "after": right.get(path, ""),
                }
            )
    return diff


def version_to_dict(version: ResumeVersion, include_content: bool = False, active: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": version.id,
        "resume_id": version.resume_id,
        "version_number": version.version_number,
        "parent_version_id": version.parent_version_id,
        "change_summary": version.change_summary,
        "changed_fields": version.changed_fields or [],
        "agent": version.agent,
        "kind": version.kind,
        "approval_status": version.approval_status,
        "created_at": version.created_at.isoformat(),
        "is_active": active,
    }
    if include_content:
        data["content"] = version.content
    return data


def latest_version(db: Session, resume: Resume) -> ResumeVersion | None:
    if resume.active_version_id:
        version = db.get(ResumeVersion, resume.active_version_id)
        if version:
            return version
    return db.scalar(
        select(ResumeVersion)
        .where(ResumeVersion.resume_id == resume.id)
        .order_by(ResumeVersion.version_number.desc())
    )


def next_version_number(db: Session, resume_id: str) -> int:
    current = db.scalar(
        select(func.max(ResumeVersion.version_number)).where(ResumeVersion.resume_id == resume_id)
    )
    return int(current or 0) + 1


def create_version(
    db: Session,
    resume: Resume,
    content: dict[str, Any],
    *,
    summary: str,
    changed_fields: list[Any] | None = None,
    agent: str = "system",
    kind: str = "accepted",
    parent_version_id: str | None = None,
    conversation_id: str | None = None,
    run_id: str | None = None,
    undo_of_version_id: str | None = None,
    undone_version_id: str | None = None,
    set_active: bool = True,
    commit: bool = True,
) -> ResumeVersion:
    validated = ResumeContent.model_validate(content).model_dump(mode="json")
    version = ResumeVersion(
        resume_id=resume.id,
        version_number=next_version_number(db, resume.id),
        parent_version_id=parent_version_id,
        content=validated,
        facts=[f.model_dump() for f in derive_facts(validated)],
        change_summary=summary,
        changed_fields=changed_fields or [],
        agent=agent,
        kind=kind,
        approval_status="approved",
        conversation_id=conversation_id,
        run_id=run_id,
        undo_of_version_id=undo_of_version_id,
        undone_version_id=undone_version_id,
    )
    db.add(version)
    db.flush()
    if set_active:
        resume.active_version_id = version.id
    if commit:
        db.commit()
        db.refresh(version)
        try:
            from .firestore_sync import mirror_resume, mirror_version

            mirror_version(resume, version)
            mirror_resume(resume)
        except Exception:  # pragma: no cover - mirroring is always optional
            pass
    return version


def resume_content(db: Session, resume: Resume) -> tuple[dict[str, Any], ResumeVersion]:
    version = latest_version(db, resume)
    if not version:
        raise ValueError("Resume has no versions")
    return version.content, version


def can_undo(db: Session, resume: Resume) -> bool:
    active = latest_version(db, resume)
    if not active:
        return False
    if active.kind == "undo" and active.undo_of_version_id:
        restored = db.get(ResumeVersion, active.undo_of_version_id)
        return bool(restored and restored.parent_version_id)
    return bool(active.parent_version_id)


def supersede_pending_proposals(db: Session, resume_id: str, *, commit: bool = True) -> int:
    from .models import Proposal

    proposals = db.scalars(
        select(Proposal).where(Proposal.resume_id == resume_id, Proposal.status == "pending")
    ).all()
    for proposal in proposals:
        proposal.status = "superseded"
    if commit:
        db.commit()
    return len(proposals)


def can_redo(db: Session, resume: Resume) -> bool:
    active = latest_version(db, resume)
    return bool(active and active.undone_version_id)


def undo(db: Session, resume: Resume, user_id: str) -> ResumeVersion:
    active = latest_version(db, resume)
    if not active:
        raise ValueError("Nothing to undo")
    if active.kind == "undo" and active.undo_of_version_id:
        restored = db.get(ResumeVersion, active.undo_of_version_id)
        target_id = restored.parent_version_id if restored else None
        target = db.get(ResumeVersion, target_id) if target_id else None
        forward = restored
    else:
        target = db.get(ResumeVersion, active.parent_version_id) if active.parent_version_id else None
        forward = active
    if not target:
        raise ValueError("No earlier accepted version is available")
    return create_version(
        db,
        resume,
        copy.deepcopy(target.content),
        summary=f"Undo: restored version {target.version_number}",
        changed_fields=target.changed_fields,
        agent="system",
        kind="undo",
        parent_version_id=active.id,
        undo_of_version_id=target.id,
        undone_version_id=forward.id if forward else active.id,
    )


def _next_accepted_child(db: Session, version: ResumeVersion) -> ResumeVersion | None:
    return db.scalar(
        select(ResumeVersion)
        .where(
            ResumeVersion.parent_version_id == version.id,
            ResumeVersion.kind.in_(["accepted", "original", "localized", "redo"]),
        )
        .order_by(ResumeVersion.version_number.asc())
    )


def redo(db: Session, resume: Resume, user_id: str) -> ResumeVersion:
    active = latest_version(db, resume)
    if not active or not active.undone_version_id:
        raise ValueError("Nothing to redo")
    target = db.get(ResumeVersion, active.undone_version_id)
    if not target:
        raise ValueError("Version to redo is missing")
    forward = _next_accepted_child(db, target)
    return create_version(
        db,
        resume,
        copy.deepcopy(target.content),
        summary=f"Redo: re-applied version {target.version_number}",
        changed_fields=target.changed_fields,
        agent="system",
        kind="redo",
        parent_version_id=active.id,
        undone_version_id=forward.id if forward else None,
    )
