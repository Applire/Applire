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

"""
CV templates must render their section headings (and the open-ended date
placeholder) in the document's output language, not hardcoded German (#4,
ADR-038). For each registered template and for lang in ("de", "en"), render
via the production Jinja env and assert the chrome follows the language.
"""
import re
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


ALL_TEMPLATES = [
    "lebenslauf.html.j2",
    "modern_swiss.html.j2",
    "executive.html.j2",
    "academic.html.j2",
    "compact_pro.html.j2",
    "creative_sidebar.html.j2",
    "tech_developer.html.j2",
]

# German heading tokens that must NOT survive an English render.
_GERMAN_TOKENS = ["Berufserfahrung", "Ausbildung"]
# German "present" placeholder that must NOT survive an English render.
_GERMAN_PRESENT = "heute"


@pytest.fixture(scope="module")
def sample_cv():
    from applire.schemas.cv import (
        TailoredCVData, TailoredContact, TailoredWorkEntry,
        TailoredEducationEntry, TailoredLanguage,
    )
    return TailoredCVData(
        contact=TailoredContact(
            name="Anna Musterfrau",
            email="anna@example.com",
            phone="+49 89 123456",
            location="München",
            linkedin="linkedin.com/in/anna",
            photo_url=None,
        ),
        summary="Erfahrene Managerin mit Fokus auf digitale Transformation.",
        work_history=[
            TailoredWorkEntry(
                company="Beispiel GmbH",
                role="Head of Product",
                start_date="2020",
                end_date=None,  # open-ended → `present`/`heute` must render
                bullets=["Aufbau des Produktteams", "Einführung agiler Methoden"],
            )
        ],
        skills=["Python", "Agile", "Stakeholder Management"],
        education=[
            TailoredEducationEntry(
                institution="LMU München",
                degree="MBA",
                field="Betriebswirtschaft",
                start_date="2014",
                end_date="2016",
            )
        ],
        languages=[TailoredLanguage(language="Deutsch", level="Muttersprache")],
        show_photo=False,
    )


@pytest.fixture(scope="module")
def color_ctx():
    from applire.services.color_detection import _make_color_context
    return _make_color_context("#2b5fa8")


def _title(html: str) -> str | None:
    """The rendered `<title>` — the PDF's metadata title (#604)."""
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return m.group(1).strip() if m else None


def _render(template_file, cv, color, lang):
    from applire.services.cv import _jinja_env
    from applire.templates.labels import cv_labels

    template = _jinja_env.get_template(template_file)
    return template.render(cv=cv, color=color, lang=lang, labels=cv_labels(lang))


@pytest.mark.parametrize("template_file", ALL_TEMPLATES)
def test_english_render_uses_english_headings(template_file, sample_cv, color_ctx):
    html = _render(template_file, sample_cv, color_ctx, "en")

    # English headings present (case-insensitive — tech_developer lowercases).
    lowered = html.lower()
    assert "experience" in lowered, f"{template_file}: 'Experience' heading missing in EN render"
    assert "education" in lowered, f"{template_file}: 'Education' heading missing in EN render"

    # No German heading leakage.
    for token in _GERMAN_TOKENS:
        assert token not in html, f"{template_file}: German heading '{token}' leaked into EN render"

    # Open-ended date placeholder follows EN ("Present"), never German "heute".
    assert "Present" in html, f"{template_file}: EN present-placeholder missing in EN render"
    assert _GERMAN_PRESENT not in html, f"{template_file}: German 'heute' leaked into EN render"

    # #604 — the PDF's metadata title is chrome too, and it was the one piece
    # this file did not examine: every template hardcoded its own word, so an
    # English CV from the Classic template shipped as "Lebenslauf – …"
    # (edge UAT 2026-08-29, CV 4121fa73). Asserting only on headings is why
    # the defect survived a passing localization suite.
    assert _title(html) == "Curriculum Vitae – Anna Musterfrau", (
        f"{template_file}: EN metadata title is {_title(html)!r}"
    )


