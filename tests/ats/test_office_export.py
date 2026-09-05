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
   check, ``status="not_applicable"``, and ``report.not_applicable`` equal to
   the number of checks carrying that status, with the producers pinned by id
   (since the ADR-039 amendment of 2026-09-04 the ``terminal-review`` and
   ``narrative-evidence`` checks share the bucket on a direct audit)
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

import re
import shutil
import subprocess
import tempfile
from collections import Counter
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
from applire.services.office_export.cv_docx import _RENDERED_LEAVES as _CV_RENDERED_LEAVES
from applire.services.office_export.cv_docx import render_cv_docx
from applire.services.office_export.extract import (
    audit_cover_letter_docx,
    audit_cv_docx,
    extract_docx_text,
)
from applire.services.office_export.letter_docx import (
    _NON_SECTION_FIELDS as _LETTER_NON_SECTION_FIELDS,
)
from applire.services.office_export.letter_docx import _RENDERED_LEAVES as _LETTER_RENDERED_LEAVES
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
    recurses into its elements, a str is a leaf (dropped if blank), a
    non-bool int is a leaf too (stringified — see below), and anything else
    (bool, None, float) contributes nothing. This is what lets
    `_missing_sections` cover a LIST section, the one nested-OBJECT section
    and a plain-scalar section through the SAME code path (task 1.3's 🔒
    boundary) instead of three special cases that could individually be
    narrowed or forgotten.

    Int handling (Finding 2, E057 adversarial review): originally int
    contributed nothing, same as bool — but `work_history[].team_size` is a
    real, content-bearing `int | None` field (`schemas/cv.py`'s own
    docstring: "None means 'not stated' — 0 is a valid team_size"), rendered
    as a literal digit by `_role_facts_line`. Excluding it meant no probe
    was ever generated for it, so NEITHER this gate nor gate 3
    (`_presence_disagreements`, which shares this walker's int-handling via
    `_iter_leaf_values` below) could ever have caught team_size going
    unrendered — independently of whether a fixture set it at all. `bool`
    is checked first and excluded (Python's `bool` is an `int` subclass):
    a boolean like `show_photo` is a structural modifier, never rendered as
    the literal text "True"/"False".
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
    if isinstance(value, int) and not isinstance(value, bool):
        return [str(value)]
    return []


def _content_present(probe: str, text_norm: str, required_count: int = 1) -> bool:
    """True if `probe`'s RAW form, OR its ``month_year()``-transformed form,
    together account for at least `required_count` occurrences in
    `text_norm`. Gate 2 (`_missing_sections`, this function's only caller)
    exists to prove a FACT survives the round trip in SOME form -- never to
    judge which form is correct, which is gate 3's job
    (`_presence_disagreements`, which deliberately does NOT use this
    tolerant check: format correctness needs the strict, single-form
    comparison this function intentionally loosens).

    Without the raw-or-transformed tolerance, gate 2 breaks on its own
    fixtures the moment a date is rendered CORRECTLY: post-fix (commit
    8dc61254), a CV date reads "03/2018" rather than the raw "2018-03", so
    a strict raw-value containment check would report every dated entry as
    content-missing — a false positive on correct output, discovered by
    running gate 2 after merging that fix and seeing exactly this failure.
    `month_year` is safe to apply universally (not just to known date
    fields): its own docstring and doctests guarantee it returns non-date
    strings UNCHANGED (a company name, a bullet, an email are never a
    recognised partial-date pattern), so this adds no tolerance where none
    is wanted -- it only additionally accepts the transformed form where a
    raw ISO date would otherwise (correctly) never appear.

    `required_count` (Finding 1, E057 adversarial review): defaults to 1,
    the ORIGINAL "present at least once, anywhere in the document" check —
    unscoped to the probe's own field, which is exactly the point: gate 2
    proves a fact survived the round trip SOMEWHERE, not where. That is a
    real, wanted tolerance for the overwhelmingly common case (a probe's
    value is unique across the fixture), but it silently stopped being a
    check at all for a probe whose value COINCIDES with another, unrelated
    leaf's value: `_content_present("2023-04", text)` returns `True`
    against text containing only an unrelated `04/2023` rendered from a
    different field entirely — reproduced end-to-end by dropping an
    education entry's date rendering while a work entry in the same
    fixture shares that month and year (an ordinary CV shape — concurrent
    job and study, or two roles starting the same month — not a contrived
    one); the work entry's own correctly-rendered date alone satisfied
    "present at least once" for BOTH leaves. `_missing_sections` closes
    this by passing `required_count` = the number of leaves ACROSS THE
    WHOLE FIXTURE that share this exact raw value — every occurrence a
    correct render legitimately owes the document, not just one.

    Per-occurrence raw-vs-transformed accounting, not a naive sum: when
    `month_year(probe)` is a no-op (the overwhelming majority of probes —
    anything that isn't a recognised partial date), raw and transformed
    normalise to the IDENTICAL string, so counting both and adding them
    would double-count every physical occurrence. Only genuinely DIFFERENT
    normalised forms (a real date) are counted separately and summed —
    `_norm`'s hyphen-to-space folding (`ats_audit._norm`) guarantees a raw
    ISO date ("2023-04" -> "2023 04") and its `month_year` form ("04/2023")
    never normalise to the same string, so this never conflates them
    either.
    """
    raw_norm = _norm_probe(probe)
    transformed_norm = _norm_probe(month_year(probe))
    if transformed_norm == raw_norm:
        available = text_norm.count(raw_norm)
    else:
        available = text_norm.count(raw_norm) + text_norm.count(transformed_norm)
    return available >= required_count


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

    `expected_counts` (Finding 1): a `Counter` over EVERY string leaf in
    the WHOLE fixture (`instance.model_dump()`, not just the field being
    checked) — how many leaves, across every section, share this exact raw
    value. Passed as `_content_present`'s `required_count`, so a probe
    whose value coincides with another field's value must be backed by
    that many occurrences in the extracted text, not just one — see
    `_content_present`'s own docstring for why a whole-document,
    unscoped-to-one-field check needs this to catch a dropped field masked
    by an unrelated coincidence elsewhere.

    Honest limitation, not silently absorbed: when two DIFFERENT fields
    genuinely share a value (this fixture's `LETTER_DE`/`LETTER_EN`
    already do — the candidate's own name, once as `header.name`, once as
    `signature.name`, both real, both meant to render), a shortfall in the
    combined count cannot say WHICH of the colliding fields lost its
    rendering — a document missing either occurrence reports the SAME
    deficit, and this function attributes it to every field that
    contributed to the expected count, not just the one actually at fault.
    That is a real precision loss against per-entry attribution, traded
    for a mechanical, schema-agnostic check that works identically for
    both document kinds (`LetterData` has no per-section heading text to
    scope a check against at all — see the module docstring on why the two
    kinds share one code path here). It never trades away RECALL: a
    genuine drop still turns the gate red, which is what matters for a
    blocking CI gate — see `test_content_present_...` below for the
    mutation-verified proof.
    """
    dumped = instance.model_dump()
    expected_counts: Counter[str] = Counter(_string_leaves(dumped))
    missing: list[tuple[str, list[str]]] = []
    for field_name in sorted(model_cls.model_fields):
        probes = _string_leaves(dumped[field_name])
        gaps = [
            p for p in probes
            if not _content_present(p, text_norm, expected_counts[p])
        ]
        if gaps:
            missing.append((field_name, gaps))
    return missing


def _iter_leaf_values(value: Any, path: str = "") -> list[tuple[str, str]]:
    """Like `_string_leaves`, but also carries a diagnostic PATH per leaf —
    `_presence_disagreements` below needs it to report which specific field
    diverged, not just that something did.

    Int-widened the same way and for the same reason as `_string_leaves`
    (Finding 2): a non-bool int leaf (`work_history[].team_size`) is
    stringified rather than dropped, so the cross-artifact differential
    below can see it diverge exactly like any string leaf would.
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
    if isinstance(value, int) and not isinstance(value, bool):
        return [(path, str(value))]
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

    A SECOND, distinct blind spot (Finding 2, E057 adversarial review;
    proved, not just claimed, in
    `test_presence_disagreements_cannot_see_education_title_dedup_diverge`):
    `education_title` (#548) dedupes a degree that already names its field
    verbatim -- e.g. `degree="Industriemeister Metall"`,
    `field="Metall"` renders once, not twice. `field`'s own raw value
    ("Metall") is a SUBSTRING of `degree`'s own rendered text
    ("Industriemeister Metall") EITHER WAY a document renders it -- deduped
    or (hypothetically, a regression) not -- so `in_pdf` and `in_docx` for
    the `field` leaf are BOTH trivially `True` regardless of whether dedup
    actually fired the same way on both sides. This is not the "transformed
    differently" case above (dedup, when it fires, fires IDENTICALLY on
    both sides -- every PDF template and this writer share the one
    `education_title` filter): it is that a genuinely DIVERGENT dedup
    outcome between the two artifacts is invisible to a presence check for
    THIS SPECIFIC LEAF, because the leaf's value was never independently
    absent on either side to begin with. A fixture change cannot fix a
    blind spot in the CHECKER'S OWN MECHANISM -- see
    `test_office_export_education_title_dedup_survives_the_round_trip` for
    the different, format-aware assertion (redundant form absent, not just
    deduped form present) that actually catches a dedup regression on this
    writer, independently of this function.
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
                # #328 role facts (ADR-062 clause 1) — E057 Finding 2: these three
                # fields shipped unrendered on the .docx export past a green CI
                # suite because no fixture in this file ever set them. Populated
                # here so the section-parity gate actually exercises them.
                # team_size deliberately does NOT match the "acht Prüfingenieuren"
                # bullet above — a single-digit value is a substring of the
                # phone number "+49 89 1234567" (the "8" in "89"), which would
                # let an UNRELATED coincidence mask a genuine drop of this
                # field's own rendering (measured while writing the
                # mutation-verification test below); a broader, two-digit
                # figure covering the wider org across all three sites avoids
                # the collision without contradicting the bullet's narrower claim.
                "team_size": 14,
                "budget_managed": "ca. 2,4 Mio. EUR",
                "industry_context": "Automobilzulieferer",
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
                # E057 Finding 2: expiry_date was unpopulated in every
                # fixture, so no gate ever exercised it either.
                "expiry_date": "2024-05-01",
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
                # #328 role facts (ADR-062 clause 1) — E057 Finding 2: see the
                # matching CV_DE comment above.
                "team_size": 11,
                "budget_managed": "approx. CHF 1.8 million",
                "industry_context": "Financial services technology",
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
                # E057 Finding 2: see the matching CV_DE comment above.
                "expiry_date": "2025-03-01",
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
    rendered_leaves: frozenset  # this writer's own NESTED-leaf registry (Finding 2)
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
    rendered_leaves=_CV_RENDERED_LEAVES,
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
    rendered_leaves=_LETTER_RENDERED_LEAVES,
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
    # The `not_applicable` bucket must count EXACTLY the checks that carry that
    # status (pins `_finish()`'s own computation, never folded into
    # passed/failed) and the page-length band must be one of them. Since the
    # ADR-039 amendment of 2026-09-04 the primary report also produces
    # `not_applicable` for `terminal-review` (no review outcome on a direct
    # audit) and, on the CV, `narrative-evidence` (no Keyword Ledger handed in),
    # so the bucket is no longer the page band alone — the set is pinned so a
    # NEW producer of the status cannot slip in unnamed.
    na_ids = sorted(c.id for c in report.checks if c.status == "not_applicable")
    assert report.not_applicable == len(na_ids), (
        f"{case.kind}-{lang}: report.not_applicable={report.not_applicable!r} "
        f"but {len(na_ids)} checks carry the status: {na_ids}"
    )
    assert "page-length" in na_ids, f"{case.kind}-{lang}: page band missing from {na_ids}"
    assert set(na_ids) <= {"page-length", "terminal-review", "narrative-evidence"}, (
        f"{case.kind}-{lang}: unexpected not_applicable producer(s): {na_ids}"
    )


# ---------------------------------------------------------------------------
# 2. Section parity, derived mechanically from `model_cls.model_fields`
# ---------------------------------------------------------------------------


def _leaf_values_at_path(dumped: dict, path: str) -> list[Any]:
    """Navigate a ``.model_dump()``-shaped dict along `path` — the SAME
    grammar `_iter_leaf_paths` (``_common.py``) produces, e.g.
    ``"work_history[].team_size"`` — and return every value reached, one
    per list element crossed by a ``[]`` segment.

    Exists to bridge a real path-grammar mismatch (Finding 2): this file's
    OTHER walkers (`_string_leaves`/`_iter_leaf_values`) number every list
    element for their own diagnostic purposes (a `skills: list[str]` field
    walks as one leaf per element), while `_iter_leaf_paths` — the
    SCHEMA-level walker `_RENDERED_LEAVES` is built from, and what this
    function's caller checks fixtures against — treats a whole
    scalar-element list field as ONE leaf with no index at all
    (``"skills"``, not ``"skills[]"``): it never descends into a `list[str]`
    the way it descends into a `list[SomeModel]`. The two conventions only
    coincide for list-of-OBJECT fields. Navigating the TARGET path directly
    against the raw dumped data, rather than generalising the diagnostic
    walkers' own per-element paths back down to the schema grammar,
    sidesteps that mismatch instead of silently mis-matching it — verified
    empirically before this was written: generalising `_iter_leaf_values`'s
    paths by stripping list indices falsely flagged `skills`,
    `work_history[].bullets`, `projects[].bullets`,
    `work_history[].projects[].bullets` and `body.paragraphs` as
    contentless in every fixture, none of which are.
    """
    segments = re.findall(r"[^.\[\]]+|\[\]", path)
    current: list[Any] = [dumped]
    for segment in segments:
        nxt: list[Any] = []
        if segment == "[]":
            for item in current:
                nxt.extend(item or [])
        else:
            for item in current:
                nxt.append(item.get(segment) if isinstance(item, dict) else None)
        current = nxt
    return current


def _has_leaf_content(value: Any) -> bool:
    """True if `value` (as returned by `_leaf_values_at_path`, so possibly
    itself a list — e.g. a `bullets`/`skills` leaf resolves to ONE list of
    strings, `work_history[].team_size` resolves to one int-or-None per
    entry) carries at least one non-blank string or a stated (non-bool)
    int, recursively. Mirrors `_string_leaves`'s own int-widening (see that
    function's docstring for why team_size — an `int | None` field — must
    count as content) — kept as a separate, small function rather than
    reusing `_string_leaves` because this needs to short-circuit on "is
    there anything at all", not collect every leaf, and because a plain
    scalar reached here is a bare value, not yet wrapped in the dict/list
    shape `_string_leaves` expects as its input.
    """
    if isinstance(value, list):
        return any(_has_leaf_content(v) for v in value)
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, int):
        return True
    return False


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

    Nested-leaf coverage (Finding 2, E057 adversarial review, HIGH): the
    TOP-LEVEL check above cannot see a nested field left empty — a
    fixture where `work_history` has bullets, dates and a company name but
    NO entry ever sets `team_size`/`budget_managed`/`industry_context`
    still gives `work_history` "some" content, so the check above stays
    green while the section-parity gate silently never probes those three
    fields at all. Those are EXACTLY the fields E057 shipped unrendered
    (`cv_docx.py`'s own module docstring) — a document missing all three
    would have passed every gate in this file. `case.rendered_leaves` is
    each writer's own MECHANICALLY-derived nested-leaf registry (imported,
    never re-typed — the same `_RENDERED_LEAVES` set
    `test_every_tailored_cv_leaf_field_is_accounted_for` /
    `test_every_letterdata_leaf_field_is_accounted_for` already prove is
    exactly "every schema leaf this writer renders as text"), so this
    check grows automatically the day a new leaf is added to a schema and
    wired into a writer — never a second hand-maintained leaf list either.
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

        empty_leaves = sorted(
            path
            for path in case.rendered_leaves
            if not _has_leaf_content(_leaf_values_at_path(dumped, path))
        )
        assert not empty_leaves, (
            f"{case.kind}-{lang}: fixture has no probeable content for the "
            f"NESTED leaf(ves) {empty_leaves} — every gate that walks the "
            f"schema down to leaf level (not just top-level sections, see "
            f"the top-level check above) cannot exercise these; add "
            f"content to the fixture rather than leaving the gap silent"
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


def test_content_present_scoped_to_expected_occurrences_across_the_document(monkeypatch):
    """Finding 1 (E057 adversarial review, HIGH): `_content_present`'s
    raw-or-transformed tolerance used to be a WHOLE-DOCUMENT substring
    check with no notion of how many times a value is expected to occur —
    so a probe was "present" if its value appeared ANYWHERE, even from a
    totally different field's independently-correct rendering.

    Part 1 is the review's own literal repro, run in isolation (no
    rendering involved) to pin the PRIMITIVE's tolerance directly: a probe
    backed by only ONE real occurrence elsewhere in the document must still
    read as present — this is the deliberate, wanted tolerance
    `_content_present`'s own docstring describes (the common case: a
    probe's value is unique across the fixture), not the bug. The bug was
    never having this tolerance; it was having ONLY this tolerance, with no
    way to demand MORE occurrences when more are genuinely expected.

    Part 2 is the end-to-end shape the review reproduced: an education
    entry's date rendering dropped entirely, undetected by
    `test_office_export_section_parity_survives_extraction` because a work
    entry in the SAME fixture starts in the same month and year — an
    ordinary CV shape (concurrent job and study, or two roles starting the
    same month), not a contrived one. The mutation drops EVERY education
    entry's date paragraph (a realistic writer regression: someone edited
    `_render_education` and dropped its date line) via
    `monkeypatch.setitem` on `_SECTION_RENDERERS["education"]` — the
    dispatch dict `render_cv_docx` actually calls through, NOT a plain
    `setattr` on the module's `_render_education` name (that name is looked
    up once, at module-IMPORT time, when the dict literal is built; a later
    `setattr` on the module attribute would not reach the already-captured
    dict value). `_render_work_history` is untouched, so the work entry's
    identical-month date still renders — proving the education entry's
    OWN missing date is masked by a DIFFERENT field's rendering, not by a
    global date cut.

    The assertion targets the exact colliding PROBE VALUE, not just "some
    education content went missing": `education[1]`'s dates (2004-10,
    2006-09 in `CV_DE`) collide with nothing and are — correctly — reported
    missing by BOTH the old and the new code once every education date is
    dropped, so asserting only `"education" in missing_fields` would pass
    either way and prove nothing about the fix specifically. Checking that
    the COLLIDING value itself (`work_history[0].start_date`) is among
    `education`'s own reported gaps is what only the fix can satisfy.
    """
    # --- Part 1: the primitive's tolerance, isolated -----------------------
    text = _norm_probe("some prose mentioning 04/2023 from an unrelated field")
    assert _content_present("2023-04", text), (
        "sanity: a SINGLE required occurrence must still accept a match "
        "coming from elsewhere in the document — this is the existing, "
        "wanted tolerance, not the bug Finding 1 is about"
    )

    # --- Part 2: end-to-end, via the real writer ----------------------------
    dumped = CV_DE.model_dump()
    edu_start_before = dumped["education"][0]["start_date"]
    work_start = dumped["work_history"][0]["start_date"]
    assert edu_start_before != work_start, (
        "fixture sanity: this test manufactures the collision itself; "
        "CV_DE must not already carry it, or the edit below is a no-op"
    )
    dumped["education"][0]["start_date"] = work_start
    instance = TailoredCVData.model_validate(dumped)

    docx_bytes = _CV_CASE.render(instance, "de")
    text_norm = _norm_probe(extract_docx_text(docx_bytes))
    assert not _missing_sections(TailoredCVData, instance, text_norm), (
        "positive control: the real, unmutated render (with the collision "
        "in place) must have nothing missing"
    )

    import applire.services.office_export.cv_docx as cv_docx_module

    def render_education_without_dates(document, tailored, labels, color, photo_bytes, lang):
        if not any(cv_docx_module._education_has_content(e) for e in tailored.education):
            return
        cv_docx_module.add_heading(document, labels["education"], 2, color)
        for entry in tailored.education:
            if not cv_docx_module._education_has_content(entry):
                continue
            header = cv_docx_module._join_nonblank(
                [entry.institution, cv_docx_module.education_title(entry.degree, entry.field)]
            )
            cv_docx_module.add_paragraph(document, header, bold=True)
            # Date paragraph intentionally OMITTED — this is the mutation.

    monkeypatch.setitem(
        cv_docx_module._SECTION_RENDERERS, "education", render_education_without_dates
    )

    mutated_bytes = _CV_CASE.render(instance, "de")
    mutated_text_norm = _norm_probe(extract_docx_text(mutated_bytes))

    # Confirm the mutation really is selective — the work entry's own,
    # identical-month date must still be there (work_history's own
    # renderer was never touched). Otherwise this is not testing "a work
    # entry shares the month", it is just re-running gate 3's own already-
    # covered whole-writer cut.
    assert _content_present(work_start, mutated_text_norm), (
        "test setup: the work entry's own date must still render — "
        "_render_work_history must be untouched by this mutation"
    )

    missing = _missing_sections(TailoredCVData, instance, mutated_text_norm)
    missing_by_field = dict(missing)
    assert work_start in missing_by_field.get("education", []), (
        f"education's dropped '{work_start}' must be reported missing even "
        f"though the work entry independently renders the identical "
        f"(raw-or-transformed) text elsewhere in the same document; "
        f"education's own reported gaps: {missing_by_field.get('education')}"
    )


def test_section_parity_checker_detects_dropped_role_facts(monkeypatch):
    """Finding 2 (E057 adversarial review, HIGH), mutation-verification half:
    `team_size` / `budget_managed` / `industry_context` are exactly the
    fields that shipped unrendered on the .docx export past a green CI
    suite (`cv_docx.py`'s own module docstring) — because no fixture in
    this file ever set them, so the fixtures test above
    (`test_fixtures_exercise_every_content_bearing_section`) would have
    stayed green regardless. That test alone does not prove the GATE
    catches a regression once the fixture carries real values for these
    three fields — it only proves the fixture HAS something to probe. This
    test is the other half: drop the writer's OWN role-facts rendering
    (`cv_docx._role_facts_line`, the one call site all three fields go
    through) and confirm `_missing_sections` — the checker
    `test_office_export_section_parity_survives_extraction` calls — now
    reports it, using the NOW-populated `CV_DE` fixture (E057 Finding 2's
    other half, above).

    Mutation targets `_role_facts_line` specifically (not the whole
    `work_history` section renderer) so company/role/dates/bullets staying
    present proves this is a REGRESSION IN THE ROLE-FACTS LINE, not a
    coarser "the whole entry vanished" cut that gate 2 would trivially
    catch regardless of this fix.
    """
    instance = CV_DE
    docx_bytes = _CV_CASE.render(instance, "de")
    text_norm = _norm_probe(extract_docx_text(docx_bytes))

    # Positive control: the real, unmutated render has nothing missing —
    # in particular, this is where the "fixture actually carries these
    # three fields" half of Finding 2 gets exercised for real.
    assert not _missing_sections(TailoredCVData, instance, text_norm)

    entry = instance.work_history[0]
    for field_name, value in (
        ("team_size", entry.team_size),
        ("budget_managed", entry.budget_managed),
        ("industry_context", entry.industry_context),
    ):
        assert value is not None and str(value).strip(), (
            f"fixture sanity: work_history[0].{field_name} must carry a "
            f"real value for this test to mean anything — got {value!r}"
        )

    import applire.services.office_export.cv_docx as cv_docx_module

    monkeypatch.setattr(cv_docx_module, "_role_facts_line", lambda entry, labels, lang: "")

    mutated_bytes = _CV_CASE.render(instance, "de")
    mutated_text_norm = _norm_probe(extract_docx_text(mutated_bytes))

    # Confirm the mutation really is selective to the role-facts line —
    # the rest of the entry (company/role/dates/bullets) must still be
    # there, or this is just re-testing "an entire entry vanished".
    for probe in (entry.company, entry.role, entry.bullets[0]):
        assert _content_present(probe, mutated_text_norm), (
            f"test setup: {probe!r} must still render — the mutation must "
            f"be selective to _role_facts_line, not the whole entry"
        )

    missing = _missing_sections(TailoredCVData, instance, mutated_text_norm)
    missing_by_field = dict(missing)
    work_history_gaps = missing_by_field.get("work_history", [])
    for field_name, value in (
        ("team_size", str(entry.team_size)),
        ("budget_managed", entry.budget_managed),
        ("industry_context", entry.industry_context),
    ):
        assert value in work_history_gaps, (
            f"dropping _role_facts_line's rendering must be caught as a "
            f"missing work_history probe for {field_name} ({value!r}) — "
            f"got work_history gaps: {work_history_gaps}"
        )


def test_office_export_omits_a_unit_less_budget_figure():
    """Finding 2 (E057 adversarial review), related unexercised shape #1: a
    `budget_managed` the display filter REJECTS — a bare, unit-less number
    (`budget_display("6000000")` -> `""`, #382 PO decision 2026-08-08) —
    had no coverage anywhere in this file. Not exercising it is a real gap
    in the opposite direction from the other two: every OTHER probe in
    this suite is expected to SURVIVE the round trip; this one is expected
    to be DELIBERATELY OMITTED, and nothing here proved the office export
    honours that (as opposed to printing the bare, meaningless figure — the
    ORIGINAL #382 defect, on a second surface).

    This is deliberately NOT folded into `CV_DE`/`CV_EN` (the fixtures
    every OTHER gate in this file walks expecting full survival): a probe
    this filter is SUPPOSED to drop would make
    `test_office_export_section_parity_survives_extraction` report a false
    failure on correct behaviour, which is exactly the "fix that makes
    correct output fail" trap task 1's own brief warns against. A
    dedicated, local fixture keeps that contract intact.
    """
    dumped = CV_DE.model_dump()
    dumped["work_history"][0]["budget_managed"] = "6000000"
    instance = TailoredCVData.model_validate(dumped)

    docx_bytes = _CV_CASE.render(instance, "de")
    text_norm = _norm_probe(extract_docx_text(docx_bytes))

    assert "6000000" not in text_norm, (
        "a bare, unit-less budget figure must be OMITTED from the office "
        "export (budget_display's #382 contract) — found it printed raw"
    )
    # Positive control: the rest of the entry the label/value pair would
    # have sat next to still renders — this is a targeted omission, not a
    # side effect of the whole entry (or the whole role-facts line, which
    # still carries team_size/industry_context) going missing.
    assert _content_present(str(instance.work_history[0].team_size), text_norm)
    assert _content_present(instance.work_history[0].industry_context, text_norm)
    assert _content_present(instance.work_history[0].company, text_norm)


def test_office_export_education_title_dedup_survives_the_round_trip():
    """Finding 2 (E057 adversarial review), related unexercised shape #2: a
    degree that repeats its own field (`degree="Industriemeister Metall"`,
    `field="Metall"` — #548's real ground truth, ``education_title``'s own
    doctest) had no coverage anywhere in this file either.

    Asserts BOTH directions, which plain substring-containment checks
    (`_content_present` / gate 2) structurally cannot: the deduped form
    MUST be present (content survived), AND the redundant, un-deduped form
    MUST NOT be present (format is actually deduped, not just "some text
    resembling it exists somewhere") — the "different assertion that DOES
    catch it" the review asked for, since presence-of-the-deduped-form
    alone would pass even if dedup silently stopped firing (the redundant
    form CONTAINS the deduped form as a prefix).
    """
    dumped = CV_DE.model_dump()
    dumped["education"] = [
        {
            "institution": "IHK Ausbildungszentrum",
            "degree": "Industriemeister Metall",
            "field": "Metall",
            "start_date": "2009-09",
            "end_date": "2011-06",
        }
    ]
    instance = TailoredCVData.model_validate(dumped)

    docx_bytes = _CV_CASE.render(instance, "de")
    text_norm = _norm_probe(extract_docx_text(docx_bytes))

    assert _norm_probe("Industriemeister Metall") in text_norm, (
        "the deduped degree title must survive the round trip"
    )
    assert _norm_probe("Industriemeister Metall, Metall") not in text_norm, (
        "the REDUNDANT, un-deduped form must not appear — education_title's "
        "dedup (#548) must actually have fired on the office export, "
        "exactly as it does in all seven PDF templates"
    )


def test_presence_disagreements_cannot_see_education_title_dedup_diverge():
    """Finding 2 (E057 adversarial review): PROVES, rather than just
    asserts in prose, the blind spot `_presence_disagreements`'s own
    docstring must disclose (task brief: "do not pretend a fixture change
    fixes that; state it in the docstring") — gate 3 cannot catch a
    genuine education_title divergence between the two artifacts even in
    principle, because `field`'s raw value ("Metall") is a SUBSTRING of
    `degree`'s own rendered text ("Industriemeister Metall") regardless of
    whether dedup fired on either side.

    Constructed directly against two hand-built normalised "extracted
    text" strings — `_presence_disagreements` is a pure function of
    normalised text, so the SAME genuine divergence a real writer bug
    would produce (one artifact deduped, one not — #548's actual shape,
    reproduced with the roles reversed here since the .docx side is the
    one that HAS the fix) is exercised exactly, with no Playwright/real
    PDF render needed to prove a negative result about text this test
    fully controls.

    The two hand-built strings ALSO diverge on `institution` (present in
    one, absent from the other) — proof that this test's texts are not
    just uniformly missing everything (which would make "no disagreement
    for `field`" a vacuous, meaningless pass): the checker DOES flag the
    institution divergence, and STILL misses the field one, in the exact
    same pair of strings.
    """
    dumped = CV_DE.model_dump()
    dumped["education"] = [
        {
            "institution": "IHK Ausbildungszentrum",
            "degree": "Industriemeister Metall",
            "field": "Metall",
            "start_date": "2009-09",
            "end_date": "2011-06",
        }
    ]
    instance = TailoredCVData.model_validate(dumped)

    # A genuine divergence: one artifact renders the deduped form, the
    # other (hypothetically, a regression) renders the redundant form —
    # AND the docx side additionally carries the institution name, the pdf
    # side does not (the real, catchable divergence proving this checker
    # is not just blind to everything in this constructed pair of strings).
    pdf_text_norm = _norm_probe("Lebenslauf Industriemeister Metall, Metall 09/2009")
    docx_text_norm = _norm_probe(
        "Lebenslauf IHK Ausbildungszentrum Industriemeister Metall 09/2009"
    )

    disagreements = _presence_disagreements(instance, pdf_text_norm, docx_text_norm)

    institution_disagreements = [d for d in disagreements if d[0] == "education[0].institution"]
    assert institution_disagreements, (
        "test sanity: the checker must catch the institution divergence "
        "these hand-built strings genuinely contain — if it does not, "
        "this test's texts are not exercising the checker at all, and "
        "the assertion below would be vacuous"
    )

    field_disagreements = [d for d in disagreements if d[0] == "education[0].field"]
    assert not field_disagreements, (
        "this IS the disclosed blind spot, not a new failure: "
        "_presence_disagreements cannot see education[].field diverge "
        "here — 'Metall' is a substring of the degree's own rendered text "
        "on BOTH sides regardless of whether dedup fired, so presence "
        "agrees on both sides for reasons unrelated to whether the "
        "artifacts actually match (proven alongside a divergence — "
        "institution, above — that this SAME checker call DOES catch). "
        "If this assertion ever fails, the checker gained a way to catch "
        "this — update it and the disclosure in _presence_disagreements' "
        "own docstring together, rather than leaving one stale."
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
