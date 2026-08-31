# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""The direct ``python-docx`` CV writer (ADR-079, E057, US296).

``render_cv_docx`` is a **pure function**: no DB, no storage, no network —
``TailoredCVData`` plus a resolved accent colour and (optionally) resolved
photo bytes go in, ``.docx`` bytes come out. The caller (``services.cv``)
does all I/O before calling it, mirroring how ``get_cv_pdf`` composes
``get_cv_html`` — the export is rendered on demand from ``tailored_data`` and
no bytes are persisted (ADR-079 clause 8).

No HTML, no template engine, no subprocess anywhere in this module (ADR-079
clause 2): the section content is composed directly with the ``_common``
paragraph helpers.

**Section coverage is schema-driven, not a hand-written call sequence.**
``render_cv_docx`` iterates ``TailoredCVData.model_fields`` and dispatches
each field to a registered renderer in ``_SECTION_RENDERERS``; a field with
no registered renderer raises rather than being silently skipped. This is
deliberate: a hand-written list of "the sections to render" is exactly how a
new schema field goes missing from the export without anyone noticing.
"""

import io
from typing import Callable

from docx.document import Document as DocxDocument
from docx.shared import Cm, RGBColor

from applire.schemas.cv import (
    TailoredCVData,
    TailoredCertification,
    TailoredEducationEntry,
    TailoredLanguage,
    TailoredProjectEntry,
    TailoredWorkEntry,
)
from applire.services.office_export._common import (
    add_bullet,
    add_heading,
    add_paragraph,
    hex_to_rgb_color,
    new_document,
)
from applire.templates.labels import cv_labels

PHOTO_WIDTH = Cm(3.2)

_DASH = " – "  # en dash, date ranges
_JOIN = " — "  # em dash, e.g. "Company — Role"


def _has_text(*values: str | None) -> bool:
    """True if at least one of `values` is non-blank after stripping."""
    return any(v and v.strip() for v in values)


def _join_nonblank(parts: list[str | None], sep: str = _JOIN) -> str:
    return sep.join(p.strip() for p in parts if p and p.strip())


def _format_date_range(start: str | None, end: str | None, labels: dict) -> str:
    """'2020-01 – 2022-06', '2022-07 – heute'/'Present' (end is None but
    start is set), or '' when both are blank. Never renders the literal
    string 'None'."""
    start = (start or "").strip()
    end = (end or "").strip()
    if not start and not end:
        return ""
    if not end:
        end = labels["present"]
    if start and end:
        return f"{start}{_DASH}{end}"
    return start or end


def _project_has_content(project: TailoredProjectEntry) -> bool:
    return _has_text(project.name) or any(_has_text(b) for b in project.bullets)


def _work_entry_has_content(entry: TailoredWorkEntry) -> bool:
    if _has_text(entry.company, entry.role, entry.start_date, entry.end_date):
        return True
    if any(_has_text(b) for b in entry.bullets):
        return True
    return any(_project_has_content(p) for p in entry.projects)


def _education_has_content(entry: TailoredEducationEntry) -> bool:
    return _has_text(entry.institution, entry.degree, entry.field, entry.start_date, entry.end_date)


def _language_has_content(entry: TailoredLanguage) -> bool:
    return _has_text(entry.language, entry.level)


def _certification_has_content(cert: TailoredCertification) -> bool:
    return _has_text(cert.name, cert.issuing_organization, cert.date_obtained, cert.expiry_date)


def _render_project(document: DocxDocument, project: TailoredProjectEntry) -> None:
    add_paragraph(document, project.name, bold=True)
    for bullet in project.bullets:
        add_bullet(document, bullet)


# ---------------------------------------------------------------------------
# Section renderers — one per TailoredCVData field. Every renderer takes the
# same five arguments so the dispatch loop below stays uniform; renderers
# that don't need `photo_bytes` simply ignore it.
# ---------------------------------------------------------------------------


def _render_contact(document, tailored, labels, color, photo_bytes) -> None:
    contact = tailored.contact
    if photo_bytes:
        document.add_picture(io.BytesIO(photo_bytes), width=PHOTO_WIDTH)
    add_heading(document, contact.name, 1, color)
    details = _join_nonblank(
        [contact.location, contact.phone, contact.email, contact.linkedin],
        sep="   |   ",
    )
    add_paragraph(document, details)


def _render_summary(document, tailored, labels, color, photo_bytes) -> None:
    if not _has_text(tailored.summary):
        return
    add_heading(document, labels["summary"], 2, color)
    add_paragraph(document, tailored.summary)


def _render_work_history(document, tailored, labels, color, photo_bytes) -> None:
    if not any(_work_entry_has_content(e) for e in tailored.work_history):
        return
    add_heading(document, labels["experience"], 2, color)
    for entry in tailored.work_history:
        if not _work_entry_has_content(entry):
            continue
        header = _join_nonblank([entry.company, entry.role])
        add_heading(document, header, 3, color)
        add_paragraph(document, _format_date_range(entry.start_date, entry.end_date, labels), italic=True)
        for bullet in entry.bullets:
            add_bullet(document, bullet)
        for project in entry.projects:
            _render_project(document, project)


def _render_skills(document, tailored, labels, color, photo_bytes) -> None:
    if not any(_has_text(s) for s in tailored.skills):
        return
    add_heading(document, labels["skills"], 2, color)
    for skill in tailored.skills:
        add_bullet(document, skill)


def _render_education(document, tailored, labels, color, photo_bytes) -> None:
    if not any(_education_has_content(e) for e in tailored.education):
        return
    add_heading(document, labels["education"], 2, color)
    for entry in tailored.education:
        if not _education_has_content(entry):
            continue
        header = _join_nonblank([entry.institution, entry.degree, entry.field])
        add_paragraph(document, header, bold=True)
        add_paragraph(document, _format_date_range(entry.start_date, entry.end_date, labels), italic=True)


def _render_languages(document, tailored, labels, color, photo_bytes) -> None:
    if not any(_language_has_content(e) for e in tailored.languages):
        return
    add_heading(document, labels["languages"], 2, color)
    for entry in tailored.languages:
        add_bullet(document, _join_nonblank([entry.language, entry.level]))


def _render_projects(document, tailored, labels, color, photo_bytes) -> None:
    if not any(_project_has_content(p) for p in tailored.projects):
        return
    add_heading(document, labels["projects"], 2, color)
    for project in tailored.projects:
        _render_project(document, project)


def _render_certifications(document, tailored, labels, color, photo_bytes) -> None:
    if not any(_certification_has_content(c) for c in tailored.certifications):
        return
    add_heading(document, labels["certifications"], 2, color)
    for cert in tailored.certifications:
        dates = _join_nonblank([cert.date_obtained, cert.expiry_date], sep=_DASH)
        add_bullet(document, _join_nonblank([cert.name, cert.issuing_organization, dates]))


_SECTION_RENDERERS: dict[str, Callable] = {
    "contact": _render_contact,
    "summary": _render_summary,
    "work_history": _render_work_history,
    "skills": _render_skills,
    "education": _render_education,
    "languages": _render_languages,
    "projects": _render_projects,
    "certifications": _render_certifications,
}

# `show_photo` is a boolean modifier of `contact` (whether the caller-resolved
# photo is embedded there), never a section of its own — the caller already
# gates `photo_bytes` on `show_photo` before calling render_cv_docx.
_NON_SECTION_FIELDS: frozenset[str] = frozenset({"show_photo"})


def render_cv_docx(
    tailored: TailoredCVData,
    *,
    lang: str,
    accent_color: str,
    photo_bytes: bytes | None = None,
) -> bytes:
    """Render `tailored` as a `.docx` file, returned as bytes.

    Pure function — no I/O. `photo_bytes` is the already-resolved photo (or
    None if absent/unreadable/not shown); the caller decides whether to
    resolve and pass it based on `tailored.show_photo`.
    """
    labels = cv_labels(lang)
    document = new_document()
    color: RGBColor = hex_to_rgb_color(accent_color)

    for field_name in TailoredCVData.model_fields:
        if field_name in _NON_SECTION_FIELDS:
            continue
        renderer = _SECTION_RENDERERS.get(field_name)
        if renderer is None:
            raise RuntimeError(
                f"office_export.cv_docx: TailoredCVData field {field_name!r} has no "
                "registered renderer in _SECTION_RENDERERS — a schema field would "
                "silently go unexported from the .docx writer."
            )
        renderer(document, tailored, labels, color, photo_bytes)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
