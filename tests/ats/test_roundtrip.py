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

"""ADR-039 / US141 — ATS extraction round-trip guarantee.

Every shipped CV and cover-letter template is rendered to a real PDF via
Playwright Chromium and the extracted text is audited. A template only ships
if its layout survives the local extraction audit (no column interleaving,
no CSS-generated headings, real text). This file IS the enforcement of
ADR-039: it is a blocking quality gate, not a smoke test.
"""
import pytest

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import audit_cover_letter, audit_cv
from applire.services.color_detection import _default_context
from applire.services.cover_letter import _TEMPLATE_FILES as LETTER_TEMPLATES
from applire.services.cover_letter import _default_color_context
from applire.services.cv import _TEMPLATE_FILES as CV_TEMPLATES
from applire.services.cv import _html_to_pdf, _jinja_env

KEYWORDS = ["Python", "Kubernetes", "Projektmanagement"]

# ---------------------------------------------------------------------------
# CV fixtures — realistic DACH content with umlauts/ß, no photo file so the
# photo block is skipped (show_photo False AND photo_url None).
# ---------------------------------------------------------------------------

CV_DE = TailoredCVData.model_validate(
    {
        "contact": {
            "name": "Jörg Müller-Lüdenscheidt",
            "email": "joerg.mueller@example.de",
            "phone": "+49 89 1234567",
            "location": "München",
            "linkedin": "linkedin.com/in/joergmueller",
            "photo_url": None,
        },
        "show_photo": False,
        "summary": (
            "Erfahrener Qualitätsingenieur mit über zwölf Jahren Verantwortung "
            "für Prozessoptimierung und Projektmanagement in der Präzisionsfertigung."
        ),
        "work_history": [
            {
                "company": "Süddeutsche Präzisionstechnik GmbH",
                "role": "Teamleiter Qualitätssicherung",
                "start_date": "2018-03",
                "end_date": None,
                "bullets": [
                    "Leitung eines Teams von acht Prüfingenieuren über drei Standorte hinweg.",
                    "Einführung eines KPI-gestützten Projektmanagements zur Prozessoptimierung.",
                    "Reduktion der Ausschussquote um 23 % durch statistische Prozesslenkung.",
                ],
            },
            {
                "company": "Bayerische Werkzeugbau AG",
                "role": "Qualitätsingenieur",
                "start_date": "2013-09",
                "end_date": "2018-02",
                "bullets": [
                    "Verantwortung für Erstmusterprüfberichte nach VDA-Standard.",
                    "Aufbau eines automatisierten Messdaten-Workflows in Python.",
                ],
            },
            {
                "company": "Technik & Söhne KG",
                "role": "Werkstudent Fertigungstechnik",
                "start_date": "2011-04",
                "end_date": "2013-08",
                "bullets": [
                    "Unterstützung bei der Einführung eines neuen Prüfmittelmanagements.",
                ],
            },
        ],
        "skills": [
            "Python",
            "Kubernetes",
            "Projektmanagement",
            "Prozessoptimierung",
            "Six Sigma",
            "VDA 6.3",
            "Messtechnik",
            "Statistische Prozesslenkung",
        ],
        "education": [
            {
                "institution": "Technische Universität München",
                "degree": "Dipl.-Ing.",
                "field": "Maschinenbau",
                "start_date": "2006-10",
                "end_date": "2011-03",
            },
            {
                "institution": "Hochschule Augsburg",
                "degree": "Vordiplom",
                "field": "Fertigungstechnik",
                "start_date": "2004-10",
                "end_date": "2006-09",
            },
        ],
        "languages": [
            {"language": "Deutsch", "level": "Muttersprache"},
            {"language": "Englisch", "level": "C1"},
        ],
    }
)

CV_EN = TailoredCVData.model_validate(
    {
        "contact": {
            "name": "Catherine O'Brien",
            "email": "catherine.obrien@example.com",
            "phone": "+44 20 7946 0958",
            "location": "Zürich",
            "linkedin": "linkedin.com/in/catherineobrien",
            "photo_url": None,
        },
        "show_photo": False,
        "summary": (
            "Platform engineer with a decade of experience building resilient "
            "cloud infrastructure and leading cross-functional delivery teams."
        ),
        "work_history": [
            {
                "company": "Müller & Söhne AG",
                "role": "Lead Platform Engineer",
                "start_date": "2019-06",
                "end_date": None,
                "bullets": [
                    "Owned the migration of 40+ services onto a managed Kubernetes platform.",
                    "Introduced infrastructure-as-code, cutting environment setup from days to minutes.",
                    "Mentored four engineers and ran the on-call reliability programme.",
                ],
            },
            {
                "company": "Northbridge Analytics Ltd",
                "role": "Senior Software Engineer",
                "start_date": "2015-01",
                "end_date": "2019-05",
                "bullets": [
                    "Built a real-time ingestion pipeline in Python handling 2M events per hour.",
                    "Designed the service-level objectives adopted across the data org.",
                ],
            },
            {
                "company": "Greenfield Systems",
                "role": "Software Engineer",
                "start_date": "2012-08",
                "end_date": "2014-12",
                "bullets": [
                    "Delivered the first customer-facing reporting API for the flagship product.",
                ],
            },
        ],
        "skills": [
            "Python",
            "Kubernetes",
            "Projektmanagement",
            "Terraform",
            "PostgreSQL",
            "Observability",
            "CI/CD",
            "Go",
        ],
        "education": [
            {
                "institution": "ETH Zürich",
                "degree": "M.Sc.",
                "field": "Computer Science",
                "start_date": "2010-09",
                "end_date": "2012-06",
            },
            {
                "institution": "University of Edinburgh",
                "degree": "B.Sc.",
                "field": "Informatics",
                "start_date": "2007-09",
                "end_date": "2010-06",
            },
        ],
        "languages": [
            {"language": "English", "level": "Native"},
            {"language": "German", "level": "B2"},
        ],
    }
)

