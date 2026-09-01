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

"""E057 task 1.3 (US296/US297, #637/#638, ADR-079 clause 5) — office-export
parity and round-trip CI gates.

Blocking, same status as ``tests/ats/test_roundtrip.py`` (ADR-039): a
``.docx`` export only ships if it survives this suite. Four properties,
each parametrized over BOTH document kinds (``_DOCUMENT_CASES`` — CV and
cover letter) rather than duplicated per kind: every test function below is
written against the generic ``_DocumentCase`` shape, never against
``TailoredCVData``/``LetterData`` by name, so the cover-letter half (added in
this same task once ``office_export/letter_docx.py`` merged) is one more
``_DocumentCase`` entry plus a ``LETTER_DE``/``LETTER_EN`` fixture pair, not
a second copy of any test.

**Sections 1 and 2 both check CONTAINMENT of a field's RAW value against the
`.docx` alone, and both are structurally blind to a defect class that slipped
through 60 pre-existing tests plus these two gates in their first form**:
commit ``8dc61254`` found the `.docx` CV writer printing `2017-04` where
every PDF template prints `04/2017` (`templates.filters.month_year` applied
on the PDF side only) — the SAME record showing two different dates
depending on which file the reader opens. A raw-value-containment check
cannot see this: the raw ISO value `"2017-04"` IS a substring of the buggy
`.docx` text either way a date is (mis)formatted, so it was never a useful
witness for this class of defect. **Section 3 exists because of that
finding** — a genuine differential between the real PDF and the real
`.docx` for the same fixture, not a second read of either against the raw
data. Mutation-verified in this task's report: reverting the writer to its
pre-fix date rule leaves sections 1 and 2 green and only section 3 goes red.

1. **Round-trip** (``test_office_export_roundtrip_zero_failures``): render
   -> extract -> run the UNCHANGED ADR-039 audit (``audit_cv_docx`` /
   ``audit_cover_letter_docx``, ADR-066 — this file never reimplements the
   audit) -> zero ``fail`` checks, DE x EN, for both document kinds. The
   ``.docx`` twin of ``test_roundtrip.py``'s ``test_cv_template_roundtrip``.
   Also asserts the page-length band explicitly: exactly one ``page-length``
   check, ``status="not_applicable"``, and ``report.not_applicable == 1``
   (ADR-079 clause 4 — a ``.docx`` has no fixed pagination until a word
   processor lays it out, so the band is reported N/A WITH its reason,
   never silently absent — the #634 failure shape — and never folded into
   ``passed``/``failed``). Checking this explicitly, not just tolerating it
   via "no `fail` status", matters: a regression that stopped setting
   ``page_band_not_applicable=True`` would make the band ABSENT rather than
   failed, which the zero-failures assertion alone would not catch.

2. **Section parity** (``test_office_export_section_parity_survives_extraction``),
   with the expected section SET derived MECHANICALLY from
   ``TailoredCVData.model_fields`` / ``LetterData.model_fields`` — never a
   hand-typed section list (ADR-079 clause 5's own wording).

   This is DELIBERATELY a different guard from
   ``tests/unit/test_office_export_cv_docx.py::TestSectionCoverageGuard`` /
   ``test_office_export_letter_docx.py``'s equivalent: those prove the
   writer's ``_SECTION_RENDERERS`` dispatch table has an entry for every
   schema field — a fact about the CALL GRAPH, checked without ever
   producing a document. This one proves the PRODUCED ARTEFACT's extracted
   text actually carries that section's content: a renderer can be
   registered and still emit nothing (a gutted branch, a swallowed
   exception, a blank template string) and the call-graph guard would still
   pass. Neither substitutes for the other; this file never touches
   ``tests/unit/`` and never re-derives what those guards already prove.

   Why this is NOT redundant with (1) either — measured, not assumed:
   ``_audit_cv_text``/``_audit_letter_text`` (``ats_audit.py``) were built
   for ATS-style structured/keyword checks, not full section coverage. Read
   against this file's own fixtures: the CV audit has NO check id at all
   for ``certifications`` or ``languages``, and no check for a project's
   ``name`` (only its ``bullets``, via ``_free_text_snippets``); the letter
   audit checks only ``header.name``/``header.email``/``recipient.company``
   /``body.paragraphs`` — NOT ``header.address``/``header.phone``,
   ``recipient.name``/``.title``/``.address``/``.date``, and NOT
   ``signature`` at all. A writer that silently dropped ``certifications``
   (CV) or the entire ``signature`` block (letter) would still pass gate
   (1) with zero failures — mutation-verified for both in this task's
   report. Gate (2) is the only one of the two that would catch it.

   🔒 Architecture Boundary (task 1.3): the gate must cover the SCHEMA's
   shape, not the fix's — LIST sections (``work_history``, ``education``,
   ...) AND the one nested-OBJECT section (``contact``) AND a plain-scalar
   section (``summary``) alike (SF-PROFILE.8's lesson: a LIST-only gate
   could not see ``professional_summary``). ``_missing_sections`` below
   walks every top-level field's DUMPED VALUE generically (dict / list /
   str, via ``.model_dump()``) with no per-field special case, so it
   structurally cannot be narrowed to "list fields only" by accident. A
   field with nothing to say for itself in a given fixture (``show_photo:
   bool`` contributes no string leaves) silently contributes zero probes
   rather than needing an explicit exclusion list — itself mechanical: the
   one production list of "this field is structural, not content" per
   writer (``cv_docx._NON_SECTION_FIELDS`` / ``letter_docx._NON_SECTION_FIELDS``,
   the latter empty — LetterData has no non-content field) stays owned by
   the writer, never duplicated here. A SEPARATE test
   (``test_fixtures_exercise_every_content_bearing_section``) guards the
   fixtures themselves against silently drifting empty.

3. **Cross-artifact content differential**
   (``test_office_export_pdf_docx_content_differential``) — renders the SAME
   fixture through the real PDF pipeline (one representative template,
   ``classic_german``, via ``_html_to_pdf``/Playwright — the exact mechanics
   ``test_roundtrip.py`` already uses per template) and through the `.docx`
   writer, extracts both, and flags any of the candidate's own data leaves
   whose raw-value presence DISAGREES between the two — present in one,
   absent from the other. Scoped to the candidate's DATA (never template
   labels/chrome, which live in a separate dictionary this never walks) —
   see ``test_office_export_pdf_docx_content_differential``'s own docstring
   for why this is not a full whole-document diff (ADR-079 clause 3 permits
   real structural differences between the two artifacts) and for the one
   disclosed blind spot (a field transformed differently, rather than
   inconsistently, on each side).

4. **Page count** (``test_office_export_page_count_within_region_norm``),
   asserted by REAL conversion (``soffice --headless --convert-to pdf``,
   isolated ``-env:UserInstallation`` profile — a shared profile silently
   drops one of two concurrent conversions, measured in the ADR-079 spike)
   against the region's own page norm (``REGION_NORMS[DEFAULT_REGION]
   .cv_max_pages`` / ``.letter_pages`` — never a hand-picked number,
   ADR-051 §1) — deliberately NOT an exact-page-count equality. See
   ``Documents/Runs/Stracciatella/office-export/2026-08-31-page-count-gate-portability.md``
   for the underlying font-substitution measurement this design is based
   on, and the test's own docstring for the numbers actually measured
   against THIS file's fixtures (which differ from — and are more
   conservative than — a same-sized comparison against that spike's
   synthetic BULLET sweep; bullets and prose paragraphs are not the same
   unit of vertical space, and this file does not assume they are).

   The CI job (workflow diff in this task's report) installs LibreOffice
   AND ``fonts-crosextra-carlito`` — the metric-compatible Calibri
   substitute Ubuntu ships (``_common.py``'s ``BASE_FONT_NAME``) — so the
   font substitution measured on the dev host reproduces in CI instead of
   being asserted across an unknown one, mirroring the ``poppler-utils``
   precedent already in that job.

No HTML, no template engine anywhere in this file's own logic (ADR-079
clause 2) — ``soffice`` is invoked ONLY to convert an already-produced
``.docx`` to PDF for page counting, never to render content.
"""

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest
from pypdf import PdfReader

