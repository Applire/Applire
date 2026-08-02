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
from applire.services.ats_audit import extract_text
from applire.services.color_detection import _default_context
from applire.services.cover_letter import _TEMPLATE_FILES as LETTER_TEMPLATES
from applire.services.cover_letter import _default_color_context
from applire.services.cv import _TEMPLATE_FILES as CV_TEMPLATES
from applire.services.cv import _html_to_pdf, _jinja_env
from applire.templates.labels import cover_letter_labels, cv_labels

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
                # US187 — a project nested under this position must render here and
                # survive the PDF extraction round-trip (ADR-039).
                "projects": [
                    {
                        "name": "Projekt Nullfehler-Initiative",
                        "bullets": [
                            "Aufbau einer statistischen Prozesslenkung für die Serienfertigung.",
                        ],
                    }
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
        # US187 — a standalone project (no associated position) must render in its
        # own section and survive the round-trip.
        "projects": [
            {
                "name": "Open-Source Messdaten-Toolkit",
                "bullets": ["Veröffentlichung eines Python-Pakets zur Messdatenanalyse."],
            }
        ],
        # PQ F7 — certifications, copied verbatim from the profile (ADR-040), must
        # survive the real PDF round-trip like every other section.
        "certifications": [
            {
                "name": "Lead Auditor ISO 9001",
                "issuing_organization": "TÜV Süd",
                "date_obtained": "2021-05-01",
            }
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
@pytest.mark.parametrize("fixture,lang", [(CV_DE, "de"), (CV_EN, "en")], ids=["de", "en"])
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_template_roundtrip(template, fixture, lang):
    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=fixture, color=_default_context(), lang=lang, labels=cv_labels(lang)
    )
    pdf = await _html_to_pdf(html)
    report = audit_cv(pdf, fixture, KEYWORDS)
    failures = [(c.id, c.details) for c in report.checks if c.status == "fail"]
    assert not failures, f"{template}: {failures}"


def _norm_probe(s: str) -> str:
    """Mirror ats_audit normalisation so substring search matches the PDF text."""
    from applire.services.ats_audit import _norm

    return _norm(s)


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_project_hierarchy_survives_roundtrip(template):
    """US187 / ADR-039 — the blocking gate. A project nested under a parent position
    and a standalone project must both survive the real Playwright PDF round-trip,
    and the nested project must render under its parent (after the parent company in
    reading order) for every shipped template."""
    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=CV_DE, color=_default_context(), lang="de", labels=cv_labels("de")
    )
    pdf = await _html_to_pdf(html)
    text = _norm_probe(extract_text(pdf))

    nested_name = _norm_probe("Projekt Nullfehler-Initiative")
    nested_bullet = _norm_probe("Aufbau einer statistischen Prozesslenkung für die Serienfertigung")
    parent_company = _norm_probe("Süddeutsche Präzisionstechnik GmbH")
    standalone_name = _norm_probe("Open-Source Messdaten-Toolkit")

    assert nested_name in text, f"{template}: nested project name dropped in PDF"
    assert nested_bullet in text, f"{template}: nested project bullet dropped in PDF"
    assert standalone_name in text, f"{template}: standalone project name dropped in PDF"
    # The nested project must appear after its parent position in reading order.
    assert text.index(parent_company) < text.index(nested_name), (
        f"{template}: nested project not rendered under its parent position"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_certifications_survive_roundtrip(template):
    """PQ F7 / ADR-039 — the blocking gate. A certification copied verbatim from the
    Master Profile (deterministic passthrough, no LLM) must survive the real
    Playwright PDF round-trip for every shipped template."""
    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=CV_DE, color=_default_context(), lang="de", labels=cv_labels("de")
    )
    pdf = await _html_to_pdf(html)
    text = _norm_probe(extract_text(pdf))

    cert_name = _norm_probe("Lead Auditor ISO 9001")
    cert_issuer = _norm_probe("TÜV Süd")

    assert cert_name in text, f"{template}: certification name dropped in PDF"
    assert cert_issuer in text, f"{template}: certification issuing organization dropped in PDF"


# ---------------------------------------------------------------------------
# Issue #118 — two CONCURRENT open-ended positions, and the summary names the
# current employer, which is exactly the constellation that made the
# reading-order check fire: the audit anchors each entry at its FIRST text
# occurrence, so a wrongly-last-placed newest entry anchored inside the summary.
# E049/ADR-067: `_enforce_work_order` is deleted — document order is now the
# VAULT's sorted order at `assemble_tailored_cv`, structurally. The regression
# guard therefore assembles from a sorted vault profile with the writer's PROSE
# arriving in the WRONG order, and the render must still be newest-start-first.
# ---------------------------------------------------------------------------

