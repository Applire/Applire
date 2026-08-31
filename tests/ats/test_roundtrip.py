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
from applire.services.cover_letter import _TEMPLATES_DIR as LETTER_TEMPLATES_DIR
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
@pytest.mark.parametrize("template", sorted(LETTER_TEMPLATES))
async def test_letter_signature_block_never_splits_across_pages(template):
    """The #429 defect verbatim: the sign-off stayed on page 1 and the sender
    name alone spilled to page 2.

    Asserting the *page count* here would be the obvious test and is the wrong
    one — it depends on the renderer's font metrics (these templates ask for
    Palatino/Georgia, absent on a bare CI runner), so a budget-length fixture
    sits at the boundary and the gate flips with the environment. This suite
    BLOCKS the build, so an environment-sensitive assertion in it is a
    liability, not coverage. What the fix actually guarantees is relative and
    metric-independent: the signature block is atomic, so the sender name is
    always on the same page as the closing it belongs to. The absolute
    "budget-length letter fits one page" property is verified on the real
    render path instead, and `academic` cannot hold it at all (#431).
    """
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
    pages = [_norm_probe(p.extract_text() or "") for p in reader.pages]

    closing = _norm_probe(LETTER_DE_BUDGET["signature"]["closing"])
    name = _norm_probe(LETTER_DE_BUDGET["signature"]["name"])
    closing_pages = [i for i, t in enumerate(pages) if closing in t]
    assert closing_pages, f"{template}: sign-off missing from the rendered PDF"

    # Every template puts the sender name in the LETTERHEAD too, so "the name
    # appears somewhere on the closing's page" is satisfied on page 1 even
    # when the signature name orphaned onto page 2 — that phrasing produces a
    # test that cannot fail (verified against the pre-fix CSS before this
    # form was chosen). The discriminator has to be positional: the name must
    # follow the closing ON the closing's own page.
    closing_page = pages[closing_pages[-1]]
    after_closing = closing_page[closing_page.rindex(closing) + len(closing):]
    assert name in after_closing, (
        f"{template}: signature block split across pages — the sign-off is on "
        f"page {closing_pages[-1] + 1} with no sender name after it; the name "
        f"orphaned onto page {len(pages)}. This is #429: the block must be "
        f"atomic (break-inside: avoid)."
    )


# ---------------------------------------------------------------------------
# #547 — the #429 fix stopped the sign-off block from SPLITTING, but did not
# stop the whole block from being BOUNCED to a page of its own: Chromium's
# `break-inside: avoid` moves the entire atomic block to the next page the
# moment it doesn't fit the remainder of the current one, even when that
# remainder is generous — measured directly (backend/../scratchpad probes,
# not reproduced here) at ~1-1.3cm short on a budget-length `executive`
# letter, with the block's own margin-top/margin-bottom ("signature air")
# accounting for most of its footprint. Reduced margins recover ~8mm of
# headroom uniformly (7 templates, see each template's #547 comment) — a
# probability reduction, not a proof: no finite CSS margin budget can
# guarantee fit against unbounded content (this is exactly why the sibling
# test above deliberately does NOT assert a page count — see its docstring).
# What IS deterministic and worth pinning is the mechanism the fix actually
# changed: the block's own air is bounded low enough that it no longer
# reproduces the executive-template case above. A page-count assertion on a
# real render lives in test_letter_signature_orphans_less_often below for
# that one calibrated case; this test is the environment-independent gate.
#
# Two structural alternatives to a margin shave were tried and rendered —
# neither survives contact with real page.pdf() output, so don't re-attempt
# either without new evidence:
#
#   (a) Move `.signature`'s margin-top onto the PRECEDING paragraph's
#       margin-bottom instead (`.body p:last-child`), keeping the same total
#       collapsed visual gap, on the theory that a margin owned by a
#       non-atomic sibling gets truncated at an unforced break instead of
#       counting toward the atomic block's fit test. Implemented and
#       rendered on executive/modern_swiss/tech_developer/lebenslauf: the
#       output PDF was pixel-identical to the unmoved version in every case
#       (`compare -metric AE` = 0 on every page). Chromium's break-fit
#       check here evidently uses the accumulated flow height up to the
#       block regardless of which sibling's CSS declares the connecting
#       margin — relocating a collapsed margin without shrinking it changes
#       nothing.
#
#       A related isolation (varying margin-top's actual VALUE, not just
#       its owner, while holding `.closing`'s margin-bottom fixed) shows
#       margin-top isn't uniformly free either: at closing_mb=8mm on
#       executive, margin-top=10mm still overflows but margin-top=6mm
#       fits; at closing_mb=12mm no margin-top value (10/4/0mm all tried)
#       fits; at closing_mb=4mm every margin-top tried fits. The two
#       margins interact non-additively near the boundary — there is no
#       clean "only the internal margin counts" rule to exploit, so the
#       committed fix reduces both rather than picking one lever.
#
#   (b) Add `break-before: avoid` / `page-break-before: avoid` to
#       `.signature`, the issue's own "no keep-with-previous" hypothesis.
#       Rendered on both the pre-#547 (10mm/12mm, 2 pages) and post-#547
#       (6mm/8mm, 1 page) margins: the page count was identical with and
#       without the property in both cases. Chromium's print pagination
#       does not act on it here — this Chromium version's paged-media
#       support for `break-before: avoid` is the poor-to-absent case the
#       spec's implementation status has long warned about.
#
# The margin-value reduction below remains the only lever with demonstrated
# effect; see the per-template scratchpad renders (not committed) for the
# raw page-count tables behind this note.
# ---------------------------------------------------------------------------

