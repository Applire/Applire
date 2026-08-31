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

"""#634 — free text containing angle brackets is silently deleted from the
delivered document.

``build_template_env`` constructs the one shared Jinja environment with
``autoescape=select_autoescape(["html"])``. Jinja's ``select_autoescape``
matches on the template *filename suffix*; every Applire template is named
``*.html.j2``, so **no** template ever had autoescape enabled. A bullet reading
``Koordination mit <Projekt Phoenix> und R&D-Teams`` is emitted into the HTML
verbatim, Chromium parses ``<Projekt Phoenix>`` as an unknown start tag and
drops it, and the candidate's PDF ships without it.

The ADR-039 audit reported **zero failures** on exactly that document, because
``_audit_cv_text`` never reads bullet or summary text at all — it checks contact
fields, per-position company/role/year, institution/degree and skill names. Its
silence was never evidence about bullets (see the FMEA note on SF-PDF.2's
``D=1``).

These tests drive the real path — the shipped ``_jinja_env``, the real Chromium
``_html_to_pdf``, the real ADR-039 ``extract_text`` — for **every** shipped
template, because the defect lives in the shared environment and therefore in
all fourteen at once.

Both directions are pinned:

* the bracketed phrase must **survive** into the delivered PDF, and
* an ampersand must arrive as ``R&D`` — never as a literal ``&amp;``, which is
  how an over-eager fix (escaping an already-escaped string) would fail.
"""
import pytest

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import _norm, extract_text
from applire.services.color_detection import _default_context
from applire.services.cover_letter import _TEMPLATE_FILES as LETTER_TEMPLATES
from applire.services.cover_letter import _default_color_context
from applire.services.cv import _TEMPLATE_FILES as CV_TEMPLATES
from applire.services.cv import _html_to_pdf, _jinja_env
from applire.templates.labels import cover_letter_labels, cv_labels

# The reported bullet, verbatim from the #634 reproduction.
BRACKET_BULLET = "Koordination mit <Projekt Phoenix> und R&D-Teams beim Rollout."
BRACKET_SUMMARY = (
    "Projektleiter mit Schwerpunkt <Digitale Fertigung> und Verantwortung "
    "für Standardisierung."
)
BRACKET_LETTER_PARAGRAPH = (
    "In meiner aktuellen Rolle verantworte ich <Projekt Phoenix> sowie die "
    "Zusammenarbeit mit den R&D-Teams beider Standorte."
)

CV_FIXTURE = TailoredCVData.model_validate(
    {
        "contact": {
            "name": "Jörg Müller",
            "email": "joerg.mueller@example.de",
            "phone": "+49 89 1234567",
            "location": "München",
            "photo_url": None,
        },
        "show_photo": False,
        "summary": BRACKET_SUMMARY,
        "work_history": [
            {
                "company": "Süddeutsche Präzisionstechnik GmbH",
                "role": "Teamleiter Qualitätssicherung",
                "start_date": "2018-03",
                "end_date": None,
                "bullets": [
                    BRACKET_BULLET,
                    "Reduktion der Ausschussquote um 23 % durch statistische Prozesslenkung.",
                ],
            }
        ],
        "skills": ["Python", "Projektmanagement"],
        "education": [
            {
                "institution": "Technische Universität München",
                "degree": "Dipl.-Ing.",
                "field": "Maschinenbau",
                "start_date": "2006-10",
                "end_date": "2011-03",
            }
        ],
    }
)

LETTER_FIXTURE = {
    "header": {
        "name": "Jörg Müller",
        "address": "Beispielstraße 1, 80331 München",
        "phone": "+49 89 1234567",
        "email": "joerg.mueller@example.de",
        "photo_url": None,
    },
    "recipient": {
        "name": "Frau Dr. Sabine Vogt",
        "title": "Leiterin Personalentwicklung",
        "company": "Nordwerk Systeme GmbH",
        "address": "Hafenstraße 12, 20457 Hamburg",
        "date": "15. Mai 2026",
    },
    "body": {
        "paragraphs": [
            BRACKET_LETTER_PARAGRAPH,
            (
                "Die ausgeschriebene Aufgabe verbindet genau die Felder, in denen ich "
                "seit über zehn Jahren arbeite: Prozessoptimierung, Normenarbeit und "
                "die Führung interdisziplinärer Teams."
            ),
            (
                "Über die Gelegenheit zu einem persönlichen Gespräch würde ich mich "
                "sehr freuen und stehe für Rückfragen jederzeit zur Verfügung."
            ),
        ]
    },
    "signature": {"closing": "Mit freundlichen Grüßen", "name": "Jörg Müller"},
}


async def _cv_pdf_text(template: str) -> str:
    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=CV_FIXTURE, color=_default_context(), lang="de", labels=cv_labels("de")
    )
    return extract_text(await _html_to_pdf(html))


async def _letter_pdf_text(template: str) -> str:
    html = _jinja_env.get_template(LETTER_TEMPLATES[template]).render(
        letter=LETTER_FIXTURE,
        color=_default_color_context(),
        lang="de",
        labels=cover_letter_labels("de"),
    )
    return extract_text(await _html_to_pdf(html))


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_bracketed_phrase_in_cv_bullet_survives_to_the_pdf(template):
    """#634 — the reported loss. ``<Projekt Phoenix>`` must reach the PDF."""
    text = _norm(await _cv_pdf_text(template))

    assert _norm(BRACKET_BULLET) in text, (
        f"{template}: the bullet did not survive intact — "
        f"'<Projekt Phoenix>' is eaten as an unknown HTML tag"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_bracketed_phrase_in_cv_summary_survives_to_the_pdf(template):
    """The summary is the second free-text surface the ADR-039 audit never reads."""
    text = _norm(await _cv_pdf_text(template))

    assert _norm(BRACKET_SUMMARY) in text, (
        f"{template}: the summary did not survive intact — "
        f"'<Digitale Fertigung>' is eaten as an unknown HTML tag"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(LETTER_TEMPLATES))
async def test_bracketed_phrase_in_letter_paragraph_survives_to_the_pdf(template):
    """The same shared environment renders the seven letter templates."""
    text = _norm(await _letter_pdf_text(template))

    assert _norm(BRACKET_LETTER_PARAGRAPH) in text, (
        f"{template}: the letter paragraph did not survive intact — "
        f"'<Projekt Phoenix>' is eaten as an unknown HTML tag"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_ampersand_is_not_double_escaped_in_the_cv_pdf(template):
    """The other direction: escaping must not leak entity syntax into the document.

    Fails if a fix escapes an already-escaped string (``&amp;amp;``) or if the
    template author reaches for ``| safe`` and hand-escapes instead.
    """
    raw = await _cv_pdf_text(template)

    assert "R&D" in raw, f"{template}: 'R&D' not found in the delivered text"
    assert "&amp;" not in raw, f"{template}: entity syntax leaked into the PDF"
