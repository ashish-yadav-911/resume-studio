"""Resume export to editable DOCX and PDF.

Exports are generated from an immutable stored version; they never mutate state.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def _date_range(item: dict[str, Any]) -> str:
    start = (item.get("start_date") or "").strip()
    end = (item.get("end_date") or "").strip()
    if item.get("current") and not end:
        end = "Present"
    return " – ".join(part for part in [start, end] if part)


def _safe_filename(title: str, extension: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]", "", title or "resume").strip().replace(" ", "_")[:80]
    return f"{stem or 'resume'}.{extension}"


def build_docx(content: dict[str, Any], title: str = "Resume") -> bytes:
    document = Document()
    styles = document.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    for section in document.sections:
        section.top_margin = Pt(36)
        section.bottom_margin = Pt(36)
        section.left_margin = Pt(42)
        section.right_margin = Pt(42)

    personal = content.get("personal_information", {}) or {}
    name_paragraph = document.add_paragraph()
    name_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = name_paragraph.add_run(personal.get("full_name") or title)
    run.bold = True
    run.font.size = Pt(22)
    run.font.color.rgb = RGBColor(0x11, 0x18, 0x27)

    if personal.get("headline"):
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(personal["headline"])
        r.italic = True
        r.font.size = Pt(11)

    contact = " | ".join(
        part for part in [
            personal.get("email"), personal.get("phone"), personal.get("location"),
        ] if part
    )
    if contact:
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(contact).font.size = Pt(9)
    links = [link for link in personal.get("links", []) or [] if link]
    if links:
        p = document.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run(" | ".join(links)).font.size = Pt(9)

    def heading(text: str) -> None:
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(12)
        paragraph.paragraph_format.space_after = Pt(4)
        run = paragraph.add_run(text.upper())
        run.bold = True
        run.font.size = Pt(12)
        run.font.color.rgb = RGBColor(0x0F, 0x76, 0x6E)

    def bullet(text: str) -> None:
        document.add_paragraph(text, style="List Bullet")

    if content.get("professional_summary"):
        heading("Professional Summary")
        document.add_paragraph(content["professional_summary"])

    experience = content.get("experience", []) or []
    if experience:
        heading("Experience")
        for item in experience:
            role = item.get("role") or item.get("company") or "Role"
            company = item.get("company") or ""
            dates = _date_range(item)
            p = document.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(1)
            r = p.add_run(role)
            r.bold = True
            meta = " · ".join(part for part in [company, item.get("location", ""), dates] if part)
            if meta:
                p.add_run(" — " + meta).font.size = Pt(10)
            for text in item.get("bullets", []) or []:
                bullet(str(text))

    projects = content.get("projects", []) or []
    if projects:
        heading("Projects")
        for project in projects:
            p = document.add_paragraph()
            p.paragraph_format.space_before = Pt(4)
            r = p.add_run(project.get("name") or "Project")
            r.bold = True
            if project.get("link"):
                p.add_run(" — " + project["link"]).font.size = Pt(9)
            if project.get("description"):
                document.add_paragraph(project["description"])
            for text in project.get("bullets", []) or []:
                bullet(str(text))
            if project.get("technologies"):
                p = document.add_paragraph()
                p.add_run("Technologies: " + ", ".join(project["technologies"])).italic = True

    education = content.get("education", []) or []
    if education:
        heading("Education")
        for item in education:
            p = document.add_paragraph()
            r = p.add_run(item.get("institution") or "Institution")
            r.bold = True
            detail = " · ".join(
                part for part in [item.get("degree", ""), item.get("field_of_study", ""), item.get("location", ""), _date_range(item)]
                if part
            )
            if detail:
                document.add_paragraph(detail)
            for value in item.get("details", []) or []:
                bullet(str(value))

    skills = content.get("skills", []) or []
    if skills:
        heading("Skills")
        for group in skills:
            p = document.add_paragraph()
            r = p.add_run((group.get("category") or "Skills") + ": ")
            r.bold = True
            p.add_run(", ".join(group.get("items", []) or []))

    certifications = content.get("certifications", []) or []
    if certifications:
        heading("Certifications")
        for item in certifications:
            text = item.get("name") or "Certification"
            if item.get("issuer"):
                text += f" — {item['issuer']}"
            if item.get("date"):
                text += f" ({item['date']})"
            bullet(text)

    languages = content.get("languages", []) or []
    if languages:
        heading("Languages")
        for item in languages:
            text = item.get("language") or ""
            if item.get("proficiency"):
                text += f" ({item['proficiency']})"
            bullet(text)

    for section in content.get("custom_sections", []) or []:
        if section.get("title") and section.get("items"):
            heading(section["title"])
            for item in section["items"]:
                bullet(str(item))

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


_PDF_FONT = "Helvetica"
_PDF_BOLD = "Helvetica-Bold"


def _register_fonts() -> None:
    global _PDF_FONT, _PDF_BOLD
    candidates = [
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (Path("C:/Windows/Fonts/calibri.ttf"), Path("C:/Windows/Fonts/calibrib.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            try:
                pdfmetrics.registerFont(TTFont("ResumeSans", str(regular)))
                pdfmetrics.registerFont(TTFont("ResumeSans-Bold", str(bold)))
                _PDF_FONT, _PDF_BOLD = "ResumeSans", "ResumeSans-Bold"
                return
            except Exception:
                continue


def build_pdf(content: dict[str, Any], title: str = "Resume") -> bytes:
    _register_fonts()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=title,
    )
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "ResumeBody", parent=base["BodyText"], fontName=_PDF_FONT, fontSize=9.5, leading=12.5,
        spaceAfter=2,
    )
    heading_style = ParagraphStyle(
        "ResumeHeading", parent=base["Heading2"], fontName=_PDF_BOLD, fontSize=11.5,
        textColor="#0f766e", spaceBefore=9, spaceAfter=3,
    )
    name_style = ParagraphStyle(
        "ResumeName", parent=base["Title"], fontName=_PDF_BOLD, fontSize=20, alignment=1,
        spaceAfter=3,
    )
    center = ParagraphStyle("Center", parent=body, alignment=1, fontSize=9)
    bullet_style = ParagraphStyle("ResumeBullet", parent=body, leftIndent=10, bulletIndent=2, spaceAfter=2)

    def p(text: Any, style: ParagraphStyle = body) -> Paragraph:
        return Paragraph(escape(str(text or "")), style)

    story: list[Any] = []
    personal = content.get("personal_information", {}) or {}
    story.append(p(personal.get("full_name") or title, name_style))
    if personal.get("headline"):
        story.append(p(personal["headline"], center))
    contact = " | ".join(x for x in [personal.get("email"), personal.get("phone"), personal.get("location")] if x)
    if contact:
        story.append(p(contact, center))
    if personal.get("links"):
        story.append(p(" | ".join(personal["links"]), center))

    def section(title_text: str) -> None:
        story.append(Spacer(1, 3))
        story.append(p(title_text.upper(), heading_style))

    def bullets(values: list[str]) -> None:
        for value in values:
            story.append(Paragraph(escape(str(value)), bullet_style, bulletText="•"))

    if content.get("professional_summary"):
        section("Professional Summary")
        story.append(p(content["professional_summary"]))

    if content.get("experience"):
        section("Experience")
        for item in content["experience"]:
            role = item.get("role") or item.get("company") or "Role"
            meta = " · ".join(
                part for part in [item.get("company", ""), item.get("location", ""), _date_range(item)] if part
            )
            story.append(Paragraph(f"<b>{escape(role)}</b>" + (f" — {escape(meta)}" if meta else ""), body))
            bullets(item.get("bullets", []) or [])

    if content.get("projects"):
        section("Projects")
        for project in content["projects"]:
            story.append(Paragraph(f"<b>{escape(project.get('name') or 'Project')}</b>", body))
            if project.get("description"):
                story.append(p(project["description"]))
            bullets(project.get("bullets", []) or [])
            if project.get("technologies"):
                story.append(Paragraph(f"<i>Technologies: {escape(', '.join(project['technologies']))}</i>", body))

    if content.get("education"):
        section("Education")
        for item in content["education"]:
            detail = " · ".join(
                part for part in [item.get("degree", ""), item.get("field_of_study", ""), item.get("location", ""), _date_range(item)]
                if part
            )
            story.append(Paragraph(f"<b>{escape(item.get('institution') or 'Institution')}</b>", body))
            if detail:
                story.append(p(detail))
            bullets(item.get("details", []) or [])

    if content.get("skills"):
        section("Skills")
        for group in content["skills"]:
            story.append(Paragraph(
                f"<b>{escape(group.get('category') or 'Skills')}:</b> {escape(', '.join(group.get('items', []) or []))}",
                body,
            ))

    if content.get("certifications"):
        section("Certifications")
        for item in content["certifications"]:
            text = item.get("name") or "Certification"
            if item.get("issuer"):
                text += f" — {item['issuer']}"
            if item.get("date"):
                text += f" ({item['date']})"
            bullets([text])

    if content.get("languages"):
        section("Languages")
        bullets([
            (item.get("language") or "") + (f" ({item['proficiency']})" if item.get("proficiency") else "")
            for item in content["languages"]
        ])

    for custom in content.get("custom_sections", []) or []:
        if custom.get("title") and custom.get("items"):
            section(custom["title"])
            bullets(custom["items"])

    doc.build(story)
    return buffer.getvalue()


def export_filename(title: str, fmt: str) -> str:
    return _safe_filename(title, "docx" if fmt == "docx" else "pdf")