# Vault profile, reverse-chronological (as _render_cv_background sorts it
# before the prompt is ever built).
PROFILE_118 = {
    "personal_info": {
        "name": "Anna Bauer",
        "email": "anna.bauer@example.de",
        "phone": "+49 151 1234567",
        "location": "Berlin",
    },
    "work_experience": [
        {"id": "w-alpha", "company": "Alpha Analytics AG", "role": "Lead Data Engineer",
         "start_date": "2026-03", "end_date": None},
        {"id": "w-beta", "company": "Beta Consulting GmbH", "role": "Senior Consultant",
         "start_date": "2024-12", "end_date": None},
    ],
    "education": [
        {"institution": "TU Berlin", "degree": "M.Sc.", "field": "Informatik",
         "start_date": "2014-10", "end_date": "2017-09"},
    ],
    "languages": [{"language": "Deutsch", "level": "Muttersprache"}],
}

# The writer's prose response, deliberately OLDEST-first — the model cannot
# reorder entries it never emits, so assembly must ignore this order entirely.
PROSE_118_WRONG_ORDER = {
    # Mentions the CURRENT employer — the first-occurrence anchor lands here.
    "summary": (
        "Lead Data Engineer bei Alpha Analytics AG mit paralleler "
        "Beratungstätigkeit und Schwerpunkt auf skalierbaren Datenplattformen."
    ),
    "work": [
        {"id": "w-beta", "bullets": ["Beratung von Mittelständlern zu Datenstrategie."]},
        {"id": "w-alpha", "bullets": ["Aufbau der zentralen Datenplattform."]},
    ],
    "skills": ["Python", "Kubernetes"],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_concurrent_open_ended_positions_render_newest_first(template):
    """#118 regression — vault-join order survives the real PDF round-trip:
    newest-start-first in the document AND a passing reading-order check.
    E049/ADR-067: order comes from `assemble_tailored_cv`'s vault join now;
    the writer's prose arrives oldest-first and must not matter."""
    from applire.services.cv import assemble_tailored_cv

    tailored = TailoredCVData.model_validate(
        assemble_tailored_cv(PROSE_118_WRONG_ORDER, PROFILE_118)
    )
    tailored = tailored.model_copy(update={"show_photo": False})
    assert [w.company for w in tailored.work_history] == [
        "Alpha Analytics AG",
        "Beta Consulting GmbH",
    ]

    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=tailored, color=_default_context(), lang="de", labels=cv_labels("de")
    )
    pdf = await _html_to_pdf(html)
    report = audit_cv(pdf, tailored, [])
    reading_order = [c for c in report.checks if c.id == "reading-order"]
    assert reading_order and reading_order[0].status == "pass", (
        f"{template}: {reading_order[0].details if reading_order else 'check missing'}"
    )

    # The newest position's WORK-SECTION marker precedes the older one's. The
    # bullets are unique to their entries (roles/companies also occur in the
    # summary, which would alias the index check).
    text = _norm_probe(extract_text(pdf))
    assert text.index(_norm_probe("Aufbau der zentralen Datenplattform")) < text.index(
        _norm_probe("Beratung von Mittelständlern")
    ), f"{template}: work entries not newest-start-first in extracted text"


@pytest.mark.asyncio
@pytest.mark.parametrize("fixture,lang", [(LETTER_DE, "de"), (LETTER_EN, "en")], ids=["de", "en"])
@pytest.mark.parametrize("template", sorted(LETTER_TEMPLATES))
async def test_letter_template_roundtrip(template, fixture, lang):
    html = _jinja_env.get_template(LETTER_TEMPLATES[template]).render(
        letter=fixture, color=_default_color_context(), lang=lang, labels=cover_letter_labels(lang)
    )
    pdf = await _html_to_pdf(html)
    report = audit_cover_letter(pdf, fixture, KEYWORDS)
    failures = [(c.id, c.details) for c in report.checks if c.status == "fail"]
    assert not failures, f"{template}: {failures}"


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(LETTER_TEMPLATES))
async def test_letter_en_signoff_and_sender_name_survive_pdf_roundtrip(template):
    """#189 / ADR-038 — the reported bug: an EN letter closed with the German
    sign-off "Mit freundlichen Grüßen," and had NO sender name after it. As the
    LLM/mock emits it, the letter carries the German closing and a blank name; after
    the deterministic post-steps (_normalize_signature_closing +
    _backfill_sender_name, sourcing the name from the profile's 'personal_info'
    fallback schema) the REAL rendered PDF must contain the English chrome closing,
    NOT the German one, and the sender name. Enforced on every shipped letter
    template via the real Playwright round-trip (per the tests/ats render rule)."""
    from applire.services.cover_letter import (
        _backfill_sender_name,
        _normalize_signature_closing,
    )

    class _Profile:
        profile_json = {"personal_info": {"name": "Catherine O'Brien"}}

    # Exactly what the LLM/mock returns for an EN letter today: German sign-off,
    # empty sender name (the fallback path fed the prompt a blank name).
    letter_data = {
        "header": {
            "name": "",
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
                "I am writing to express my strong interest in the Lead Platform "
                "Engineer role and believe my background would make an immediate impact.",
                "Over the past decade I have led migrations onto managed Kubernetes "
                "platforms and championed infrastructure-as-code practices.",
            ]
        },
        "signature": {"closing": "Mit freundlichen Grüßen", "name": ""},
    }
    letter_data = _normalize_signature_closing(letter_data, "en")
    letter_data = _backfill_sender_name(letter_data, cv_data={}, profile=_Profile())

    html = _jinja_env.get_template(LETTER_TEMPLATES[template]).render(
        letter=letter_data,
        color=_default_color_context(),
        lang="en",
        labels=cover_letter_labels("en"),
        subject="Application: Lead Platform Engineer",
    )
    pdf = await _html_to_pdf(html)
    text = _norm_probe(extract_text(pdf))

    assert _norm_probe("Kind regards") in text, f"{template}: EN sign-off missing in PDF"
    assert _norm_probe("Mit freundlichen Grüßen") not in text, (
        f"{template}: German sign-off leaked into EN letter PDF"
    )
    assert _norm_probe("Catherine O'Brien") in text, (
        f"{template}: backfilled sender name missing after the sign-off in PDF"
    )