_SIGNATURE_AIR_BUDGET_MM = 14.0  # #547 — pre-fix sums were 18-22mm; academic
# included even though #431 excludes it from the "fits in one page" claim —
# the split-guard and the air budget are independent concerns.


@pytest.mark.parametrize("template", sorted(LETTER_TEMPLATES))
def test_letter_signature_air_stays_under_547_budget(template):
    """The block's own margin-top + closing margin-bottom must not creep back
    above the #547 budget. Deterministic and font-independent — reads the raw
    template source, renders nothing. Revert either margin to its pre-#547
    value on any template and this fails on that template alone.
    """
    import re

    source = (LETTER_TEMPLATES_DIR / LETTER_TEMPLATES[template]).read_text(encoding="utf-8")
    margin_top = re.search(r"\.signature\s*\{[^}]*margin-top:\s*([\d.]+)mm", source)
    margin_bottom = re.search(r"\.signature\s+\.closing\s*\{\s*margin-bottom:\s*([\d.]+)mm", source)
    assert margin_top, f"{template}: no .signature margin-top found — template shape changed"
    assert margin_bottom, f"{template}: no .signature .closing margin-bottom found — template shape changed"

    air = float(margin_top.group(1)) + float(margin_bottom.group(1))
    assert air <= _SIGNATURE_AIR_BUDGET_MM, (
        f"{template}: signature block air is {air}mm (margin-top "
        f"{margin_top.group(1)}mm + closing margin-bottom {margin_bottom.group(1)}mm), "
        f"over the #547 budget of {_SIGNATURE_AIR_BUDGET_MM}mm — this is the exact "
        f"regression #547 fixed: a padded closing block gets bounced whole to a "
        f"near-empty page 2 more often than it needs to."
    )


@pytest.mark.asyncio
async def test_letter_signature_orphans_less_often_547():
    """The #547 defect verbatim, on the template it reproduces on directly: a
    budget-length letter (LETTER_DE_BUDGET) plus ONE realistic extra sentence
    overflows page 1 by roughly a centimetre on `executive`. Pre-fix (10mm
    signature margin-top + 12mm closing margin-bottom = 22mm of air) this
    rendered 2 pages, with page 2 holding nothing but "Mit freundlichen
    Grüßen" + the sender's name and page 1 ending with ~5cm of visible free
    space (verified by hand against a rendered screenshot during triage).
    Post-fix (6mm + 8mm = 14mm) the same fixture fits on page 1.

    This is the one template/fixture pair where the fix's effect on the
    actual page count is verified and stable enough to gate on — it is NOT a
    claim that every overflowing letter now fits (see the budget test above
    for why that claim can't be made in CSS). Other templates need a
    different amount of overflow to reproduce the same shape; this fixture
    was calibrated against `executive` specifically.
    """
    import copy
    import io

    from pypdf import PdfReader

    pad_sentence = (
        "Diese zusätzliche Erfahrung im Bereich Qualitätsmanagement und "
        "Prozessoptimierung rundet mein Profil weiter ab und zeigt meine "
        "Bereitschaft, Verantwortung zu übernehmen. "
    )
    letter = copy.deepcopy(LETTER_DE_BUDGET)
    letter["body"]["paragraphs"] = list(letter["body"]["paragraphs"]) + [pad_sentence]

    html = _jinja_env.get_template(LETTER_TEMPLATES["executive"]).render(
        letter=letter,
        color=_default_color_context(),
        lang="de",
        labels=cover_letter_labels("de"),
        subject="Bewerbung als Leiter Qualitätssicherung",
    )
    pdf = await _html_to_pdf(html)
    pages = len(PdfReader(io.BytesIO(pdf)).pages)
    assert pages == 1, (
        f"executive: budget-length letter + one extra sentence rendered as "
        f"{pages} pages — the #547 orphan (sign-off alone on page 2 with "
        f"page 1 still visibly short of full) is back. This exact fixture "
        f"rendered 2 pages before #547's margin reduction."
    )


