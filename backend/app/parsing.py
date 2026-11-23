"""Document ingestion: validation, PDF/DOCX extraction and section inference.

Parsing is intentionally conservative: anything that cannot be structured is
kept in a custom section or as a warning rather than silently discarded.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from docx import Document

from .config import settings
from .schemas import ResumeContent

MAX_PDF_PAGES = 40
MAX_DOCX_ENTRIES = 2000
MAX_DOCX_UNCOMPRESSED = 80 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250


class DocumentError(Exception):
    """A safe, user-facing document error."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


HEADING_ALIASES: dict[str, set[str]] = {
    "professional_summary": {
        "summary", "professional summary", "profile", "objective", "about", "about me",
        "professional profile", "career summary",
    },
    "experience": {
        "experience", "work experience", "professional experience", "employment",
        "employment history", "work history", "career history",
    },
    "education": {"education", "academic background", "education & training", "qualifications"},
    "skills": {
        "skills", "technical skills", "core skills", "core competencies", "technologies",
        "tech stack", "technical proficiencies", "areas of expertise",
    },
    "projects": {"projects", "personal projects", "selected projects", "key projects", "portfolio"},
    "certifications": {"certifications", "certificates", "licenses", "certifications & training"},
    "languages": {"languages", "language skills"},
    "custom_sections": {
        "awards", "honors", "publications", "volunteer experience", "volunteering", "interests",
        "activities", "leadership", "achievements", "references", "training", "coursework",
    },
}

SECTION_TO_KEY = {
    alias: key for key, aliases in HEADING_ALIASES.items() for alias in aliases
}

DATE_RE = re.compile(
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}"
    r"|\d{1,2}/\d{4}|\d{4})"
    r"\s*(?:-|–|—|to|until)\s*"
    r"((?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{4}"
    r"|\d{1,2}/\d{4}|\d{4}|present|current|now)",
    re.I,
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
URL_RE = re.compile(r"(?:https?://\S+|(?:www\.|linkedin\.com/|github\.com/)\S+)", re.I)
BULLET_RE = re.compile(r"^\s*(?:[-•●▪◦*·]|\d+[.)])\s+")
ROLE_WORDS = re.compile(
    r"\b(engineer|developer|manager|director|analyst|scientist|consultant|architect|designer|"
    r"intern|lead|head|specialist|administrator|officer|associate|founder|president)\b",
    re.I,
)


@dataclass
class TextBlock:
    text: str
    page: int = 1
    is_heading: bool = False
    is_bullet: bool = False


@dataclass
class ParsedDocument:
    content: dict[str, Any]
    raw_text: str
    meta: dict[str, Any] = field(default_factory=dict)


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "resume").name
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "resume"
    stem = Path(name).stem[:120] or "resume"
    suffix = Path(name).suffix.lower()[:10]
    return f"{stem}{suffix}"