# ---------------------------------------------------------------------------
# Cover-letter fixtures — same letter_data shape as prompts/cover_letter.py.
# ---------------------------------------------------------------------------

LETTER_DE = {
    "header": {
        "name": "Jörg Müller-Lüdenscheidt",
        "address": "Maximilianstraße 12, 80539 München",
        "phone": "+49 89 1234567",
        "email": "joerg.mueller@example.de",
        "photo_url": None,
    },
    "recipient": {
        "name": "Frau Dr. Sabine Großmann",
        "title": "Leiterin Personalentwicklung",
        "company": "Süddeutsche Präzisionstechnik GmbH",
        "address": "Industriestraße 5, 85716 Unterschleißheim",
        "date": "11. Juni 2026",
    },
    "body": {
        "paragraphs": [
            (
                "mit großem Interesse habe ich Ihre Ausschreibung für die Position als "
                "Leiter Qualitätssicherung gelesen und bewerbe mich hiermit um diese "
                "verantwortungsvolle Aufgabe in Ihrem Hause."
            ),
            (
                "In meiner aktuellen Tätigkeit verantworte ich das Projektmanagement und "
                "die Prozessoptimierung über drei Fertigungsstandorte hinweg. Dabei konnte "
                "ich die Ausschussquote durch konsequente statistische Prozesslenkung "
                "deutlich senken."
            ),
            (
                "Meine fundierten Kenntnisse in Python und der Aufbau automatisierter "
                "Messdaten-Workflows ermöglichen es mir, Qualitätsdaten effizient "
                "auszuwerten und fundierte Entscheidungen zu treffen."
            ),
            (
                "Über die Gelegenheit zu einem persönlichen Gespräch würde ich mich sehr "
                "freuen und stehe Ihnen für Rückfragen jederzeit gerne zur Verfügung."
            ),
        ]
    },
    "signature": {
        "closing": "Mit freundlichen Grüßen",
        "name": "Jörg Müller-Lüdenscheidt",
    },
}

LETTER_EN = {
    "header": {
        "name": "Catherine O'Brien",
        "address": "Bahnhofstrasse 21, 8001 Zürich",
        "phone": "+44 20 7946 0958",
        "email": "catherine.obrien@example.com",
        "photo_url": None,
    },
    "recipient": {
        "name": "Mr. Daniel Weber",
        "title": "Head of Engineering",
        "company": "Müller & Söhne AG",
        "address": "Technoparkstrasse 1, 8005 Zürich",
        "date": "11 June 2026",
    },
    "body": {
        "paragraphs": [
            (
                "I am writing to express my strong interest in the Lead Platform Engineer "
                "role at your company, where I believe my background in cloud infrastructure "
                "would make an immediate impact."
            ),
            (
                "Over the past decade I have led the migration of large service estates onto "
                "managed Kubernetes platforms and championed infrastructure-as-code practices "
                "that dramatically shortened delivery cycles."
            ),
            (
                "My day-to-day work combines hands-on engineering in Python and Go with the "
                "project management discipline needed to keep cross-functional teams aligned "
                "on shared reliability goals."
            ),
            (
                "I would welcome the opportunity to discuss how my experience can support your "
                "platform ambitions and am happy to provide any further information you need."
            ),
        ]
    },
    "signature": {
        "closing": "Kind regards",
        "name": "Catherine O'Brien",
    },
}


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", [CV_DE, CV_EN], ids=["de", "en"])
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_template_roundtrip(template, fixture):
    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=fixture, color=_default_context()
    )
    pdf = await _html_to_pdf(html)
    report = audit_cv(pdf, fixture, KEYWORDS)
    failures = [(c.id, c.details) for c in report.checks if c.status == "fail"]
    assert not failures, f"{template}: {failures}"


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture", [LETTER_DE, LETTER_EN], ids=["de", "en"])
@pytest.mark.parametrize("template", sorted(LETTER_TEMPLATES))
async def test_letter_template_roundtrip(template, fixture):
    html = _jinja_env.get_template(LETTER_TEMPLATES[template]).render(
        letter=fixture, color=_default_color_context()
    )
    pdf = await _html_to_pdf(html)
    report = audit_cover_letter(pdf, fixture, KEYWORDS)
    failures = [(c.id, c.details) for c in report.checks if c.status == "fail"]
    assert not failures, f"{template}: {failures}"