# ---------------------------------------------------------------------------
# #547 (orphan case, session 2) — the ONE real-render gate above
# (test_letter_signature_orphans_less_often_547) is calibrated on a single
# template, executive. That happens to be one of the three templates where
# the orphan does NOT reproduce (see the sweep below) — the one rendering
# gate this codebase had for the defect was calibrated on a template the
# defect does not affect.
#
# A real-render sweep of all 7 (LETTER_DE_BUDGET — already ~1 page on every
# template — plus N realistic German words appended to the LAST paragraph;
# a harder probe than a new paragraph, since it pays none of a fresh <p>
# margin's fixed cost; real Chromium via _html_to_pdf, measured with
# `pdftotext -bbox-layout`) found the smallest N that flips a template from
# 1 to 2 pages, where page 2 holds ONLY the closing + name and page 1 ends
# with double-digit mm of visibly unused space — the #547 orphan shape,
# not a genuine content-driven 2-page letter:
#
#   template            flip N (extra words)   reproduces the orphan?
#   classic_german       8                      yes
#   tech_developer       20                     yes
#   modern_swiss          30                     yes
#   academic              34                     yes
#   executive             not within 40          no (not observed)
#   compact_pro           not within 40          no (not observed)
#   creative_sidebar      not within 40          no (not observed)
#
# A candidate fix was tried and REVERTED, not shipped: a further uniform
# -2mm/-2mm shave on the shared `.signature`/`.closing` block (same lever,
# same magnitude as the original #547 margin reduction). Measured
# (real-rendered, not assumed): it moved classic_german 8 -> 22 words and
# tech_developer 20 -> 34 (both +14, materially real) and modern_swiss
# beyond the 40-word swept range, but bought academic +0 words — the
# IDENTICAL cut, zero measured effect, on a template whose baseline page-1
# slack (LETTER_DE_BUDGET, zero extra words: 6.4/11.1/14.4/16.5mm for
# classic_german/tech_developer/academic/modern_swiss respectively, vs
# 19.4/71.8/70.8mm for executive/compact_pro/creative_sidebar) already
# showed the shared block is not the dominant variable — tech_developer and
# executive carried the IDENTICAL pre-shave signature air (14mm) and
# flipped 20+ words apart. A margin trim that helps 3 of 4 affected
# templates unevenly and does nothing on the fourth, applied to 3 more
# templates that never reproduced the defect at all, is not a fix backed by
# its own evidence — reverted (see git history / the run report for the
# per-template before/after numbers) rather than shipped as a partial
# result dressed as a defect fix.
#
# The real per-template driver is each template's OWN page-1 capacity —
# header band height, accent-rule margins, date/subject spacing, all
# outside the shared signature markup (verified by reading the template
# source: lebenslauf_letter's coloured header band + 16mm margin + 14mm
# accent-rule margin alone spend ~30mm before the body starts). Closing it
# needs a per-template page-1-capacity change in the manner of #621's
# `@page :first` inset or #431's retypography — both visible "look"
# decisions the founder made for their own templates, not a shared CSS
# budget this block owns. Not made unilaterally here; the four affected
# templates are marked `xfail` below, carrying their measured flip word
# count, so the gap is recorded rather than hidden. Same lever, N
# templates, when it earns its keep — ADR-066.
# ---------------------------------------------------------------------------