from applire.norms import DEFAULT_REGION, REGION_NORMS
from applire.schemas.ats import ATSReport
from applire.schemas.cover_letter import LetterData
from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import _norm, extract_text
from applire.services.color_detection import _default_context
from applire.services.cover_letter import _TEMPLATE_FILES as LETTER_PDF_TEMPLATES
from applire.services.cover_letter import _default_color_context
from applire.services.cv import _TEMPLATE_FILES as CV_PDF_TEMPLATES
# _html_to_pdf / _jinja_env are generic HTML->PDF/Jinja machinery defined
# ONCE in services.cv and reused for both document kinds (test_roundtrip.py's
# own import pattern — cover_letter.py has no separate copy of either).
from applire.services.cv import _html_to_pdf, _jinja_env
from applire.services.office_export.cv_docx import _NON_SECTION_FIELDS as _CV_NON_SECTION_FIELDS
from applire.services.office_export.cv_docx import render_cv_docx
from applire.services.office_export.extract import (
    audit_cover_letter_docx,
    audit_cv_docx,
    extract_docx_text,
)
from applire.services.office_export.letter_docx import (
    _NON_SECTION_FIELDS as _LETTER_NON_SECTION_FIELDS,
)
from applire.services.office_export.letter_docx import render_letter_docx
from applire.templates.filters import month_year
from applire.templates.labels import cover_letter_labels, cv_labels

ACCENT_COLOR = "#2c3e50"
KEYWORDS = ["Python", "Kubernetes", "Projektmanagement"]

# The one PDF template used as the "ground truth" rendering for the
# PDF-vs-docx differential (test_office_export_pdf_docx_content_differential
# below) -- not a re-run of test_roundtrip.py's own per-template suite (that
# suite already proves every template renders correctly); this differential
# exists to compare the .docx writer against SOME real member of the shared
# rendering pipeline, and all seven CV templates apply templates.filters
# .month_year identically (commit 8dc61254's own claim, spot-checked against
# lebenslauf.html.j2's source directly), so one representative is enough.
_DIFFERENTIAL_TEMPLATE = "classic_german"


def _norm_probe(s: str) -> str:
    """Mirror ``ats_audit`` normalisation — the SAME convention
    ``tests/ats/test_roundtrip.py`` already uses for its own hand-written
    assertions (as opposed to the audit's internal ``_find``, which adds
    kerning tolerance PDF extraction needs and ``.docx`` extraction does
    not: this writer emits one clean run per paragraph, so plain
    normalised-substring containment is the right, simpler predicate)."""
    return _norm(s)