@pytest.mark.parametrize("template_file", ALL_TEMPLATES)
def test_german_render_uses_german_headings(template_file, sample_cv, color_ctx):
    html = _render(template_file, sample_cv, color_ctx, "de")

    # German headings present (case-insensitive — tech_developer lowercases).
    lowered = html.lower()
    assert "berufserfahrung" in lowered, f"{template_file}: 'Berufserfahrung' heading missing in DE render"
    assert "ausbildung" in lowered, f"{template_file}: 'Ausbildung' heading missing in DE render"

    # No English heading leakage (whole-word, to avoid CSS class false positives).
    assert "Experience" not in html, f"{template_file}: English 'Experience' leaked into DE render"
    assert "Education" not in html, f"{template_file}: English 'Education' leaked into DE render"

    # Open-ended date placeholder follows DE ("heute"), never English "Present".
    assert _GERMAN_PRESENT in html, f"{template_file}: German present-placeholder missing in DE render"
    assert "Present" not in html, f"{template_file}: English 'Present' leaked into DE render"

    # #604 — see the EN counterpart. The DE direction is the one that was
    # already correct; it is pinned so the fix cannot regress into the
    # opposite leak.
    assert _title(html) == "Lebenslauf – Anna Musterfrau", (
        f"{template_file}: DE metadata title is {_title(html)!r}"
    )


# ---------------------------------------------------------------------------
# Cover-letter chrome: the subject prefix + <html lang> follow the document's
# output language, not a hardcoded German "Bewerbung" (#4, ADR-038). For each
# registered cover-letter template and lang in ("de", "en"), render via the
# production cover-letter Jinja env and assert the subject prefix follows the
# language.
# ---------------------------------------------------------------------------

ALL_LETTER_TEMPLATES = [
    "academic_letter.html.j2",
    "compact_pro_letter.html.j2",
    "creative_sidebar_letter.html.j2",
    "executive_letter.html.j2",
    "lebenslauf_letter.html.j2",
    "modern_swiss_letter.html.j2",
    "tech_developer_letter.html.j2",
]


@pytest.fixture(scope="module")
def sample_letter():
    # The templates resolve `letter.recipient.company` etc. via Jinja attribute
    # access, which falls back to dict-key lookup — so a plain dict suffices.
    return {
        "header": {
            "name": "Anna Musterfrau",
            "address": "Musterstraße 1, München",
            "phone": "+49 89 123456",
            "email": "anna@example.com",
        },
        "recipient": {
            "name": "Dr. Müller",
            "title": "Head of Talent",
            "company": "Beispiel GmbH",
            "address": "Hauptstraße 2, Berlin",
            "date": "13. Juni 2026",
        },
        "subject": "",
        "body": {"paragraphs": ["Erster Absatz.", "Zweiter Absatz."]},
        "signature": {"closing": "Mit freundlichen Grüßen", "name": "Anna Musterfrau"},
    }


def _render_letter(template_file, letter, color, lang, role_title=None):
    from applire.services.cover_letter import _jinja_env
    from applire.templates.labels import cover_letter_labels

    labels = cover_letter_labels(lang)
    # F3 (blind PQ blocker): `subject` is computed by the service (cover_letter.py
    # get_cover_letter_html) from JobAnalysis.role_title, not stored on letter_data —
    # mirror that computation here so this standalone-render test still exercises the
    # real subject-prefix-follows-language contract (AC #3 adds the role on top).
    subject = f"{labels['subject_prefix']}: {role_title}" if role_title else labels["subject_prefix"]
    template = _jinja_env.get_template(template_file)
    return template.render(
        letter=letter, color=color, lang=lang, labels=labels, subject=subject
    )


@pytest.mark.parametrize("template_file", ALL_LETTER_TEMPLATES)
def test_english_letter_subject_uses_english_prefix(template_file, sample_letter, color_ctx):
    html = _render_letter(template_file, sample_letter, color_ctx, "en")
    assert "Application" in html, f"{template_file}: EN subject prefix 'Application' missing in EN render"
    assert "Bewerbung" not in html, f"{template_file}: German 'Bewerbung' leaked into EN render"
    assert 'lang="en"' in html, f"{template_file}: <html lang> did not follow EN output language"


@pytest.mark.parametrize("template_file", ALL_LETTER_TEMPLATES)
def test_german_letter_subject_uses_german_prefix(template_file, sample_letter, color_ctx):
    html = _render_letter(template_file, sample_letter, color_ctx, "de")
    assert "Bewerbung" in html, f"{template_file}: DE subject prefix 'Bewerbung' missing in DE render"
    assert "Application" not in html, f"{template_file}: English 'Application' leaked into DE render"
    assert 'lang="de"' in html, f"{template_file}: <html lang> did not follow DE output language"