_ORPHAN_547_FILLER_WORDS = (
    "Diese zusätzliche Erfahrung im Bereich Qualitätsmanagement und "
    "Prozessoptimierung rundet mein Profil weiter ab und zeigt meine "
    "Bereitschaft Verantwortung zu übernehmen sowie Projekte über "
    "mehrere Standorte hinweg erfolgreich zum Abschluss zu bringen "
    "und dabei stets die Kundenanforderungen im Blick zu behalten."
).split()  # 40 words

_ORPHAN_547_FREE_SPACE_FLOOR_MM = 8.0  # below this, a lonely page-2 closing
# is a genuine, non-wasteful natural break (little room was left to waste),
# not the #547 orphan shape — see the sweep comment above for the measured
# 17-25mm this defect actually leaves on every reproduced case.

_ORPHAN_547_XFAIL_REASON = (
    "flips at +{measured} words (this test is calibrated at that exact "
    "measured flip point). "
    "Root cause pinned to this template's OWN page-1 capacity (header/"
    "accent-rule/date-block budget), not the shared signature block — a "
    "shared-block margin trim was tried and reverted (measured to help "
    "unevenly across templates, see the comment above). Needs a "
    "per-template page-1-capacity change (#621 @page:first / #431 "
    "retypography class) — a founder 'look' decision, not made here."
)

def _xfail_547(measured_flip_words: int) -> pytest.MarkDecorator:
    """``strict=True``: the marked case must keep FAILING the real assertion
    below. If a future per-template capacity fix makes it pass, pytest turns
    that XPASS into a hard failure instead of silently going green — the
    marker itself must be removed once a fix is verified, not left stale."""
    return pytest.mark.xfail(
        reason=_ORPHAN_547_XFAIL_REASON.format(measured=measured_flip_words),
        strict=True,
    )


_ORPHAN_547_PARAMS = [
    # (template, n_words_appended) — each template's OWN measured flip
    # point (the smallest overflow that reproduces the orphan; 40 == the
    # full filler list, for the 3 templates that never flipped within it);
    # xfail only on the 4 confirmed-reproducing cases.
    pytest.param("classic_german", 8, marks=_xfail_547(8), id="classic_german"),
    pytest.param("tech_developer", 20, marks=_xfail_547(20), id="tech_developer"),
    pytest.param("modern_swiss", 30, marks=_xfail_547(30), id="modern_swiss"),
    pytest.param("academic", 34, marks=_xfail_547(34), id="academic"),
    pytest.param("executive", 40, id="executive"),
    pytest.param("compact_pro", 40, id="compact_pro"),
    pytest.param("creative_sidebar", 40, id="creative_sidebar"),
]
assert sorted(p.id for p in _ORPHAN_547_PARAMS) == sorted(LETTER_TEMPLATES), (
    "the #547 orphan sweep must cover exactly the shipped letter templates"
)


def _bbox_pages_547(pdf_bytes: bytes) -> list[dict]:
    """poppler ``pdftotext -bbox-layout`` -> one dict per page: width/height
    (pt) + a word list of (xMin, yMin, xMax, yMax, text) tuples, top-left
    origin. A local re-implementation of test_page_geometry.py's own
    ``_bbox_pages`` — kept local rather than cross-imported, matching this
    tree's per-file convention (test_letter_final_floor_547.py)."""
    import html as html_mod
    import re
    import subprocess
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        pdf_path = f.name
    try:
        proc = subprocess.run(
            ["pdftotext", "-bbox-layout", pdf_path, "-"],
            capture_output=True, text=True, check=True,
        )
    finally:
        Path(pdf_path).unlink(missing_ok=True)

    page_re = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
    word_re = re.compile(
        r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', re.S
    )
    pages = []
    for width, height, body in page_re.findall(proc.stdout):
        words = [
            (float(x0), float(y0), float(x1), float(y1), html_mod.unescape(text))
            for x0, y0, x1, y1, text in word_re.findall(body)
        ]
        pages.append({"width": float(width), "height": float(height), "words": words})
    return pages


def _read_page_bottom_margin_mm_547(template_file: str) -> float:
    import re

    source = (LETTER_TEMPLATES_DIR / template_file).read_text(encoding="utf-8")
    m = re.search(r"@page\s*\{[^}]*?margin:\s*([^;]+);", source, re.S)
    assert m, "no @page margin rule found"
    vals = [float(v[:-2]) for v in m.group(1).split()]
    if len(vals) == 1:
        return vals[0]
    if len(vals) == 2:
        return vals[0]
    return vals[2]  # 3- or 4-value shorthand: bottom is always index 2