# ---------------------------------------------------------------------------
# #429 (charter run 15) — a budget-length letter is ONE page; the signature
# never orphans. Run 15's delivered Anschreiben (266 words of body, within the
# ADR-051 DACH budget) rendered as 2 PDF pages with page 2 holding nothing but
# the sender name: the break fell INSIDE the signature block, and the block's
# generous spacing pushed the name ~3mm past 297mm. The condense machinery
# owns content length; layout must give budgeted content room.
# ---------------------------------------------------------------------------

LETTER_DE_BUDGET = {
    "header": LETTER_DE["header"],
    "recipient": LETTER_DE["recipient"],
    "body": {
        "paragraphs": [
            (
                "mit großem Interesse habe ich Ihre Ausschreibung für die Position "
                "als Leiter Qualitätssicherung gelesen. Süddeutsche Präzisionstechnik "
                "fertigt hochwertige Baugruppen für anspruchsvolle Industriekunden in "
                "ganz Europa, und genau dieses Umfeld aus Serienfertigung, "
                "Projektmanagement und hoher Dokumentationsdisziplin reizt mich an "
                "dieser verantwortungsvollen Aufgabe."
            ),
            (
                "Als Teamleiter Qualitätssicherung führe ich derzeit acht "
                "Prüfingenieure über drei Fertigungsstandorte hinweg und verantworte "
                "die statistische Prozesslenkung der gesamten Serienfertigung. Die "
                "Ausschussquote konnte ich in achtzehn Monaten durch konsequente "
                "Prozessoptimierung deutlich senken, während die Liefertreue "
                "gegenüber unseren Schlüsselkunden stabil blieb. In der Vertretung "
                "der Werksleitung habe ich mehrfach die Verantwortung für den "
                "gesamten Standort mit allen Schichten übernommen."
            ),
            (
                "Meine fundierten Kenntnisse in Python und der Aufbau automatisierter "
                "Messdaten-Workflows ermöglichen es mir, Qualitätsdaten effizient "
                "auszuwerten und Entscheidungen auf belastbare Zahlen zu stellen. "
                "Die Einführung eines KPI-gestützten Berichtswesens hat bei uns die "
                "monatlichen Qualitätsrunden von einer Diskussion über Einzelfälle "
                "zu einer Steuerung über Trends verändert, die auch die "
                "Geschäftsführung unmittelbar nutzt."
            ),
            (
                "Eine Zertifizierungslandschaft nach IATF-Standard kenne ich bisher "
                "nur aus der Auditvorbereitung, nicht aus eigener Verantwortung — "
                "das wäre für mich der nächste Schritt, und genau deshalb reizt "
                "mich diese Position. Die Audit- und Dokumentationsdisziplin selbst "
                "ist mir aus zehn Jahren Qualitätsarbeit in der Präzisionsfertigung "
                "durchgehend vertraut."
            ),
            (
                "Über die Gelegenheit zu einem persönlichen Gespräch würde ich mich "
                "sehr freuen und stehe Ihnen für Rückfragen jederzeit gerne zur "
                "Verfügung."
            ),
        ]
    },
    "signature": LETTER_DE["signature"],
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "template",
    [
        # #431: academic's typography (11pt / 1.7 / 28mm measure) is ~32mm
        # over at budget length — needs a design decision, not a spacing
        # nudge. strict=True so fixing the template forces this marker out.
        pytest.param(t, marks=pytest.mark.xfail(strict=True, reason="#431"))
        if t == "academic"
        else t
        for t in sorted(LETTER_TEMPLATES)
    ],
)
async def test_letter_at_budget_renders_one_page(template):
    import io

    from pypdf import PdfReader

    html = _jinja_env.get_template(LETTER_TEMPLATES[template]).render(
        letter=LETTER_DE_BUDGET,
        color=_default_color_context(),
        lang="de",
        labels=cover_letter_labels("de"),
        subject="Bewerbung als Leiter Qualitätssicherung",
    )
    pdf = await _html_to_pdf(html)
    reader = PdfReader(io.BytesIO(pdf))
    n_words = sum(len(p.split()) for p in LETTER_DE_BUDGET["body"]["paragraphs"])
    assert len(reader.pages) == 1, (
        f"{template}: {n_words}-word body rendered {len(reader.pages)} pages — "
        "budget-length letters must fit the DACH 1-page norm (ADR-051); "
        f"last page text: {reader.pages[-1].extract_text()!r}"
    )