def _string_leaves(value: Any) -> list[str]:
    """Every non-blank string leaf inside `value`, walked generically off a
    pydantic ``.model_dump()``-shaped structure (dict / list / str / other).

    No per-field knowledge: a dict recurses into its values, a list
    recurses into its elements, a str is a leaf (dropped if blank), and
    anything else (bool, int, None) contributes nothing. This is what lets
    `_missing_sections` cover a LIST section, the one nested-OBJECT section
    and a plain-scalar section through the SAME code path (task 1.3's 🔒
    boundary) instead of three special cases that could individually be
    narrowed or forgotten.
    """
    if isinstance(value, dict):
        out: list[str] = []
        for v in value.values():
            out.extend(_string_leaves(v))
        return out
    if isinstance(value, list):
        out = []
        for v in value:
            out.extend(_string_leaves(v))
        return out
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _content_present(probe: str, text_norm: str) -> bool:
    """True if `probe`'s RAW form, OR its ``month_year()``-transformed form,
    is present in `text_norm`. Gate 2 (`_missing_sections`, this function's
    only caller) exists to prove a FACT survives the round trip in SOME
    form -- never to judge which form is correct, which is gate 3's job
    (`_presence_disagreements`, which deliberately does NOT use this
    tolerant check: format correctness needs the strict, single-form
    comparison this function intentionally loosens).

    Without this tolerance, gate 2 breaks on its own fixtures the moment a
    date is rendered CORRECTLY: post-fix (commit 8dc61254), a CV date reads
    "03/2018" rather than the raw "2018-03", so a strict raw-value
    containment check would report every dated entry as content-missing —
    a false positive on correct output, discovered by running gate 2 after
    merging that fix and seeing exactly this failure. `month_year` is safe
    to apply universally (not just to known date fields): its own docstring
    and doctests guarantee it returns non-date strings UNCHANGED (a company
    name, a bullet, an email are never a recognised partial-date pattern),
    so this adds no tolerance where none is wanted -- it only additionally
    accepts the transformed form where a raw ISO date would otherwise
    (correctly) never appear.
    """
    if _norm_probe(probe) in text_norm:
        return True
    return _norm_probe(month_year(probe)) in text_norm


def _missing_sections(model_cls: type, instance: Any, text_norm: str) -> list[tuple[str, list[str]]]:
    """For every TOP-LEVEL field in ``model_cls.model_fields`` (mechanically
    — never a hand-typed list, ADR-079 clause 5), collect that field's
    string leaves from `instance` and report which ones are absent from
    `text_norm` (already `_norm_probe`-normalised) IN EITHER their raw or
    `month_year`-transformed form (`_content_present` — content survival,
    not format correctness; see that function's docstring).

    Returns ``[(field_name, [missing_probe, ...]), ...]`` for fields with at
    least one missing probe. A field that contributes zero probes in this
    fixture (nothing to say for itself, or a structural non-content field
    like `show_photo`) is silently skipped — not a false pass, since there
    is nothing to assert about it either way (see
    `test_fixtures_exercise_every_content_bearing_section`, which guards
    against a fixture drifting into that state by accident).
    """
    dumped = instance.model_dump()
    missing: list[tuple[str, list[str]]] = []
    for field_name in sorted(model_cls.model_fields):
        probes = _string_leaves(dumped[field_name])
        gaps = [p for p in probes if not _content_present(p, text_norm)]
        if gaps:
            missing.append((field_name, gaps))
    return missing