@pytest.mark.asyncio
@pytest.mark.parametrize("template, n_words", _ORPHAN_547_PARAMS)
async def test_letter_signature_no_wasteful_orphan_547(template, n_words):
    """The RELATIVE #547 invariant — "a letter whose body fits page 1 keeps
    its closing block on page 1" — calibrated per template from the flip
    points measured above (each fixture is set at its template's OWN
    measured flip point: the smallest overflow at which the body's own
    text still ends with visible room to spare on page 1, yet the atomic
    signature block gets bounced whole to a lonely page 2 anyway), never
    from an assumed page geometry. Deliberately NOT a bare page-count
    assertion (the risk this exact file's own #429 test docstring already
    flags for a cross-template gate: font substitution in CI shifts
    exactly where the boundary falls). Instead: if the render spans 2+
    pages, it only fails when page 2 holds nothing but the closing/name
    AND page 1 still has meaningful (>8mm) unused room — both quantities
    read from the SAME render, so a font swap moves the boundary but not
    the comparison: on a template that never reproduces the orphan (the
    three plain params below with no marker), a font-driven overflow at
    this same fixture would still pass, because real body text — not just
    the closing — would share page 2, or page 1 would already be genuinely
    full.

    xfail (strict, via the marks on _ORPHAN_547_PARAMS) on the four
    templates this session confirmed still reproduce the orphan today: the
    body of THIS function still runs and is still asserted on for those
    four (imperative ``pytest.xfail()`` would skip the render entirely and
    defeat strict's whole purpose) — only the marker on the param, not a
    skip in this body, is what converts a real failure into a recorded,
    tracked XFAIL, and an unexpected pass into a hard error.
    """
    import copy

    letter = copy.deepcopy(LETTER_DE_BUDGET)
    if n_words:
        extra = " ".join(_ORPHAN_547_FILLER_WORDS[:n_words])
        paragraphs = list(letter["body"]["paragraphs"])
        paragraphs[-1] = paragraphs[-1] + " " + extra
        letter["body"]["paragraphs"] = paragraphs

    html = _jinja_env.get_template(LETTER_TEMPLATES[template]).render(
        letter=letter,
        color=_default_color_context(),
        lang="de",
        labels=cover_letter_labels("de"),
        subject="Bewerbung als Leiter Qualitätssicherung",
    )
    pdf = await _html_to_pdf(html)
    pages = _bbox_pages_547(pdf)

    if len(pages) < 2:
        return  # single page: no page 2 to orphan, invariant trivially holds

    pt_per_mm = 72.0 / 25.4
    bottom_margin_mm = _read_page_bottom_margin_mm_547(LETTER_TEMPLATES[template])
    p1, p2 = pages[0], pages[1]
    y_max_p1 = max((w[3] for w in p1["words"]), default=0.0)
    bottom_ceiling_pt = p1["height"] - bottom_margin_mm * pt_per_mm
    free_mm = (bottom_ceiling_pt - y_max_p1) / pt_per_mm

    closing = _norm_probe(LETTER_DE_BUDGET["signature"]["closing"])
    name = _norm_probe(LETTER_DE_BUDGET["signature"]["name"])
    p2_text = _norm_probe(" ".join(w[4] for w in p2["words"]))
    leftover = p2_text
    for tok in closing.split() + name.split():
        leftover = leftover.replace(tok, "")
    orphan_only = len(leftover.strip(" ,.-")) < 5

    is_wasteful_orphan = orphan_only and free_mm > _ORPHAN_547_FREE_SPACE_FLOOR_MM
    assert not is_wasteful_orphan, (
        f"{template}: {n_words} extra words bounced the closing alone onto "
        f"page {len(pages)}, leaving {free_mm:.1f}mm unused on page 1 — "
        f"the #547 orphan."
    )