def validate_upload(filename: str, data: bytes, declared_mime: str | None) -> tuple[str, str]:
    if not data:
        raise DocumentError("EMPTY_FILE", "The uploaded file is empty.")
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise DocumentError(
            "FILE_TOO_LARGE", f"File exceeds the {settings.max_upload_mb} MB upload limit.", 413
        )
    safe_name = sanitize_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        raise DocumentError(
            "UNSUPPORTED_FILE_TYPE", "Only text-based PDF and DOCX resumes are supported."
        )
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise DocumentError("INVALID_FILE_SIGNATURE", "The file is not a valid PDF.")
        return safe_name, "application/pdf"
    if data[:4] != b"PK\x03\x04":
        raise DocumentError("INVALID_FILE_SIGNATURE", "The file is not a valid DOCX document.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if len(names) > MAX_DOCX_ENTRIES:
                raise DocumentError("DOCX_TOO_COMPLEX", "DOCX contains too many entries.")
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise DocumentError("INVALID_DOCX", "The file is not a valid Word document.")
            if any("vbaproject.bin" in n.lower() or n.lower().endswith(".docm") for n in names):
                raise DocumentError(
                    "MACRO_DOCUMENT", "Macro-enabled Word documents are not accepted. Save as .docx and retry."
                )
            total = sum(info.file_size for info in archive.infolist())
            if total > MAX_DOCX_UNCOMPRESSED:
                raise DocumentError("DOCX_TOO_LARGE", "The uncompressed document is too large.")
            if any(
                info.file_size > 5 * 1024 * 1024 and info.file_size > max(info.compress_size, 1) * MAX_COMPRESSION_RATIO
                for info in archive.infolist()
            ):
                raise DocumentError("DOCX_SUSPICIOUS", "The document has a suspicious compression ratio.")
    except zipfile.BadZipFile as exc:
        raise DocumentError("INVALID_DOCX", "The DOCX archive is corrupted.") from exc
    return safe_name, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\x00", " ")).strip()


def extract_pdf(data: bytes) -> tuple[list[TextBlock], dict[str, Any]]:
    warnings: list[str] = []
    blocks: list[TextBlock] = []
    ocr_used = False
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # pragma: no cover - library specific
        raise DocumentError("CORRUPT_PDF", "The PDF could not be opened.") from exc
    try:
        if doc.needs_pass:
            raise DocumentError("ENCRYPTED_PDF", "Password-protected PDFs are not supported. Upload an unlocked copy.")
        if doc.page_count == 0:
            raise DocumentError("EMPTY_PDF", "The PDF has no pages.")
        if doc.page_count > MAX_PDF_PAGES:
            raise DocumentError("PDF_TOO_LONG", f"PDFs over {MAX_PDF_PAGES} pages are not supported.")
        for page_index in range(doc.page_count):
            page = doc[page_index]
            page_dict = page.get_text("dict")
            width = page.rect.width or 1
            text_blocks = [
                b
                for b in page_dict.get("blocks", [])
                if b.get("type") == 0 and b.get("lines")
            ]
            text_blocks.sort(key=lambda b: (round(b["bbox"][1] / 4), b["bbox"][0]))
            # Two-column detection: overlapping vertical ranges with distinct x halves.
            left = [b for b in text_blocks if (b["bbox"][0] + b["bbox"][2]) / 2 < width * 0.45]
            right = [b for b in text_blocks if (b["bbox"][0] + b["bbox"][2]) / 2 > width * 0.55]
            if len(left) >= 3 and len(right) >= 3:
                warnings.append(
                    f"Page {page_index + 1}: multi-column layout detected; reading order may need review."
                )
                text_blocks.sort(key=lambda b: (0 if (b["bbox"][0] + b["bbox"][2]) / 2 < width / 2 else 1, b["bbox"][1]))
            for block in text_blocks:
                for line in block.get("lines", []):
                    text = _clean("".join(span.get("text", "") for span in line.get("spans", [])))
                    if text:
                        blocks.append(TextBlock(text=text, page=page_index + 1))
        raw_text = "\n".join(b.text for b in blocks)
        if len(_clean(raw_text)) < 80:
            ocr_blocks, ocr_warning = _ocr_pdf(doc)
            if ocr_blocks:
                blocks = ocr_blocks
                ocr_used = True
                raw_text = "\n".join(b.text for b in blocks)
                warnings.append("The PDF appears to be scanned; OCR text was used. Review extraction carefully.")
            else:
                warnings.append(ocr_warning or "Very little text was extracted from this PDF.")
    finally:
        doc.close()
    meta = {"page_count": len({b.page for b in blocks}) or 0, "ocr_used": ocr_used, "warnings": warnings}
    return blocks, meta


def _ocr_pdf(doc: fitz.Document) -> tuple[list[TextBlock], str]:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except Exception:
        return [], "This looks like a scanned PDF and OCR is not installed. Upload a text-based PDF or DOCX."
    blocks: list[TextBlock] = []
    try:
        for page_index in range(min(doc.page_count, MAX_PDF_PAGES)):
            pix = doc[page_index].get_pixmap(dpi=200, alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(image)
            for line in text.splitlines():
                line = _clean(line)
                if line:
                    blocks.append(TextBlock(text=line, page=page_index + 1))
    except Exception:
        return [], "OCR failed on this PDF. Upload a text-based PDF or DOCX."
    return blocks, ""


def extract_docx(data: bytes) -> tuple[list[TextBlock], dict[str, Any]]:
    warnings: list[str] = []
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise DocumentError("CORRUPT_DOCX", "The DOCX could not be opened. It may be corrupted.") from exc
    blocks: list[TextBlock] = []
    try:
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.ns import qn

        body = document.element.body
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, document)
                text = _clean(paragraph.text)
                if not text:
                    continue
                style = (paragraph.style.name or "").lower() if paragraph.style else ""
                is_heading = "heading" in style or "title" in style
                is_bullet = "list" in style or paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
                blocks.append(TextBlock(text=text, page=1, is_heading=is_heading, is_bullet=is_bullet))
            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                for row in table.rows:
                    cells = [_clean(c.text) for c in row.cells if _clean(c.text)]
                    if cells:
                        blocks.append(TextBlock(text=" | ".join(dict.fromkeys(cells)), page=1))
    except Exception:  # pragma: no cover - defensive
        blocks = [
            TextBlock(text=_clean(p.text), page=1, is_heading="heading" in (p.style.name or "").lower())
            for p in document.paragraphs
            if _clean(p.text)
        ]
    raw_text = "\n".join(b.text for b in blocks)
    if len(_clean(raw_text)) < 20:
        raise DocumentError("EMPTY_DOCX", "No readable text was found in this document.")
    meta = {"page_count": 1, "ocr_used": False, "warnings": warnings}
    return blocks, meta


def is_heading(text: str, explicit: bool = False) -> bool:
    cleaned = text.strip().strip(":").strip()
    if explicit:
        return True
    normalized = re.sub(r"[^a-z& ]", "", cleaned.lower()).strip()
    if normalized in SECTION_TO_KEY:
        return True
    if len(cleaned) <= 45 and cleaned.isupper() and len(cleaned.split()) <= 4:
        return True
    return False


def normalize_heading(text: str) -> str | None:
    cleaned = re.sub(r"[^a-z& ]", "", text.strip().lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return SECTION_TO_KEY.get(cleaned)


def _split_lines(blocks: list[TextBlock]) -> list[TextBlock]:
    lines: list[TextBlock] = []
    for block in blocks:
        if block.text:
            line = TextBlock(
                text=block.text.strip(),
                page=block.page,
                is_heading=block.is_heading,
                is_bullet=block.is_bullet,
            )
            if line.is_heading or is_heading(line.text, explicit=block.is_heading) or BULLET_RE.match(line.text):
                line.is_bullet = line.is_bullet or bool(BULLET_RE.match(line.text))
                if is_heading(line.text, explicit=block.is_heading):
                    line.is_heading = True
            lines.append(line)
    return lines


def parse_resume(data: bytes, filename: str, mime: str | None = None) -> ParsedDocument:
    safe_name, detected_mime = validate_upload(filename, data, mime)
    if safe_name.lower().endswith(".pdf"):
        blocks, meta = extract_pdf(data)
    else:
        blocks, meta = extract_docx(data)
    content, structure_meta = structure_resume(blocks)
    meta.update(structure_meta)
    meta["filename"] = safe_name
    meta["mime_type"] = detected_mime
    meta["checksum"] = hashlib.sha256(data).hexdigest()
    raw_text = "\n".join(_clean(b.text) for b in blocks if _clean(b.text))
    return ParsedDocument(content=content.model_dump(mode="json"), raw_text=raw_text, meta=meta)


def structure_resume(blocks: list[TextBlock]) -> tuple[ResumeContent, dict[str, Any]]:
    warnings: list[str] = []
    lines = _split_lines(blocks)

    # Build sections keyed by canonical name, plus custom titles.
    sections: list[tuple[str, str, list[TextBlock]]] = []
    current_key = "header"
    current_title = "Header"
    current_lines: list[TextBlock] = []

    for line in lines:
        key = normalize_heading(line.text) if (line.is_heading or is_heading(line.text)) else None
        if key:
            if current_lines:
                sections.append((current_key, current_title, current_lines))
            current_key = key
            current_title = line.text.strip().strip(":").title()
            current_lines = []
            continue
        current_lines.append(line)
    if current_lines:
        sections.append((current_key, current_title, current_lines))

    if len(sections) == 1:
        warnings.append(
            "No clear section headings were detected. Content was placed in summary/experience using layout heuristics."
        )
        # Fallback: first 4 lines header, rest experience.
        header = sections[0][2][:4]
        rest = sections[0][2][4:]
        sections = [("header", "Header", header), ("experience", "Experience", rest)]

    content = ResumeContent()
    text_all = "\n".join(l.text for l in lines)
    meta_sections: dict[str, str] = {}
    unknown_titles: set[str] = set()

    for key, title, section_lines in sections:
        text = "\n".join(l.text for l in section_lines)
        if key == "header":
            _parse_header(content, section_lines)
        elif key == "professional_summary":
            content.professional_summary = " ".join(l.text for l in section_lines).strip()
            meta_sections[key] = title
        elif key == "experience":
            content.experience = _parse_experience(section_lines)
            meta_sections[key] = title
        elif key == "education":
            content.education = _parse_education(section_lines)
            meta_sections[key] = title
        elif key == "skills":
            content.skills = _parse_skills(section_lines)
            meta_sections[key] = title
        elif key == "projects":
            content.projects = _parse_projects(section_lines)
            meta_sections[key] = title
        elif key == "certifications":
            content.certifications = _parse_certifications(section_lines)
            meta_sections[key] = title
        elif key == "languages":
            content.languages = _parse_languages(section_lines)
            meta_sections[key] = title
        elif key == "custom_sections":
            content.custom_sections.append(
                ResumeContent.model_fields["custom_sections"].annotation.__args__[0](
                    title=title, items=[l.text for l in section_lines if l.text]
                )
            )
            meta_sections[f"custom:{title}"] = title
        else:
            if text.strip():
                unknown_titles.add(title)

    # Any leftover unknown sections are preserved, not discarded.
    for title in unknown_titles:
        items = [l.text for l in lines if l.text]
        if items:
            content.custom_sections.append(
                ResumeContent.model_fields["custom_sections"].annotation.__args__[0](
                    title=title, items=items[-5:]
                )
            )

    _dedupe_content(content)
    score = _extraction_confidence(content, text_all)
    if score < 0.35:
        raise DocumentError(
            "NOT_A_RESUME",
            "This document does not look like a resume. Upload a resume with experience, education or skills sections.",
        )
    if content.experience and any(not e.company and not e.role for e in content.experience):
        warnings.append("Some experience entries are missing a company or role. Please correct them in the preview.")
    if not content.experience and not content.education:
        warnings.append("No experience or education entries were detected. Review the extraction manually.")

    meta = {
        "warnings": warnings,
        "section_map": meta_sections,
        "confidence": round(score, 2),
        "detected_sections": [s[0] for s in sections if s[0] != "header"],
    }
    return content, meta


def _parse_header(content: ResumeContent, lines: list[TextBlock]) -> None:
    if not lines:
        return
    texts = [l.text for l in lines]
    content.personal_information.full_name = texts[0][:120]
    if len(texts) > 1 and not EMAIL_RE.search(texts[1]) and len(texts[1]) < 100:
        content.personal_information.headline = texts[1]
    for text in texts:
        email = EMAIL_RE.search(text)
        if email and not content.personal_information.email:
            content.personal_information.email = email.group(0)
        for url in URL_RE.findall(text):
            url = url.rstrip(".,;)")
            if url not in content.personal_information.links:
                content.personal_information.links.append(url[:300])
        phone = PHONE_RE.search(text)
        if phone and not content.personal_information.phone:
            digits = re.sub(r"\D", "", phone.group(0))
            if 10 <= len(digits) <= 15:
                content.personal_information.phone = phone.group(0).strip()
    location_line = next(
        (
            t
            for t in texts[1:]
            if "|" in t and not EMAIL_RE.search(t) and not URL_RE.search(t) and len(t) < 120
        ),
        "",
    )
    if location_line:
        content.personal_information.location = location_line.split("|")[0].strip()


def _parse_experience(lines: list[TextBlock]) -> list[Any]:
    from .schemas import ExperienceItem

    items: list[ExperienceItem] = []
    current: ExperienceItem | None = None
    pending_header: str | None = None

    def flush() -> None:
        nonlocal current
        if current and (current.company or current.role or current.bullets):
            items.append(current)
        current = None

    for line in lines:
        text = BULLET_RE.sub("", line.text).strip()
        if not text:
            continue
        date_match = DATE_RE.search(text)
        looks_bullet = line.is_bullet or bool(BULLET_RE.match(line.text))
        if looks_bullet and current:
            current.bullets.append(text)
            continue
        if date_match and len(text) < 160:
            if current and (current.bullets or current.role):
                flush()
            if pending_header and not current:
                header = pending_header
                pending_header = None
            else:
                header = DATE_RE.sub("", text).strip(" |,-–—") or text
            current = ExperienceItem()
            _split_role_company(current, header)
            current.start_date, current.end_date = _normalize_dates(date_match)
            if current.end_date.lower() in {"present", "current", "now"}:
                current.current = True
            continue
        if current is None:
            pending_header = text
            # A following line often carries dates; if bullets already start, treat header as entry.
            continue
        if not current.bullets and not current.company:
            _split_role_company(current, text)
        elif not current.bullets and not current.role and ROLE_WORDS.search(text):
            current.role = text
        else:
            current.bullets.append(text)
    flush()

    # If dates were never found, chunk on blank-equivalent header transitions.
    if not items or (len(items) == 1 and len(items[0].bullets) > 8):
        items = _chunk_experience_without_dates(lines)
    return items


def _chunk_experience_without_dates(lines: list[TextBlock]) -> list[Any]:
    from .schemas import ExperienceItem

    items: list[ExperienceItem] = []
    current: ExperienceItem | None = None
    for line in lines:
        text = BULLET_RE.sub("", line.text).strip()
        if not text:
            continue
        if current is None:
            current = ExperienceItem()
            _split_role_company(current, text)
        elif current.bullets and not line.is_bullet and (ROLE_WORDS.search(text) or " at " in text) and len(text) < 120:
            items.append(current)
            current = ExperienceItem()
            _split_role_company(current, text)
        else:
            current.bullets.append(text)
    if current:
        items.append(current)
    return items


def _split_role_company(item: Any, text: str) -> None:
    text = text.strip(" |,-–—")
    if not text:
        return
    parts = [p.strip() for p in re.split(r"\s+[|—–]\s+|\s+\bat\b\s+|,\s+", text) if p.strip()]
    if len(parts) >= 2:
        if ROLE_WORDS.search(parts[0]) and not ROLE_WORDS.search(parts[1]):
            item.role, item.company = parts[0], parts[1]
        elif ROLE_WORDS.search(parts[1]):
            item.company, item.role = parts[0], parts[1]
        else:
            item.role, item.company = parts[0], parts[1]
        if len(parts) > 2 and not item.location:
            item.location = parts[2] if not re.search(r"\d", parts[2]) else item.location
    elif ROLE_WORDS.search(text):
        item.role = text
    else:
        item.company = text


def _normalize_dates(match: re.Match[str]) -> tuple[str, str]:
    return match.group(1).strip(), match.group(2).strip()


def _parse_education(lines: list[TextBlock]) -> list[Any]:
    from .schemas import EducationItem

    items: list[EducationItem] = []
    current: EducationItem | None = None
    for line in lines:
        text = BULLET_RE.sub("", line.text).strip()
        if not text:
            continue
        date_match = DATE_RE.search(text)
        if date_match or current is None:
            current = EducationItem()
            body = DATE_RE.sub("", text).strip(" |,-–—") if date_match else text
            parts = [p.strip() for p in re.split(r"\s+[|—–]\s+|,\s+", body) if p.strip()]
            current.institution = parts[0] if parts else body
            if len(parts) > 1:
                current.degree = " — ".join(parts[1:])
            if date_match:
                current.start_date, current.end_date = _normalize_dates(date_match)
            items.append(current)
            continue
        if current and not current.degree and len(text) < 120:
            current.degree = text
        elif current:
            current.details.append(text)
    return items


def _parse_skills(lines: list[TextBlock]) -> list[Any]:
    from .schemas import SkillGroup

    groups: list[SkillGroup] = []
    loose: list[str] = []
    for line in lines:
        text = BULLET_RE.sub("", line.text).strip()
        if not text:
            continue
        if ":" in text and len(text.split(":", 1)[0]) < 40:
            category, values = text.split(":", 1)
            items = _split_terms(values)
            if items:
                groups.append(SkillGroup(category=category.strip().title(), items=items))
                continue
        loose.extend(_split_terms(text))
    if loose:
        # Split into groups of 12 for readability.
        cleaned = list(dict.fromkeys(loose))
        for index in range(0, len(cleaned), 12):
            groups.append(SkillGroup(category="Skills" if index == 0 else "Additional skills", items=cleaned[index:index + 12]))
    return groups


def _split_terms(text: str) -> list[str]:
    terms = re.split(r"[,;|•·]|\s{2,}", text)
    cleaned: list[str] = []
    for term in terms:
        term = term.strip(" .-–—")
        if 1 < len(term) <= 60:
            cleaned.append(term)
    return list(dict.fromkeys(cleaned))


def _parse_projects(lines: list[TextBlock]) -> list[Any]:
    from .schemas import ProjectItem

    projects: list[ProjectItem] = []
    current: ProjectItem | None = None
    for line in lines:
        text = BULLET_RE.sub("", line.text).strip()
        if not text:
            continue
        if current is None or (line.is_heading and len(text) < 80):
            current = ProjectItem(name=text[:120])
            projects.append(current)
        elif line.is_bullet or BULLET_RE.match(line.text):
            current.bullets.append(text)
        elif not current.description:
            techs = [t.strip() for t in re.split(r"[,;|]", text) if t.strip()]
            current.description = text
            if len(techs) >= 2:
                current.technologies = techs[:12]
        else:
            current.bullets.append(text)
    return projects


def _parse_certifications(lines: list[TextBlock]) -> list[Any]:
    from .schemas import CertificationItem

    items: list[CertificationItem] = []
    for line in lines:
        text = BULLET_RE.sub("", line.text).strip(" .")
        if not text:
            continue
        issuer = ""
        if "|" in text:
            text, issuer = [p.strip() for p in text.split("|", 1)]
        date_match = re.search(r"\b(19|20)\d{2}\b", text)
        items.append(CertificationItem(name=text, issuer=issuer, date=date_match.group(0) if date_match else ""))
    return items


def _parse_languages(lines: list[TextBlock]) -> list[Any]:
    from .schemas import LanguageItem

    items: list[LanguageItem] = []
    for line in lines:
        text = BULLET_RE.sub("", line.text).strip(" .")
        if not text:
            continue
        proficiency = ""
        if "(" in text and ")" in text:
            proficiency = text[text.find("(") + 1:text.rfind(")")].strip()
            text = text[:text.find("(")].strip()
        elif " - " in text:
            language, proficiency = [p.strip() for p in text.split(" - ", 1)]
            text = language
        items.append(LanguageItem(language=text[:60], proficiency=proficiency[:60]))
    return items


def _dedupe_content(content: ResumeContent) -> None:
    seen: set[tuple[str, str]] = set()
    for exp in content.experience:
        exp.bullets = list(dict.fromkeys(b.strip() for b in exp.bullets if b.strip()))
        seen.add((exp.company, exp.role))
    content.experience = list({(e.company, e.role, i): e for i, e in enumerate(content.experience)}.values())
    for group in content.skills:
        group.items = list(dict.fromkeys(item.strip() for item in group.items if item.strip()))
    content.skills = [g for g in content.skills if g.items]


def _extraction_confidence(content: ResumeContent, text: str) -> float:
    score = 0.0
    if content.personal_information.full_name:
        score += 0.1
    if content.personal_information.email:
        score += 0.15
    if content.professional_summary:
        score += 0.1
    if content.experience:
        score += 0.3
    if any(e.bullets for e in content.experience):
        score += 0.1
    if content.education:
        score += 0.1
    if content.skills:
        score += 0.1
    if content.projects:
        score += 0.05
    if len(text) > 250:
        score += 0.05
    if len(text) < 120:
        score -= 0.3
    return max(0.0, min(1.0, score))