def _iter_leaf_values(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Like `_string_leaves`, but also carries a diagnostic PATH per leaf —
    `_presence_disagreements` below needs it to report which specific field
    diverged, not just that something did.
    """
    if isinstance(value, dict):
        out: list[tuple[str, str]] = []
        for k, v in value.items():
            out.extend(_iter_leaf_values(v, f"{path}.{k}" if path else k))
        return out
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out.extend(_iter_leaf_values(v, f"{path}[{i}]"))
        return out
    if isinstance(value, str) and value.strip():
        return [(path, value)]
    return []


def _presence_disagreements(
    instance: Any, pdf_text_norm: str, docx_text_norm: str
) -> list[tuple[str, str, bool, bool]]:
    """The genuine cross-artifact differential (coordinator direction,
    SF-EXPORT.2): for every leaf `_iter_leaf_values` finds in `instance`,
    report leaves where the RAW value's presence DISAGREES between the two
    artifacts' extracted text — present in exactly one, absent from the
    other. Returns ``[(path, value, in_pdf, in_docx), ...]``.

    This is deliberately NOT "does the raw value appear in the docx" (that
    is `_missing_sections` above, and it is structurally blind to exactly
    the defect class this function exists for): a field with NO display
    transform is expected to appear verbatim in BOTH artifacts, so
    `in_pdf == in_docx == True` and there is no disagreement. A field
    transformed IDENTICALLY on both sides (dates, post-fix: both apply
    `templates.filters.month_year`) has its RAW ISO value absent from
    BOTH extractions -- `in_pdf == in_docx == False`, also no
    disagreement, because a transform that renders the same way on both
    sides is not a defect. A field transformed on only ONE side --
    commit 8dc61254's actual bug, "the writer had its own date rule
    instead of the shared templates.filters.month_year" -- leaves the raw
    value present on the untransformed side and absent on the
    transformed side: `in_pdf != in_docx`, caught here without this
    function needing to know ahead of time that dates specifically are
    the risky field. Any FUTURE field that grows a display transform on
    one side only reproduces the identical shape and is caught the same
    way -- this is not a special case for dates.

    Known, deliberate blind spot (disclosed, not silently accepted): if a
    field were transformed DIFFERENTLY on each side (rather than "one side
    transforms, one side does not"), the raw value would be absent from
    BOTH and this function would see no disagreement, even though the two
    artifacts show genuinely different text. That is a real but narrower
    failure mode than the one just found, and this file's report names it
    explicitly rather than claiming this function catches every possible
    divergence.
    """
    disagreements: list[tuple[str, str, bool, bool]] = []
    for path, value in _iter_leaf_values(instance.model_dump()):
        needle = _norm_probe(value)
        in_pdf = needle in pdf_text_norm
        in_docx = needle in docx_text_norm
        if in_pdf != in_docx:
            disagreements.append((path, value, in_pdf, in_docx))
    return disagreements


# ---------------------------------------------------------------------------
# CV fixtures — DACH-realistic content with umlauts/ß (DE) and a distinct EN
# fixture, BOTH populating every content-bearing top-level field (contact,
# summary, work_history incl. a nested project, skills, education, languages,
# a STANDALONE project, certifications) so the section-parity gate in both
# languages actually exercises every section — a fixture that left one
# section empty would make that section's probe list vacuous for that
# language (see `_missing_sections`'s own docstring on why an empty probe
# list is not a false pass, but also asserts nothing;
# `test_fixtures_exercise_every_content_bearing_section` guards this).
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
                ],
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
        ],
        "skills": [
            "Python", "Kubernetes", "Projektmanagement", "Six Sigma", "VDA 6.3", "Messtechnik",
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
        "projects": [
            {
                "name": "Open-Source Messdaten-Toolkit",
                "bullets": ["Veröffentlichung eines Python-Pakets zur Messdatenanalyse."],
            }
        ],
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
                ],
                "projects": [
                    {
                        "name": "Zero-Downtime Migration Initiative",
                        "bullets": [
                            "Designed the blue-green rollout strategy for the platform migration.",
                        ],
                    }
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
        ],
        "skills": [
            "Python", "Kubernetes", "Terraform", "PostgreSQL", "Observability", "CI/CD",
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
        "projects": [
            {
                "name": "Open-Source Observability Toolkit",
                "bullets": ["Published a Python package for latency-budget analysis."],
            }
        ],
        "certifications": [
            {
                "name": "Certified Kubernetes Administrator",
                "issuing_organization": "CNCF",
                "date_obtained": "2022-03-01",
            }
        ],
    }
)


# ---------------------------------------------------------------------------
# Cover-letter fixtures — same persona pairs as the CV fixtures above (DE:
# Jörg Müller-Lüdenscheidt applying to Süddeutsche Präzisionstechnik; EN:
# Catherine O'Brien applying to Müller & Söhne AG), so the two document
# kinds' fixtures tell one consistent story. Both populate all FOUR
# LetterData top-level fields (header, recipient, body, signature) — see
# the CV fixture block comment above for why that matters to the
# section-parity gate. Body length (4 paragraphs) matches the ADR-079
# spike's own calibration point ("15 paragraphs" total, 1 page) — see
# `test_office_export_page_count_within_region_norm`'s docstring for the
# real conversion measurement this shape produces.
# ---------------------------------------------------------------------------

LETTER_DE = LetterData.model_validate(
    {
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
                    "mit großem Interesse habe ich Ihre Ausschreibung für die Position "
                    "als Leiter Qualitätssicherung gelesen und bewerbe mich hiermit um "
                    "diese verantwortungsvolle Aufgabe in Ihrem Hause."
                ),
                (
                    "In meiner aktuellen Tätigkeit verantworte ich das Projektmanagement "
                    "und die Prozessoptimierung über drei Fertigungsstandorte hinweg. "
                    "Dabei konnte ich die Ausschussquote durch konsequente statistische "
                    "Prozesslenkung deutlich senken."
                ),
                (
                    "Meine fundierten Kenntnisse in Python und der Aufbau automatisierter "
                    "Messdaten-Workflows ermöglichen es mir, Qualitätsdaten effizient "
                    "auszuwerten und fundierte Entscheidungen zu treffen."
                ),
                (
                    "Über die Gelegenheit zu einem persönlichen Gespräch würde ich mich "
                    "sehr freuen und stehe Ihnen für Rückfragen jederzeit gerne zur "
                    "Verfügung."
                ),
            ]
        },
        "signature": {"closing": "Mit freundlichen Grüßen", "name": "Jörg Müller-Lüdenscheidt"},
    }
)

LETTER_EN = LetterData.model_validate(
    {
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
                    "I am writing to express my strong interest in the Lead Platform "
                    "Engineer role at your company, where I believe my background in "
                    "cloud infrastructure would make an immediate impact."
                ),
                (
                    "Over the past decade I have led the migration of large service "
                    "estates onto managed Kubernetes platforms and championed "
                    "infrastructure-as-code practices that dramatically shortened "
                    "delivery cycles."
                ),
                (
                    "My day-to-day work combines hands-on engineering in Python with "
                    "the project management discipline needed to keep cross-functional "
                    "teams aligned on shared reliability goals."
                ),
                (
                    "I would welcome the opportunity to discuss how my experience can "
                    "support your platform ambitions and am happy to provide any "
                    "further information you need."
                ),
            ]
        },
        "signature": {"closing": "Kind regards", "name": "Catherine O'Brien"},
    }
)


def _bulk_cv() -> TailoredCVData:
    """A CV with FAR more content than any realistic candidate — 10 work
    entries x 10 bullets each (100 bullets total). Used ONLY by the
    page-count gate's own mutation-verification test, never by the parity
    gates above (its other sections are deliberately empty — irrelevant to
    what it exists to prove). Measured (this exact fixture, this file, this
    host's font substitution): **6 pages**, against the DACH `cv_max_pages`
    bound of 3.
    """
    return TailoredCVData.model_validate(
        {
            "contact": {
                "name": "Bulk Testperson", "email": "bulk@example.de",
                "phone": "+49 89 1234567", "location": "München", "photo_url": None,
            },
            "show_photo": False,
            "summary": "Testfixture zur Kalibrierung der Seitenzahl-Schranke — kein echter Kandidat.",
            "work_history": [
                {
                    "company": f"Firma {j:02d} Präzisionstechnik GmbH",
                    "role": f"Position {j:02d} Qualitätsingenieur",
                    "start_date": f"{2000 + j}-01",
                    "end_date": f"{2001 + j}-01",
                    "bullets": [
                        f"Verantwortung Beleg{j:02d}{b:02d} für die statistische "
                        f"Prozesslenkung der Serienfertigung über mehrere "
                        f"Fertigungslinien hinweg."
                        for b in range(10)
                    ],
                }
                for j in range(10)
            ],
            "skills": ["Python"],
            "education": [],
            "languages": [],
            "projects": [],
            "certifications": [],
        }
    )


def _bulk_letter() -> LetterData:
    """`LETTER_DE` plus 12 extra body paragraphs. Used ONLY by the
    page-count gate's own mutation-verification test. Measured (this exact
    fixture, this file, this host's font substitution): **2 pages**,
    against the DACH `letter_pages` hard bound of 1 — see
    `test_office_export_page_count_within_region_norm`'s docstring for the
    full sweep this specific count (+12) was picked from (the 1->2 page
    flip for THIS letter shape lands at +5 extra paragraphs; +12 gives
    margin above that flip point without relying on an extreme fixture).
    """
    pad = (
        "Diese zusätzliche Erfahrung im Bereich Qualitätsmanagement und "
        "Prozessoptimierung rundet mein Profil weiter ab und zeigt meine "
        "Bereitschaft, Verantwortung für anspruchsvolle Aufgaben zu "
        "übernehmen und Projekte über mehrere Standorte hinweg erfolgreich "
        "zum Abschluss zu bringen."
    )
    dumped = LETTER_DE.model_dump()
    dumped["body"]["paragraphs"] = list(dumped["body"]["paragraphs"]) + [pad] * 12
    return LetterData.model_validate(dumped)


# ---------------------------------------------------------------------------
# One entry per document kind. Every test function below is written against
# this generic shape, never against `TailoredCVData`/`LetterData` by name.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DocumentCase:
    kind: str  # pytest id prefix, e.g. "cv" / "letter"
    document_field: str  # expected ATSReport.document value ("cv" / "cover_letter")
    model_cls: type
    non_section_fields: frozenset  # this writer's own "structural, not content" set
    render: Callable[[Any, str], bytes]       # (instance, lang) -> docx bytes
    audit: Callable[[bytes, Any], ATSReport]  # (docx_bytes, instance) -> report
    render_pdf: Callable[[Any, str], Awaitable[bytes]]  # (instance, lang) -> real PDF bytes,
    # via the SAME shared Jinja/Playwright pipeline the product ships -- the
    # "ground truth" side of the differential (SF-EXPORT.2).
    fixtures: dict[str, Any]                  # {"de": ..., "en": ...}
    inflate: Callable[[], Any]                # () -> an instance that exceeds page_bound
    page_bound: int
    page_bound_label: str  # for assertion messages, e.g. "REGION_NORMS[DACH].cv_max_pages"


async def _render_cv_pdf(tailored: TailoredCVData, lang: str) -> bytes:
    html = _jinja_env.get_template(CV_PDF_TEMPLATES[_DIFFERENTIAL_TEMPLATE]).render(
        cv=tailored, color=_default_context(), lang=lang, labels=cv_labels(lang)
    )
    return await _html_to_pdf(html)


async def _render_letter_pdf(letter: LetterData, lang: str) -> bytes:
    html = _jinja_env.get_template(LETTER_PDF_TEMPLATES[_DIFFERENTIAL_TEMPLATE]).render(
        letter=letter.model_dump(), color=_default_color_context(), lang=lang,
        labels=cover_letter_labels(lang), subject="Bewerbung" if lang == "de" else "Application",
    )
    return await _html_to_pdf(html)


_CV_CASE = _DocumentCase(
    kind="cv",
    document_field="cv",
    model_cls=TailoredCVData,
    non_section_fields=_CV_NON_SECTION_FIELDS,
    render=lambda tailored, lang: render_cv_docx(tailored, lang=lang, accent_color=ACCENT_COLOR),
    audit=lambda docx_bytes, tailored: audit_cv_docx(docx_bytes, tailored, KEYWORDS),
    render_pdf=_render_cv_pdf,
    fixtures={"de": CV_DE, "en": CV_EN},
    inflate=_bulk_cv,
    page_bound=REGION_NORMS[DEFAULT_REGION].cv_max_pages,
    page_bound_label=f"REGION_NORMS[{DEFAULT_REGION}].cv_max_pages",
)

_LETTER_CASE = _DocumentCase(
    kind="letter",
    document_field="cover_letter",
    model_cls=LetterData,
    non_section_fields=_LETTER_NON_SECTION_FIELDS,
    render=lambda letter, lang: render_letter_docx(letter, lang=lang, accent_color=ACCENT_COLOR),
    # _audit_letter_text (and therefore audit_cover_letter_docx) takes
    # letter_data as a plain dict, unlike audit_cv_docx's TailoredCVData
    # instance — .model_dump() bridges that, matching how services.cover_letter
    # itself always carries letter_data as a dict, never a LetterData instance,
    # until the US249 agent-door validation boundary.
    audit=lambda docx_bytes, letter: audit_cover_letter_docx(docx_bytes, letter.model_dump(), KEYWORDS),
    render_pdf=_render_letter_pdf,
    fixtures={"de": LETTER_DE, "en": LETTER_EN},
    inflate=_bulk_letter,
    page_bound=REGION_NORMS[DEFAULT_REGION].letter_pages,
    page_bound_label=f"REGION_NORMS[{DEFAULT_REGION}].letter_pages",
)

_DOCUMENT_CASES = [_CV_CASE, _LETTER_CASE]

_CASE_LANG_PARAMS = [(case, lang) for case in _DOCUMENT_CASES for lang in sorted(case.fixtures)]
_CASE_LANG_IDS = [f"{case.kind}-{lang}" for case, lang in _CASE_LANG_PARAMS]
_CASE_IDS = [case.kind for case in _DOCUMENT_CASES]


# ---------------------------------------------------------------------------
# 1. Round-trip gate (ADR-039, extended to the office artefact — ADR-079 cl. 5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case,lang", _CASE_LANG_PARAMS, ids=_CASE_LANG_IDS)
def test_office_export_roundtrip_zero_failures(case, lang):
    """The `.docx` twin of `test_roundtrip.py::test_cv_template_roundtrip`
    (and its letter counterpart): render -> extract -> the UNCHANGED
    ADR-039 audit -> zero `fail` checks. Blocking: a `.docx` export only
    ships if it survives this. Also asserts the page-length band's explicit
    `not_applicable` shape (ADR-079 clause 4) — see module docstring for
    why that needs its own assertion rather than riding along on
    "no failures"."""
    instance = case.fixtures[lang]
    docx_bytes = case.render(instance, lang)
    report = case.audit(docx_bytes, instance)

    assert report.document == case.document_field, (
        f"{case.kind}-{lang}: audit reported document={report.document!r}, "
        f"expected {case.document_field!r} — wrong audit function wired to this case"
    )

    # Assert the REPORT'S OWN `failed` counter — the assertion that actually
    # matters (`_finish()` computes it as `sum(1 for c in checks if
    # c.status == "fail")`) — not "every check passed": there is no such
    # single boolean on ATSReport, and there must not be one that miscounts
    # the `not_applicable` page-length band as a problem. A `.docx` report
    # legitimately contains a `not_applicable` check and must still read
    # `failed == 0`; this is exactly the ADR-079 clause 4 shape, asserted
    # directly rather than via a derived list that would happen to agree
    # with it but not actually pin `_finish()`'s own computation.
    failures = [(c.id, c.status, c.details) for c in report.checks if c.status == "fail"]
    assert report.failed == 0, f"{case.kind}-{lang}: report.failed={report.failed}, checks: {failures}"

    page_checks = [c for c in report.checks if c.id == "page-length"]
    assert len(page_checks) == 1, (
        f"{case.kind}-{lang}: expected exactly one 'page-length' check, got "
        f"{len(page_checks)}: {page_checks}"
    )
    assert page_checks[0].status == "not_applicable", (
        f"{case.kind}-{lang}: 'page-length' check status is "
        f"{page_checks[0].status!r}, expected 'not_applicable' (ADR-079 "
        f"clause 4 — a .docx has no fixed pagination until a word processor "
        f"lays it out)"
    )
    assert report.not_applicable == 1, (
        f"{case.kind}-{lang}: report.not_applicable={report.not_applicable!r}, "
        f"expected 1 (the page-length band, counted in its own bucket, "
        f"never folded into passed/failed)"
    )


# ---------------------------------------------------------------------------
# 2. Section parity, derived mechanically from `model_cls.model_fields`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _DOCUMENT_CASES, ids=_CASE_IDS)
def test_fixtures_exercise_every_content_bearing_section(case):
    """Fixture-completeness guard — a different failure mode from anything
    else in this file. `_missing_sections` silently contributes zero probes
    for a field that is empty in a given fixture (correct: there is nothing
    to assert either way — see its own docstring). That is safe TODAY
    because every fixture above was deliberately built to populate every
    content field, but it means the section-parity gate's completeness
    rests on the FIXTURE's completeness: if a fixture is edited later and a
    section is left empty, `test_office_export_section_parity_survives_
    extraction` would silently stop checking that section for that fixture
    — no red anywhere, the same 'vacuous pass from an empty positive set'
    shape the checker guard-the-guard test below protects against, but from
    fixture drift instead of a checker regression. This test closes that
    gap by asserting every content-bearing field contributes at least one
    probe in EVERY fixture (not just the union across DE/EN), so a future
    edit that empties a section fails HERE with a clear reason, not
    silently downstream.

    `case.non_section_fields` is each writer's own production list of "this
    field is structural, not content" (imported, not re-typed) — reusing it
    is what keeps this fixture-quality check from becoming a second
    hand-maintained list of section names, which would defeat its purpose.
    """
    for lang, instance in case.fixtures.items():
        dumped = instance.model_dump()
        empty_sections = [
            field_name
            for field_name in case.model_cls.model_fields
            if field_name not in case.non_section_fields and not _string_leaves(dumped[field_name])
        ]
        assert not empty_sections, (
            f"{case.kind}-{lang}: fixture has no probeable content for "
            f"{empty_sections} — the section-parity gate cannot exercise "
            f"these sections against this fixture; add content to the "
            f"fixture rather than leaving the gap silent"
        )


@pytest.mark.parametrize("case,lang", _CASE_LANG_PARAMS, ids=_CASE_LANG_IDS)
def test_office_export_section_parity_survives_extraction(case, lang):
    """ADR-079 clause 5 — every top-level schema field's content (LIST and
    OBJECT/scalar sections alike, task 1.3's 🔒 boundary) survives the
    `.docx` round trip, with the expected section SET derived mechanically
    from `case.model_cls.model_fields` rather than hand-listed. See the
    module docstring for why this is a different, non-redundant guard from
    both the `tests/unit/` call-graph coverage guards and gate (1) above."""
    instance = case.fixtures[lang]
    docx_bytes = case.render(instance, lang)
    text_norm = _norm_probe(extract_docx_text(docx_bytes))
    missing = _missing_sections(case.model_cls, instance, text_norm)
    assert not missing, (
        f"{case.kind}-{lang}: section(s) with content missing from the "
        f"extracted .docx text: {missing}"
    )


def test_section_parity_checker_detects_a_stripped_section():
    """Guard-the-guard (task 1.3 cross-cutting note, the #619 gate's own
    shape): prove `_missing_sections` can still report a gap, so a matching
    regression in it (e.g. a normalisation change broad enough to make
    every probe trivially match) cannot make
    `test_office_export_section_parity_survives_extraction` vacuously
    green. One case (CV) suffices — `_missing_sections` has no per-document-
    kind branch, so this exercises the SAME code path
    `test_office_export_section_parity_survives_extraction[letter-*]` uses.
    Pure text-level mutation on the REAL render's extracted text — the
    writer itself is untouched here (see this task's report for the
    SEPARATE scratchpad-copy mutations against the real CV and letter
    writers, which prove the same thing end-to-end rather than at just this
    checker's seam).

    Also proves the checker does not cross-contaminate between sections:
    stripping ONLY `certifications`' own probe text must report ONLY
    `certifications` as missing, not every section (which a checker with a
    single shared "have I found anything at all" flag could do) and not
    nothing (a checker that always reports empty)."""
    instance = _CV_CASE.fixtures["de"]
    docx_bytes = _CV_CASE.render(instance, "de")
    text_norm = _norm_probe(extract_docx_text(docx_bytes))

    # Positive control: the real render has nothing missing.
    assert not _missing_sections(_CV_CASE.model_cls, instance, text_norm)

    cert_probes = _string_leaves(instance.model_dump()["certifications"])
    assert cert_probes, "fixture sanity: certifications must carry probeable content"
    stripped = text_norm
    for probe in cert_probes:
        stripped = stripped.replace(_norm_probe(probe), "")

    missing = _missing_sections(_CV_CASE.model_cls, instance, stripped)
    missing_fields = {field for field, _ in missing}
    assert missing_fields == {"certifications"}, (
        f"expected stripping certifications' own text to report ONLY "
        f"'certifications' missing; got {missing_fields!r} — either the "
        f"checker cannot detect a real gap (vacuous pass) or it is "
        f"cross-contaminating between sections"
    )


# ---------------------------------------------------------------------------
# 3. Cross-artifact content differential (SF-EXPORT.2) — added after a real
# export was converted and read: the .docx wrote "2017-04" where all seven
# PDF templates write "04/2017" (commit 8dc61254). Sections 1 and 2 above
# both check CONTAINMENT of a field's RAW value against the .docx alone --
# the raw value is present either way a date is (mis)formatted, so neither
# could have seen this, any more than the 60 pre-existing writer-suite tests
# could (mutation-verified in this task's report: reverting the fix leaves
# gates 1 and 2 green and only this section goes red). This section renders
# the SAME fixture through the real PDF pipeline too and compares what each
# artifact ACTUALLY contains, rather than comparing each independently
# against the fixture's raw data.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("case,lang", _CASE_LANG_PARAMS, ids=_CASE_LANG_IDS)
async def test_office_export_pdf_docx_content_differential(case, lang):
    """SF-EXPORT.2 / commit 8dc61254 — a genuine differential between the
    two real artifacts, not a re-check of each against the fixture's raw
    data. Renders `classic_german` (CV) / `classic_german` (letter) via the
    SAME Playwright/Jinja pipeline `test_roundtrip.py` exercises per
    template, and the `.docx` via `case.render`, for the identical fixture.
    `_presence_disagreements` flags any leaf whose raw value's presence
    disagrees between the two extracted texts — see its own docstring for
    why that catches "one side transforms a value, the other doesn't"
    (exactly what broke) without needing to know in advance which field
    would be the risky one.

    Scope, stated rather than silently narrowed (coordinator direction): this
    is NOT a full whole-document text diff of everything in both artifacts.
    ADR-079 clause 3 is explicit that the export "does not reproduce the
    chosen template's layout" -- some structural/wording differences between
    the two are BY DESIGN (this writer has no icons, ADR-020; a template may
    order or join elements differently). A naive full-text set-diff would
    need a curated allow-list of expected differences that does not exist
    yet and untested, so this compares ONLY the candidate's own DATA leaves
    (`instance.model_dump()`'s string leaves — company names, bullets,
    dates, ...), never template chrome/labels (which are a SEPARATE
    dictionary, `cv_labels()`/`cover_letter_labels()`, not walked here at
    all). Within that scope the check is general, not date-specific: ANY
    leaf transformed on one side and not the other reproduces the identical
    disagreement shape and is caught the same way.
    """
    instance = case.fixtures[lang]
    docx_bytes = case.render(instance, lang)
    docx_text = _norm_probe(extract_docx_text(docx_bytes))

    pdf_bytes = await case.render_pdf(instance, lang)
    pdf_text = _norm_probe(extract_text(pdf_bytes))

    disagreements = _presence_disagreements(instance, pdf_text, docx_text)
    assert not disagreements, (
        f"{case.kind}-{lang}: content present in exactly ONE of the two "
        f"artifacts for the same record (path, raw value, in_pdf, in_docx): "
        f"{disagreements}"
    )


# ---------------------------------------------------------------------------
# 4. Page-count gate, asserted by REAL conversion (LibreOffice headless)
# ---------------------------------------------------------------------------


def _require_soffice() -> str:
    """Locate the `soffice` binary or fail HARD — mirrors the poppler-utils
    precedent in `.github/workflows/test.yml` (see this task's report for
    the exact diff): for a suite whose whole point is that the page-count
    claim is backed by a real conversion, a skip would be worse than a
    failure. The CI job installs `libreoffice-writer`, so this must resolve
    there; running `pytest -rs` locally without LibreOffice installed sees
    this as a hard FAILURE, not a skip — deliberately, per this file's
    module docstring."""
    path = shutil.which("soffice")
    if not path:
        pytest.fail(
            "soffice (LibreOffice headless) not found on PATH — the .docx "
            "page-count gate (ADR-079 clause 5) cannot run without it. "
            "This is a hard failure, not a skip: install libreoffice-writer "
            "(Debian/Ubuntu: `apt-get install libreoffice-writer`), or run "
            "inside CI, which installs it for exactly this job."
        )
    return path


def _pages_for_docx(docx_bytes: bytes) -> int:
    """Convert `docx_bytes` to PDF with a REAL headless LibreOffice and
    return its page count.

    Isolated `-env:UserInstallation` profile per call — REQUIRED, not an
    optimisation: measured in the ADR-079 spike, a profile shared between
    two concurrent conversions silently drops one of them (exit 1, no
    output). A fresh temp dir per call gives every conversion its own
    profile, so this is safe to call from parallel test runs too.
    """
    soffice = _require_soffice()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        docx_path = tmp_path / "document.docx"
        docx_path.write_bytes(docx_bytes)
        profile_dir = tmp_path / "profile"
        result = subprocess.run(
            [
                soffice, "--headless",
                f"-env:UserInstallation=file://{profile_dir}",
                "--convert-to", "pdf",
                "--outdir", str(tmp_path),
                str(docx_path),
            ],
            capture_output=True, text=True, timeout=60,
        )
        pdf_path = tmp_path / "document.pdf"
        if result.returncode != 0 or not pdf_path.exists():
            pytest.fail(
                f"soffice conversion failed (exit {result.returncode}): "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        return len(PdfReader(str(pdf_path)).pages)


@pytest.mark.parametrize("case,lang", _CASE_LANG_PARAMS, ids=_CASE_LANG_IDS)
def test_office_export_page_count_within_region_norm(case, lang):
    """ADR-079 clause 5 / clause 4 — the page-length band the `.docx`
    report itself cannot state (reported `not_applicable`, ADR-079 cl. 4,
    since a `.docx` has no fixed pagination until a word processor lays it
    out) is enforced HERE instead, by REAL conversion against the region's
    own published page bound (never a hand-picked number, ADR-051 §1). For
    the letter case this IS ADR-051 §6's hard 1-page DACH norm.

    Deliberately NOT an exact-page-count equality — see the module
    docstring and
    `Documents/Runs/Stracciatella/office-export/2026-08-31-page-count-gate-portability.md`:
    a page count is a font-dependent rendered-layout quantity, and #547
    (2026-08-30) already had to walk back an `xfail(strict=True)` on
    exactly such a property once CI's font substitution moved the flip
    point it was calibrated against.

    Measured on THIS file's own fixtures, on THIS host's `soffice 26.2.3.2`
    — which substitutes Carlito for Calibri, the exact substitution the CI
    job pins via `fonts-crosextra-carlito`, so this IS the reproduced case,
    not a proxy for it:

    * CV — `CV_DE`/`CV_EN` (2 work entries incl. one nested project, 1
      standalone project, 1 certification, 2 education, 2 languages, 6
      skills) render **2 pages**, against a `cv_max_pages` bound of **3**:
      one whole page of headroom. `_bulk_cv()` (100 bullets, ~11x a
      realistic bullet count) renders **6 pages** — see the paired
      mutation-verification test below.
    * Letter — `LETTER_DE`/`LETTER_EN` (4 body paragraphs, all header/
      recipient/signature fields populated — 15 total non-blank paragraph
      fields, matching the ADR-079 spike's own "15 paragraphs" calibration
      point) render **1 page**, against a `letter_pages` bound of **1**:
      ZERO page-count headroom. A direct sweep of THIS fixture (not
      inherited from the CV's or the spike's synthetic-bullet numbers,
      which do not transfer — a bullet and a wrapped prose paragraph are
      not the same unit of vertical space) found the 1->2 page flip at
      just **+5** extra body paragraphs beyond the realistic 4. This is
      NOT a large margin, and IS consistent with the product's own design:
      `RegionNorm.letter_body_word_budget`/`.letter_body_word_floor`
      (200-300 words) deliberately target fitting 1 page closely, so a
      realistic tailored letter is EXPECTED to sit near this boundary, not
      comfortably under it.

      **Honest caveat, not silently absorbed**: unlike the CV gate, this
      specific assertion is closer to knife-edge than comfortable — its
      font-portability rests entirely on "the exact SAME Carlito
      substitution reproduces in CI", not on a wide numeric margin
      absorbing a shift. That reproduction is pinned (same font package)
      but NOT proven cross-environment by this file alone — per the
      portability doc's own "Not established here" section, the first real
      CI run is the actual proof for this exact case, more so than for the
      CV. If CI's LibreOffice build substitutes Carlito even slightly less
      tightly than this dev host's 26.2.3.2, this specific assertion is
      the one most likely to need a look.
    """
    instance = case.fixtures[lang]
    docx_bytes = case.render(instance, lang)
    pages = _pages_for_docx(docx_bytes)
    assert pages <= case.page_bound, (
        f"{case.kind}-{lang}: rendered .docx converts to {pages} pages, over "
        f"the {case.page_bound_label} bound of {case.page_bound}. Check "
        f"whether the fixture genuinely grew that much content, or whether "
        f"a writer regression is emitting redundant/duplicated paragraphs — "
        f"either would show up here."
    )


@pytest.mark.parametrize("case", _DOCUMENT_CASES, ids=_CASE_IDS)
def test_office_export_page_count_gate_detects_an_oversized_document(case):
    """Guard-the-guard / mutation-verify for the page-count gate (task 1.3
    Method: 'inflate the document past the page bound ... report WHICH
    NAMED test goes red'): `case.inflate()` — FAR more content than any
    realistic document of this kind — must convert to MORE pages than the
    region bound allows, proving `_pages_for_docx` plus the comparison in
    `test_office_export_page_count_within_region_norm` can actually observe
    a violation, not just always pass. See `_bulk_cv`/`_bulk_letter`'s own
    docstrings for the exact measured page counts (6 pages / 2 pages,
    against bounds of 3 / 1)."""
    docx_bytes = case.render(case.inflate(), "de")
    pages = _pages_for_docx(docx_bytes)
    assert pages > case.page_bound, (
        f"{case.kind}: expected the oversized fixture to exceed the "
        f"{case.page_bound_label} bound of {case.page_bound} pages; got "
        f"{pages} — either LibreOffice's pagination behaviour changed "
        f"dramatically or `_pages_for_docx` is broken (e.g. always reading "
        f"page 1)."
    )