# ---------------------------------------------------------------------------
# #431 — academic_letter.html.j2 could not hold a budget-length body on one
# page: 11pt / line-height 1.7 / 28mm side padding, the airiest typography of
# the family, overflowed A4 by ~32mm on LETTER_DE_BUDGET when reported
# (2026-08-02) and still by ~24mm after #547's shared signature-air reduction
# landed (measured 2026-08-28: signature bottom 300.93mm + 20mm bottom
# padding = 320.93mm vs 297mm — a real-render scratch probe, not committed,
# per this suite's rule against new absolute page-count gates).
#
# PO decision 2026-08-28: densify — a visible typography change, not a CSS
# margin shave, because #431 was routed to the founder for exactly that
# reason (making a budget letter fit on this template requires a look
# change). Target ~10.5pt / line-height 1.55 / 24mm side padding, tried
# first and kept as-is: measured on the real render path (LETTER_DE_BUDGET,
# same scratch probe) at 1 page, signature bottom 263.94mm, 13.06mm reserve
# below the signature before the bottom padding starts — comfortably over
# the PO's >=10mm target without being drastically loose. Font family,
# color, structure and every other template untouched.
#
# The gate below is the font-independent half — modelled on
# `test_letter_signature_air_stays_under_547_budget` above: it reads the
# template source and pins the typography as an upper-bound BUDGET (not an
# exact-value pin), so any further tightening still passes and only a
# regression back toward the pre-#431 airiness fails it.
# ---------------------------------------------------------------------------

_ACADEMIC_FONT_SIZE_BUDGET_PT = 10.5
_ACADEMIC_LINE_HEIGHT_BUDGET = 1.55
_ACADEMIC_SIDE_PADDING_BUDGET_MM = 24.0


def test_academic_letter_typography_stays_under_431_budget():
    """#431 (PO decision 2026-08-28): the academic template's body typography
    must not creep back toward the pre-fix airiness (11pt / 1.7 / 28mm side
    padding) that made a budget-length DE letter overflow A4. Deterministic
    and font-independent — reads the raw template source, renders nothing.
    Loosen any one of the three values past its budget and this fails.
    """
    import re

    source = (LETTER_TEMPLATES_DIR / LETTER_TEMPLATES["academic"]).read_text(encoding="utf-8")
    font_size = re.search(r"\bbody\s*\{[^}]*font-size:\s*([\d.]+)pt", source)
    line_height = re.search(r"\bbody\s*\{[^}]*line-height:\s*([\d.]+)", source)
    side_padding = re.search(r"\.page\s*\{[^}]*padding:\s*[\d.]+mm\s+([\d.]+)mm", source)
    assert font_size, "academic: no body font-size found — template shape changed"
    assert line_height, "academic: no body line-height found — template shape changed"
    assert side_padding, "academic: no .page side padding found — template shape changed"

    assert float(font_size.group(1)) <= _ACADEMIC_FONT_SIZE_BUDGET_PT, (
        f"academic: body font-size is {font_size.group(1)}pt, over the #431 budget of "
        f"{_ACADEMIC_FONT_SIZE_BUDGET_PT}pt — this is the exact regression #431 fixed: "
        f"typography airy enough to push a budget-length letter onto a second page."
    )
    assert float(line_height.group(1)) <= _ACADEMIC_LINE_HEIGHT_BUDGET, (
        f"academic: body line-height is {line_height.group(1)}, over the #431 budget of "
        f"{_ACADEMIC_LINE_HEIGHT_BUDGET} — this is the exact regression #431 fixed: "
        f"typography airy enough to push a budget-length letter onto a second page."
    )
    assert float(side_padding.group(1)) <= _ACADEMIC_SIDE_PADDING_BUDGET_MM, (
        f"academic: .page side padding is {side_padding.group(1)}mm, over the #431 budget "
        f"of {_ACADEMIC_SIDE_PADDING_BUDGET_MM}mm — this is the exact regression #431 "
        f"fixed: typography airy enough to push a budget-length letter onto a second page."
    )


# ---------------------------------------------------------------------------
# #357 — a position block is atomic across the page break. The reported defect:
# a 3-page Lebenslauf split one position so 3 of its 5 bullets sat on page 1
# and 2 on page 2, with no visual link back to the heading; the worst shape is
# a role heading alone at the bottom of a page. German CV convention treats
# (role, employer, dates, bullets) as one unit, so a split reads as an
# assembly failure to a DACH reviewer. Same class as #429 on the letter side.
#
# Fixture design follows the #429 lesson: do NOT assert an absolute page count
# or tune one entry onto a boundary — both depend on the renderer's font
# metrics, and this suite BLOCKS the build. Instead render enough entries to
# span several pages (so several boundaries exist wherever they land) and
# assert the metric-independent relative invariant: whichever page an entry's
# heading lands on, that entry's whole body is on it too. Post-fix this holds
# structurally for any font (`break-inside: avoid` on `.entry`); pre-fix it
# fails because a boundary falling inside an entry is near-certain once the
# document is several pages long.
#
# #622 amendment: CV_357's positions carry 4 bullets each — LONG by #622's own
# threshold (>3), so they are now DESIGNED to be allowed to break (head +
# first 2 bullets keep together, last 2 bullets keep together; #622 replaced
# the blanket per-entry `break-inside: avoid` with that shape for long
# entries specifically). Re-measured on the fixed templates: several
# templates' positions do now split at this shape, which is the intended
# fix's own effect, not a regression — a lone bullet no longer gets pushed
# whole onto a near-empty next page. Asserting the ORIGINAL whole-atomicity
# invariant unconditionally would fail on correct #622 output, so it now
# applies per #622's own short/long rule: education entries (0 bullets, ALWAYS
# short) keep the original whole-atomicity check unchanged; work_history
# entries check the #622 contract that applies to their own bullet count —
# whole-atomicity if <=3, the lead/tail shape if >3 (mirrors
# tests/ats/test_page_geometry.py's long-entries test, same fixture shape,
# different fixture data).
# ---------------------------------------------------------------------------

_WORK_357 = [
    ("Alpha Präzisionswerke GmbH", "Leiter Prüfmittelmanagement Kennung01"),
    ("Berger Maschinenbau AG", "Teamleiter Serienprüfung Kennung02"),
    ("Cordes Fertigungstechnik GmbH", "Qualitätsingenieur Messtechnik Kennung03"),
    ("Dörfler Systemtechnik KG", "Prozessingenieur Fertigung Kennung04"),
    ("Eckhardt Werkzeugbau GmbH", "Auditor Lieferantenqualität Kennung05"),
    ("Fassbender Industrietechnik AG", "Fachreferent Prüfplanung Kennung06"),
    ("Gerlach Antriebstechnik GmbH", "Gruppenleiter Wareneingang Kennung07"),
    ("Hübner Umformtechnik AG", "Ingenieur Serienbetreuung Kennung08"),
    ("Imhoff Feinmechanik GmbH", "Referent Qualitätsplanung Kennung09"),
    ("Jansen Präzisionsguss AG", "Werksingenieur Gussteile Kennung10"),
    ("Köhler Verfahrenstechnik GmbH", "Spezialist Messsysteme Kennung11"),
    ("Lindner Blechbearbeitung AG", "Koordinator Erstmuster Kennung12"),
    ("Möller Kunststofftechnik GmbH", "Fachplaner Prüfprozesse Kennung13"),
    ("Neuhaus Zerspanungstechnik AG", "Sachbearbeiter Prüfmittel Kennung14"),
]


def _bullets_357(n: int) -> list[str]:
    tag = f"{n:02d}"
    return [
        f"Verantwortung Beleg{tag}A für die statistische Prozesslenkung "
        f"der Serienfertigung über drei Fertigungslinien im Dreischichtbetrieb.",
        f"Verantwortung Beleg{tag}B für die Erstmusterprüfberichte nach "
        f"VDA-Standard über mehrere Produktfamilien der Baureihe hinweg.",
        f"Verantwortung Beleg{tag}C für die Schulung der Prüfer in "
        f"Messtechnik, Dokumentationsdisziplin und Prüfmittelüberwachung.",
        f"Verantwortung Beleg{tag}D für die Auditvorbereitung sowie die "
        f"Nachverfolgung sämtlicher vereinbarter Korrekturmaßnahmen.",
    ]


CV_357 = TailoredCVData.model_validate(
    {
        "contact": {
            "name": "Jörg Müller-Lüdenscheidt",
            "email": "joerg.mueller@example.de",
            "phone": "+49 89 1234567",
            "location": "München",
            "photo_url": None,
        },
        "show_photo": False,
        "summary": (
            "Erfahrener Qualitätsingenieur mit langjähriger Verantwortung für "
            "Prozessoptimierung und Projektmanagement in der Präzisionsfertigung."
        ),
        "work_history": [
            {
                "company": company,
                "role": role,
                "start_date": f"{2024 - 2 * n:04d}-04",
                "end_date": f"{2026 - 2 * n:04d}-03",
                "bullets": _bullets_357(n + 1),
            }
            for n, (company, role) in enumerate(_WORK_357)
        ],
        "skills": ["Python", "Kubernetes", "Projektmanagement", "Messtechnik"],
        "education": [
            {
                "institution": "Technische Universität München Ausbildung31",
                "degree": "Dipl.-Ing. Maschinenbau Abschluss31",
                "field": "Fertigungstechnik",
                "start_date": "1998-10",
                "end_date": "2003-03",
            },
            {
                "institution": "Fachhochschule Rosenheim Ausbildung32",
                "degree": "Techniker Feinwerktechnik Abschluss32",
                "field": "Feinwerktechnik",
                "start_date": "1995-09",
                "end_date": "1998-07",
            },
        ],
        "languages": [{"language": "Deutsch", "level": "Muttersprache"}],
    }
)


def _pdf_pages_norm(pdf: bytes) -> list[str]:
    import io

    from pypdf import PdfReader

    return [_norm_probe(p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages]


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_position_block_never_splits_across_pages(template):
    """#357 (amended for #622, see banner above) — a SHORT entry (<=3 bullets,
    every education entry included since those never carry bullets) renders
    wholly on one page, unchanged since #357. A LONG entry (CV_357's
    positions: 4 bullets each) follows #622's contract instead: the title's
    page also holds bullets 1-2, and the entry's last page holds >=2 bullets.
    """
    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=CV_357, color=_default_context(), lang="de", labels=cv_labels("de")
    )
    pdf = await _html_to_pdf(html)
    pages = _pdf_pages_norm(pdf)

    assert len(pages) >= 2, (
        f"{template}: fixture rendered {len(pages)} page(s) — it must span a page "
        f"break for this invariant to mean anything"
    )

    short_entries: list[tuple[str, list[str]]] = []
    long_entries: list[tuple[str, str, list[str]]] = []
    for job in CV_357.work_history:
        if len(job.bullets) <= 3:
            short_entries.append((job.role, [job.role, *job.bullets]))
        else:
            long_entries.append((job.role, job.role, job.bullets))
    for edu in CV_357.education:
        short_entries.append((edu.degree, [edu.degree, edu.institution]))

    for label, probes in short_entries:
        located = {}
        for probe in probes:
            needle = _norm_probe(probe)
            hits = [i for i, page in enumerate(pages) if needle in page]
            assert hits, f"{template}: '{probe}' dropped from the rendered PDF"
            located[probe] = hits[0]

        distinct = sorted(set(located.values()))
        assert len(distinct) == 1, (
            f"{template}: the SHORT position block '{label}' is split across pages "
            f"{[p + 1 for p in distinct]} — "
            + "; ".join(
                f"'{probe[:40]}' on page {page + 1}" for probe, page in located.items()
            )
            + ". This is #357: a SHORT entry (<=3 bullets) must be atomic."
        )

    for label, title, bullets in long_entries:
        title_needle = _norm_probe(title)
        title_hits = [i for i, page in enumerate(pages) if title_needle in page]
        assert title_hits, f"{template}: '{title}' dropped from the rendered PDF"
        title_page = title_hits[0]

        bullet_pages = []
        for bullet in bullets:
            needle = _norm_probe(bullet)
            hits = [i for i, page in enumerate(pages) if needle in page]
            assert hits, f"{template}: '{bullet[:40]}' dropped from the rendered PDF"
            bullet_pages.append(hits[0])

        touched = set(bullet_pages) | {title_page}
        if len(touched) <= 1:
            continue  # this LONG entry fit on one page anyway — nothing to check

        assert bullet_pages[0] == title_page and bullet_pages[1] == title_page, (
            f"{template}: LONG position '{label}' spans pages {[p + 1 for p in sorted(touched)]} "
            f"but its title is on page {title_page + 1} while bullets 1/2 are on pages "
            f"{bullet_pages[0] + 1}/{bullet_pages[1] + 1}. This is #622: head + first 2 "
            f"bullets must be one keep-together group (.entry-lead)."
        )
        last_page = max(touched)
        count_on_last = sum(1 for p in bullet_pages if p == last_page)
        assert count_on_last >= 2, (
            f"{template}: LONG position '{label}''s last page ({last_page + 1}) holds only "
            f"{count_on_last} of its bullets. This is #622: the last 2 bullets must be one "
            f"keep-together group (.entry-tail)."
        )
