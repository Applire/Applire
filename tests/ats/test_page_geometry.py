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

"""#621 / #622 — page geometry: every-page margins and the entry break policy.

#621 (bug): the CV/letter margin was ``padding`` on ``.page`` — Playwright's
``page.pdf()`` renders with ``margin: 0``, and CSS box fragmentation only keeps
a box's own top/bottom padding at its TRUE start/end. Every page that is not
the true start (i.e. every page after the first) loses the top padding, so
page 2+ rendered with ~0.5mm of top margin instead of the template's real
value. The fix moves the margin into an ``@page`` rule, which Chromium's
paged-media engine re-applies on every page uniformly.

These tests measure the rendered PDF directly with poppler's
``pdftotext -bbox-layout`` (coordinates in pt, top-left origin) — never CSS
reasoning — per the #547 lesson that Chromium's paged-media behaviour does not
always match what the stylesheet says.

#622 (feature): the break policy for CV entries and list sections, tested
further down this file (see its own section banner).
"""
import html
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from applire.schemas.cv import TailoredCVData
from applire.services.color_detection import _default_context
from applire.services.cover_letter import _TEMPLATE_FILES as LETTER_TEMPLATES
from applire.services.cover_letter import _TEMPLATES_DIR as LETTER_TEMPLATES_DIR
from applire.services.cover_letter import _default_color_context
from applire.services.cv import _TEMPLATE_FILES as CV_TEMPLATES
from applire.services.cv import _TEMPLATES_DIR as CV_TEMPLATES_DIR
from applire.services.ats_audit import _norm as _ats_norm
from applire.services.cv import _html_to_pdf, _jinja_env
from applire.templates.labels import cover_letter_labels, cv_labels

PT_PER_MM = 72.0 / 25.4
TOLERANCE_MM = 3.0

# ---------------------------------------------------------------------------
# pdftotext -bbox-layout parsing — regex, not an XML parser: the output is a
# small, stable poppler-emitted vocabulary (<page>/<word>, always
# double-quoted attributes), and this mirrors the codebase's existing style of
# reading template source with regex (test_letter_signature_air_stays_under_547_budget)
# rather than pulling in a CSS/HTML parsing dependency for a narrow read.
# ---------------------------------------------------------------------------

_PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
_WORD_RE = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', re.S
)


def _bbox_pages(pdf_bytes: bytes) -> list[dict]:
    """Render *pdf_bytes* through ``pdftotext -bbox-layout`` and return one dict
    per page: ``{"width": pt, "height": pt, "words": [(xMin, yMin, xMax, yMax, text), ...]}``.
    All coordinates are pt, origin top-left (poppler's -bbox-layout convention).
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        pdf_path = f.name
    try:
        proc = subprocess.run(
            ["pdftotext", "-bbox-layout", pdf_path, "-"],
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        Path(pdf_path).unlink(missing_ok=True)

    pages = []
    for width, height, body in _PAGE_RE.findall(proc.stdout):
        words = [
            (float(x0), float(y0), float(x1), float(y1), html.unescape(text))
            for x0, y0, x1, y1, text in _WORD_RE.findall(body)
        ]
        pages.append({"width": float(width), "height": float(height), "words": words})
    return pages


def _read_page_margin_mm(source: str) -> tuple[float, float, float, float]:
    """Parse ``@page { ... margin: <shorthand>; ... }`` from template *source*
    into ``(top, right, bottom, left)`` mm — the CSS shorthand expansion rule,
    same as a browser's. Raises via assert (not skip): pre-#621, no template
    has an ``@page`` rule, so this is the mechanism that makes the geometry
    tests fail on today's templates.
    """
    m = re.search(r"@page\s*\{[^}]*?margin:\s*([^;]+);", source, re.S)
    assert m, "no @page margin rule found in template source (#621 not implemented)"
    raw = m.group(1).split()
    assert raw and all(v.endswith("mm") for v in raw), f"@page margin not in mm: {m.group(1)!r}"
    vals = [float(v[:-2]) for v in raw]
    if len(vals) == 1:
        t = r = b = l = vals[0]
    elif len(vals) == 2:
        t, r = vals
        b, l = t, r
    elif len(vals) == 3:
        t, r, b = vals
        l = r
    elif len(vals) == 4:
        t, r, b, l = vals
    else:
        raise AssertionError(f"@page margin has {len(vals)} components, expected 1-4: {vals}")
    return t, r, b, l


def _read_first_page_top_override_mm(source: str) -> float | None:
    """``@page :first { margin-top: ...; }`` — executive_letter's deliberate
    page-1 exception (its coloured header band bleeds to the top edge by
    design; only continuation pages need #621's margin). ``None`` when a
    template declares no such override, i.e. page 1 uses the same top
    margin as every other page.
    """
    m = re.search(r"@page\s*:first\s*\{[^}]*?margin-top:\s*([\d.]+)mm", source, re.S)
    return float(m.group(1)) if m else None


def _assert_margins_hold_every_page(
    template: str,
    margins_mm: tuple[float, float, float, float],
    pages: list[dict],
    first_page_top_mm: float | None = None,
) -> None:
    top_mm, right_mm, bottom_mm, left_mm = margins_mm
    assert len(pages) >= 2, (
        f"{template}: fixture rendered {len(pages)} page(s) — it must span a page "
        f"break for the page-2 invariant to mean anything"
    )
    left_floor_pt = (left_mm - TOLERANCE_MM) * PT_PER_MM
    for i, page in enumerate(pages):
        words = page["words"]
        assert words, f"{template}: page {i + 1} of {len(pages)} has no extracted text at all"
        y_min = min(w[1] for w in words)
        y_max = max(w[3] for w in words)
        x_min = min(w[0] for w in words)
        bottom_ceiling_pt = page["height"] - (bottom_mm - TOLERANCE_MM) * PT_PER_MM
        # page 2+ must hold the general margin regardless; page 1 only if the
        # template declares no @page :first override for it (see docstring).
        page_top_mm = top_mm if (i > 0 or first_page_top_mm is None) else first_page_top_mm
        top_floor_pt = (page_top_mm - TOLERANCE_MM) * PT_PER_MM

        assert y_min >= top_floor_pt, (
            f"{template}: page {i + 1}/{len(pages)} first text starts {y_min / PT_PER_MM:.2f}mm "
            f"from the top, below the {page_top_mm}mm top margin (−{TOLERANCE_MM}mm tolerance = "
            f"{page_top_mm - TOLERANCE_MM:.1f}mm floor). This is #621: only page 1 got the margin."
        )
        assert y_max <= bottom_ceiling_pt, (
            f"{template}: page {i + 1}/{len(pages)} last text ends {y_max / PT_PER_MM:.2f}mm from "
            f"the top on a {page['height'] / PT_PER_MM:.1f}mm-tall page — over the "
            f"{bottom_mm}mm bottom margin (+{TOLERANCE_MM}mm tolerance) ceiling of "
            f"{page['height'] / PT_PER_MM - (bottom_mm - TOLERANCE_MM):.1f}mm."
        )
        assert x_min >= left_floor_pt, (
            f"{template}: page {i + 1}/{len(pages)} left-most text is {x_min / PT_PER_MM:.2f}mm "
            f"from the left edge, below the {left_mm}mm left margin (−{TOLERANCE_MM}mm tolerance = "
            f"{left_mm - TOLERANCE_MM:.1f}mm floor)."
        )


# ---------------------------------------------------------------------------
# Synthetic fixtures guaranteed to span >= 2 pages on every template. Short,
# numbered tokens (never real profile data — see the run brief's boundaries).
# ---------------------------------------------------------------------------


def _margin_probe_bullets(n: int) -> list[str]:
    return [f"MPB{n:02d}{letter} Messpunkt Rand-Probe Eintrag Nummer {n:02d}{letter}" for letter in "ABCDEF"]


CV_MARGIN_PROBE = TailoredCVData.model_validate(
    {
        "contact": {
            "name": "Rand Probe",
            "email": "rand.probe@example.de",
            "phone": "+49 30 0000000",
            "location": "Berlin",
            "photo_url": None,
        },
        "show_photo": False,
        "summary": "Kurzprofil für den Seitenrand-Messtest über mehrere Seiten hinweg.",
        "work_history": [
            {
                "company": f"Messfirma MP{n:02d} GmbH",
                "role": f"Randtester MP{n:02d}",
                "start_date": f"{2012 - n:04d}-01",
                "end_date": f"{2013 - n:04d}-01",
                "bullets": _margin_probe_bullets(n),
            }
            for n in range(1, 13)  # 12 positions x 6 bullets (brief's suggested shape)
        ],
        "skills": ["MPSkillA", "MPSkillB", "MPSkillC"],
        "languages": [{"language": "Deutsch", "level": "Muttersprache"}],
    }
)

_LETTER_MARGIN_PARAGRAPH = (
    "Absatz {n:02d} dieses synthetischen Messtexts dient ausschließlich dazu, "
    "den Seitenumbruch zuverlässig über mehrere Seiten zu erzwingen, damit sich "
    "der obere und untere Rand auf Folgeseiten unabhängig vom ersten Blatt "
    "überprüfen lässt, ohne echte Bewerbungsdaten zu verwenden."
)

LETTER_MARGIN_PROBE = {
    "header": {
        "name": "Rand Probe",
        "address": "Testweg 1, 10115 Berlin",
        "phone": "+49 30 0000000",
        "email": "rand.probe@example.de",
        "photo_url": None,
    },
    "recipient": {
        "name": "Frau Musterfrau",
        "title": "Randtest-Leitung",
        "company": "Messfirma Rand GmbH",
        "address": "Probestraße 2, 10117 Berlin",
        "date": "1. Januar 2026",
    },
    "body": {
        "paragraphs": [_LETTER_MARGIN_PARAGRAPH.format(n=n) for n in range(1, 17)],  # 16 paragraphs
    },
    "signature": {
        "closing": "Mit freundlichen Grüßen",
        "name": "Rand Probe",
    },
}


# ---------------------------------------------------------------------------
# Part 1 (#621) — the margin must hold on every page, not just the first.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_page_margin_holds_every_page(template):
    source = (CV_TEMPLATES_DIR / CV_TEMPLATES[template]).read_text(encoding="utf-8")
    margins_mm = _read_page_margin_mm(source)

    html_out = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=CV_MARGIN_PROBE, color=_default_context(), lang="de", labels=cv_labels("de")
    )
    pdf = await _html_to_pdf(html_out)
    pages = _bbox_pages(pdf)
    _assert_margins_hold_every_page(template, margins_mm, pages)


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(LETTER_TEMPLATES))
async def test_letter_page_margin_holds_every_page(template):
    source = (LETTER_TEMPLATES_DIR / LETTER_TEMPLATES[template]).read_text(encoding="utf-8")
    margins_mm = _read_page_margin_mm(source)
    first_page_top_mm = _read_first_page_top_override_mm(source)

    html_out = _jinja_env.get_template(LETTER_TEMPLATES[template]).render(
        letter=LETTER_MARGIN_PROBE,
        color=_default_color_context(),
        lang="de",
        labels=cover_letter_labels("de"),
        subject="Bewerbung als Randtester",
    )
    pdf = await _html_to_pdf(html_out)
    pages = _bbox_pages(pdf)
    _assert_margins_hold_every_page(template, margins_mm, pages, first_page_top_mm=first_page_top_mm)


def test_all_14_templates_declare_an_page_rule():
    """Gate-adjacent sanity check named after the brief's own count: exactly the
    7 CV + 7 letter templates declare an ``@page`` rule — grep count == 14."""
    all_files = list(CV_TEMPLATES.values()) + list(LETTER_TEMPLATES.values())
    assert len(all_files) == 14, f"expected 14 templates (7 CV + 7 letter), found {len(all_files)}"
    missing = [
        f for f in all_files if "@page" not in (CV_TEMPLATES_DIR / f).read_text(encoding="utf-8")
    ]
    assert not missing, f"templates missing an @page rule: {missing}"


# ---------------------------------------------------------------------------
# Part 2 (#622) — break policy: SHORT entries (<=3 bullets) stay atomic (#357
# behaviour, unchanged); LONG entries (>3 bullets) may break, but head +
# first-2-bullets is one keep-together group, last-2-bullets another, and
# every remaining bullet is itself atomic. List sections (certifications
# etc.) follow the same shape at the item level. CV templates only (7) — the
# letter templates' .signature rules from #547/#429 are untouched.
# ---------------------------------------------------------------------------


def _pdf_pages_text(pdf_bytes: bytes) -> list[str]:
    """Per-page extracted text, ATS-normalised (lowercased, hyphen-folded,
    whitespace-collapsed) — mirrors test_roundtrip.py's own pattern for
    locating a probe string on a specific rendered page.
    """
    import io

    from pypdf import PdfReader

    return [_ats_norm(p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf_bytes)).pages]


def _short_probe_bullets(i: int) -> list[str]:
    tag = f"{i:02d}"
    return [f"P{tag}B1 Kurzpunkt Randtest", f"P{tag}B2 Kurzpunkt Randtest", f"P{tag}B3 Kurzpunkt Randtest"]


CV_SHORT_ENTRIES_PROBE = TailoredCVData.model_validate(
    {
        "contact": {
            "name": "Short Entries Probe",
            "email": "short.entries@example.de",
            "phone": "+49 30 0000002",
            "location": "Berlin",
            "photo_url": None,
        },
        "show_photo": False,
        "work_history": [
            {
                "company": f"Firma P{i:02d} GmbH",
                "role": f"Rolle P{i:02d}",
                "start_date": f"{2030 - i:04d}-01",
                "end_date": f"{2031 - i:04d}-01",
                "bullets": _short_probe_bullets(i),
            }
            for i in range(1, 31)  # 30 positions x 3 bullets (brief's suggested shape)
        ],
    }
)


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_short_entries_never_split(template):
    """30 positions x 3 bullets (every entry SHORT by #622's own threshold) —
    every position's title and all 3 bullets land on the same page. Not
    vacuous: asserts the render spans >=3 pages, so several page boundaries
    exist for a naive (pre-#357) renderer to fall inside an entry.
    """
    html_out = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=CV_SHORT_ENTRIES_PROBE, color=_default_context(), lang="de", labels=cv_labels("de")
    )
    pdf = await _html_to_pdf(html_out)
    pages = _pdf_pages_text(pdf)
    assert len(pages) >= 3, (
        f"{template}: fixture rendered {len(pages)} page(s) — need >=3 for this "
        f"invariant to mean anything"
    )

    for i in range(1, 31):
        tag = f"{i:02d}"
        probes = [f"Rolle P{tag}", *_short_probe_bullets(i)]
        located = {}
        for probe in probes:
            needle = _ats_norm(probe)
            hits = [p for p, text in enumerate(pages) if needle in text]
            assert hits, f"{template}: '{probe}' dropped from the rendered PDF"
            located[probe] = hits[0]
        distinct = sorted(set(located.values()))
        assert len(distinct) == 1, (
            f"{template}: SHORT position P{tag} (3 bullets) is split across pages "
            f"{[p + 1 for p in distinct]}: "
            + "; ".join(f"'{probe}' on page {page + 1}" for probe, page in located.items())
        )


def _long_probe_bullets(i: int) -> list[str]:
    tag = f"{i:02d}"
    return [
        f"L{tag}BULLET{j:02d} Textinhalt Randtest Aufzaehlungspunkt mit ausreichend "
        f"Laenge damit die Seite zuverlaessig ueberlaeuft und der Umbruch greift."
        for j in range(1, 13)
    ]


CV_LONG_ENTRIES_PROBE = TailoredCVData.model_validate(
    {
        "contact": {
            "name": "Long Entries Probe",
            "email": "long.entries@example.de",
            "phone": "+49 30 0000003",
            "location": "Berlin",
            "photo_url": None,
        },
        "show_photo": False,
        "work_history": [
            {
                "company": f"Firma L{i:02d} GmbH",
                "role": f"Rolle L{i:02d}",
                "start_date": f"{2030 - i:04d}-01",
                "end_date": f"{2031 - i:04d}-01",
                "bullets": _long_probe_bullets(i),
            }
            for i in range(1, 9)  # 8 positions x 12 bullets (brief's suggested shape)
        ],
    }
)


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_long_entries_break_after_head_plus_two_bullets(template):
    """8 positions x 12 bullets (every entry LONG). For every position that
    spans >1 page: the title's page also holds bullets 1 and 2 (the
    .entry-lead group); the entry's last page holds >=2 of its bullets (the
    .entry-tail group); every bullet's own text is intact on a single page
    (no bullet split mid-sentence).
    """
    html_out = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=CV_LONG_ENTRIES_PROBE, color=_default_context(), lang="de", labels=cv_labels("de")
    )
    pdf = await _html_to_pdf(html_out)
    pages = _pdf_pages_text(pdf)
    assert len(pages) >= 2, f"{template}: fixture rendered {len(pages)} page(s), need >=2"

    spanning_found = False
    for i in range(1, 9):
        tag = f"{i:02d}"
        title_needle = _ats_norm(f"Rolle L{tag}")
        title_hits = [p for p, text in enumerate(pages) if title_needle in text]
        assert title_hits, f"{template}: position L{tag} title dropped from the rendered PDF"
        title_page = title_hits[0]

        bullet_pages = []
        for j in range(1, 13):
            needle = _ats_norm(f"L{tag}BULLET{j:02d}")
            hits = [p for p, text in enumerate(pages) if needle in text]
            assert hits, (
                f"{template}: position L{tag} bullet {j:02d} not found intact on any "
                f"single page — it split mid-bullet"
            )
            bullet_pages.append(hits[0])

        touched_pages = set(bullet_pages) | {title_page}
        if len(touched_pages) <= 1:
            continue  # this position fit entirely on one page — not under test here
        spanning_found = True

        assert bullet_pages[0] == title_page and bullet_pages[1] == title_page, (
            f"{template}: LONG position L{tag} spans pages {[p + 1 for p in sorted(touched_pages)]} "
            f"but its title is on page {title_page + 1} while bullets 1/2 are on pages "
            f"{bullet_pages[0] + 1}/{bullet_pages[1] + 1} — the .entry-lead group split (#622)."
        )
        last_page = max(touched_pages)
        count_on_last = sum(1 for p in bullet_pages if p == last_page)
        assert count_on_last >= 2, (
            f"{template}: LONG position L{tag}'s last page ({last_page + 1}) holds only "
            f"{count_on_last} of its bullets — the .entry-tail group (>=2 bullets) split (#622)."
        )

    assert spanning_found, (
        f"{template}: no position in the 8x12-bullet fixture spans two pages on this "
        f"template — fixture not calibrated here, the break-after-lead invariant went untested"
    )


def _cert5_fixture(n_filler: int) -> TailoredCVData:
    """5 certifications (<=6, section-atomic) preceded by *n_filler* short
    filler BULLET LINES (packed 3 per position so every filler position stays
    SHORT/atomic itself), to shift the section's vertical start position.
    Bullet-line granularity (~5-6mm/step), not whole-position granularity
    (~15-20mm/step): measured (scratch probe, classic_german and compact_pro)
    that a 5-item certifications section's own height is ~35-40% of a page,
    so the section is forced to reset to the top of a fresh page once
    remaining space drops below that — capping the reachable start fraction
    at ~0.60-0.66 on every template tried, well under a naive 3-4-position
    step's resolution. Fine bullet-line steps are needed to land inside the
    narrow reachable window at all (see the module-level threshold below).
    """
    positions = []
    remaining = n_filler
    k = 0
    while remaining > 0:
        k += 1
        take = min(3, remaining)
        positions.append(
            {
                "company": f"Fuellfirma {k:02d} GmbH",
                "role": f"Fuellrolle {k:02d}",
                "start_date": f"{2020 - k:04d}-01",
                "end_date": f"{2021 - k:04d}-01",
                "bullets": [f"F{k:02d}B{b} Fuelltext" for b in range(1, take + 1)],
            }
        )
        remaining -= take
    return TailoredCVData.model_validate(
        {
            "contact": {
                "name": "Cert Filler Probe",
                "email": "cert.filler@example.de",
                "phone": "+49 30 0000004",
                "location": "Berlin",
                "photo_url": None,
            },
            "show_photo": False,
            "work_history": positions,
            "certifications": [
                {
                    "name": f"Zert{c:02d}CERT5PROBE",
                    "issuing_organization": f"Stelle{c:02d}",
                    "date_obtained": f"{2020 + c:04d}-01-01",
                }
                for c in range(1, 6)
            ],
        }
    )


CV_CERT12_PROBE = TailoredCVData.model_validate(
    {
        "contact": {
            "name": "Cert Twelve Probe",
            "email": "cert.twelve@example.de",
            "phone": "+49 30 0000005",
            "location": "Berlin",
            "photo_url": None,
        },
        "show_photo": False,
        "certifications": [
            {
                "name": f"Zert{c:02d}CERT12PROBE",
                "issuing_organization": f"Stelle{c:02d}",
                "date_obtained": f"{2020 + c:04d}-01-01",
            }
            for c in range(1, 13)
        ],
    }
)


#: Scenario A's "near a page end" floor. The brief's own suggestion ("bottom
#: 25% of a page", i.e. y_frac >= 0.75) is UNREACHABLE for this fixture on
#: every template measured: a 5-item certifications section (heading + 5
#: entries) occupies roughly 35-40% of a page's usable height, so
#: section-atomic's own "doesn't fit -> bump whole to next page, starting at
#: its top" rule caps the reachable start fraction well below 0.75 — measured
#: (scratch probe, bullet-line-granularity filler): classic_german maxes out
#: at y_frac~0.61, compact_pro at ~0.66, both asymptoting from below and
#: never crossing. 0.55 sits under both measured ceilings with margin, so the
#: scenario can actually fire instead of skipping on every template — this is
#: a deviation from the brief's literal number, kept honest by the comment
#: instead of silently tuned; see the report for the measured numbers.
_SCENARIO_A_NEAR_PAGE_END_FRAC = 0.55


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_list_sections_keep_together_or_leave_two(template):
    """Certifications (a list section, per #622 design point 2).

    Scenario A (<=6 items, section-atomic): a 5-item certifications section
    is rendered behind a growing amount of filler so its heading lands near
    the bottom of a page (see _SCENARIO_A_NEAR_PAGE_END_FRAC) for at least
    one filler count — the section (heading + all 5 items) must still land
    wholly on ONE page whenever that happens. Filler counts that don't
    produce a near-page-end start are skipped (not asserted on); if NONE do,
    the scenario skips with a reason instead of passing vacuously.

    Scenario B (>6 items): a 12-item certifications section — no page holds
    exactly 1 of its items, and the heading's own page holds >=2 items.
    """
    labels_de = cv_labels("de")
    cert_heading = _ats_norm(labels_de["certifications"])

    # --- Scenario A: 5 items, near a page end, for at least one filler count.
    qualifying_renders = []
    for n_filler in range(0, 24, 2):
        fixture = _cert5_fixture(n_filler)
        html_out = _jinja_env.get_template(CV_TEMPLATES[template]).render(
            cv=fixture, color=_default_context(), lang="de", labels=labels_de
        )
        pdf = await _html_to_pdf(html_out)
        bbox_pages = _bbox_pages(pdf)
        heading_page = None
        heading_y_frac = None
        for pidx, page in enumerate(bbox_pages):
            hits = [w for w in page["words"] if cert_heading.split()[0].lower() in w[4].lower()]
            if hits:
                y_min = min(w[1] for w in hits)
                heading_page = pidx
                heading_y_frac = y_min / page["height"]
                break
        if heading_page is None:
            continue  # heading not found as its own word run — try another filler count
        if heading_y_frac >= _SCENARIO_A_NEAR_PAGE_END_FRAC:
            qualifying_renders.append((n_filler, pdf))

    if not qualifying_renders:
        pytest.skip(
            f"{template}: no filler count in range(0, 24, 2) placed the certifications "
            f"heading past y_frac={_SCENARIO_A_NEAR_PAGE_END_FRAC} of a page — scenario A "
            f"untested for this template"
        )

    for n_filler, pdf in qualifying_renders:
        pages = _pdf_pages_text(pdf)
        cert_probes = [f"Zert{c:02d}CERT5PROBE" for c in range(1, 6)]
        located = {}
        for probe in cert_probes:
            needle = _ats_norm(probe)
            hits = [p for p, text in enumerate(pages) if needle in text]
            assert hits, f"{template} (filler={n_filler}): '{probe}' dropped from the rendered PDF"
            located[probe] = hits[0]
        distinct = sorted(set(located.values()))
        assert len(distinct) == 1, (
            f"{template} (filler={n_filler}): 5-item certifications section (<=6, "
            f"section-atomic) split across pages {[p + 1 for p in distinct]}: "
            + "; ".join(f"'{p}' on page {pg + 1}" for p, pg in located.items())
        )

    # --- Scenario B: 12 items — no page holds exactly 1 item; heading's page holds >=2.
    html_out = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=CV_CERT12_PROBE, color=_default_context(), lang="de", labels=labels_de
    )
    pdf = await _html_to_pdf(html_out)
    pages = _pdf_pages_text(pdf)

    item_pages: dict[int, int] = {}
    for c in range(1, 13):
        needle = _ats_norm(f"Zert{c:02d}CERT12PROBE")
        hits = [p for p, text in enumerate(pages) if needle in text]
        assert hits, f"{template}: certification {c:02d}/12 dropped from the rendered PDF"
        item_pages[c] = hits[0]

    # Locate the heading via bbox word search, not pypdf's joined page text:
    # academic.html.j2's .section-title has letter-spacing (like every other
    # template's section heading — 0.06-0.15em, pre-existing, untouched by
    # #621/#622) which Chromium/pypdf sometimes extracts as "Z E R T I F I..."
    # (one <word> per LETTER) rather than one word — reproduced on academic
    # specifically with this exact fixture; a plain _ats_norm substring
    # search on the pypdf-joined page text then never matches. poppler's
    # -bbox-layout word segmentation is robust to it (used successfully for
    # the same heading in Scenario A above), so reuse that here too.
    bbox_pages = _bbox_pages(pdf)
    heading_first_word = cert_heading.split()[0]
    heading_page = None
    for pidx, page in enumerate(bbox_pages):
        if any(heading_first_word in w[4].lower() for w in page["words"]):
            heading_page = pidx
            break
    assert heading_page is not None, f"{template}: certifications heading dropped from the rendered PDF"

    counts_per_page: dict[int, int] = {}
    for page in item_pages.values():
        counts_per_page[page] = counts_per_page.get(page, 0) + 1

    lonely = [p for p, n in counts_per_page.items() if n == 1]
    assert not lonely, (
        f"{template}: 12-item certifications section leaves exactly 1 item alone on "
        f"page(s) {[p + 1 for p in lonely]} — item->page map: "
        f"{ {c: p + 1 for c, p in item_pages.items()} }"
    )
    heading_page_count = counts_per_page.get(heading_page, 0)
    assert heading_page_count >= 2, (
        f"{template}: certifications heading is on page {heading_page + 1}, which holds "
        f"only {heading_page_count} item(s) — need >=2 (the .section-lead group)."
    )


# ---------------------------------------------------------------------------
# #621 follow-up (bug-batch 3): page 1 of a LETTER keeps its capacity.
#
# Moving the margin from `.page` padding to `@page` is neutral for page 1 —
# unless a template's header used to bleed INTO the padding (lebenslauf_letter's
# did: its negative margin cancelled `.page`'s padding, so the band reached the
# paper edge and page 1's content started at ~5 mm). Insetting that band into a
# full 20 mm `@page` top margin moved the first text to 25 mm and cost ~20 mm of
# page-1 height: the calibrated LETTER_DE_BUDGET fixture and every captured
# 235-258-word letter flipped 1 -> 2 pages. A letter is a one-page document, so
# page 1 must not lose capacity to a margin fix; lebenslauf_letter now carries a
# `@page :first` inset (executive_letter's precedent).
#
# TWO tests, deliberately split — the first is the durable gate, the second the
# calibrated witness. An earlier version of this pin asserted `== 1 page` across
# ALL SEVEN letter templates; that is exactly the absolute, font-metric-sensitive
# page-count gate this suite's own rule forbids (test_roundtrip.py's #547 notes:
# 3 of the 7 templates ask for Georgia/Palatino/Times New Roman, absent here and
# on a bare CI runner, so they are already font-substituted). Narrowed to the one
# template that actually regressed, mirroring
# test_letter_signature_orphans_less_often_547's own scoping (adversarial pass,
# 2026-08-30).
# ---------------------------------------------------------------------------

_PAGE1_TOP_INSET_CEILING_MM = 6.0


def test_lebenslauf_letter_keeps_a_page1_top_inset_621():
    """The durable, font-independent gate: the CSS budget read from source.

    `@page :first` must keep page 1's top inset small (the header band sat at
    ~5 mm before #621), while the outer `@page` rule keeps the real margin for
    continuation pages — that is the whole point of #621 and must not be
    traded away to fix page 1.
    """
    source = (LETTER_TEMPLATES_DIR / LETTER_TEMPLATES["classic_german"]).read_text()
    first_top = _read_first_page_top_override_mm(source)
    assert first_top is not None, (
        "lebenslauf_letter must declare @page :first — without it the inset header "
        "band costs ~20 mm of page-1 capacity and one-page letters run to two pages"
    )
    assert first_top <= _PAGE1_TOP_INSET_CEILING_MM, (
        f"@page :first margin-top is {first_top}mm — page 1 loses capacity above "
        f"{_PAGE1_TOP_INSET_CEILING_MM}mm"
    )
    top, _right, _bottom, _left = _read_page_margin_mm(source)
    assert top > first_top, (
        f"the outer @page top margin ({top}mm) must stay larger than the :first "
        f"override ({first_top}mm) — continuation pages are what #621 is about"
    )


@pytest.mark.asyncio
async def test_letter_page1_capacity_holds_621():
    """One calibrated Chromium render on the ONE template that regressed.

    Same class as test_letter_signature_orphans_less_often_547: a single
    template/fixture pair, NOT a claim about every letter. LETTER_DE_BUDGET
    rendered to 1 page on every letter template before #621 (W2 Part-3 table);
    on this template it flipped to 2 until the `@page :first` inset.
    """
    from test_roundtrip import LETTER_DE_BUDGET

    html = _jinja_env.get_template(LETTER_TEMPLATES["classic_german"]).render(
        letter=LETTER_DE_BUDGET,
        color=_default_color_context(),
        lang="de",
        labels=cover_letter_labels("de"),
    )
    pdf = await _html_to_pdf(html)
    pages = _bbox_pages(pdf)
    assert len(pages) == 1, (
        f"lebenslauf_letter: the calibrated LETTER_DE_BUDGET letter rendered to "
        f"{len(pages)} pages — it was 1 page before #621; a margin fix must not eat "
        "page-1 capacity (~20 mm was lost to the inset header until @page :first)"
    )


# ---------------------------------------------------------------------------
# #622 — STANDALONE projects (cv.projects, US187) get the same break policy as
# work_history entries. Shipped in all 7 templates but pinned by nothing until
# now: the W2 report itself described them as "unconditional atomicity", which
# the diff contradicts (only the NESTED job.projects case stayed atomic). An
# implemented-but-unpinned path is one refactor away from silently reverting —
# and an incorrect report claim is how it stays unnoticed (adversarial pass,
# 2026-08-30, MINOR #3).
# ---------------------------------------------------------------------------


def _project_probe_bullets(i: int) -> list[str]:
    tag = f"{i:02d}"
    return [
        f"P{tag}BULLET{j:02d} Projektinhalt Randtest Aufzaehlungspunkt mit ausreichend "
        f"Laenge damit die Seite zuverlaessig ueberlaeuft und der Umbruch greift."
        for j in range(1, 13)
    ]


CV_STANDALONE_PROJECTS_PROBE = TailoredCVData.model_validate(
    {
        "contact": {
            "name": "Standalone Projects Probe",
            "email": "standalone.projects@example.de",
            "phone": "+49 30 0000004",
            "location": "Berlin",
            "photo_url": None,
        },
        "show_photo": False,
        "work_history": [
            {
                "company": "Firma S01 GmbH",
                "role": "Rolle S01",
                "start_date": "2029-01",
                "end_date": "2030-01",
                "bullets": ["S01BULLET01 Kurzer Eintrag als Vorspann fuer die Projektsektion."],
            }
        ],
        "projects": [
            {"name": f"Projekt PJ{i:02d}", "bullets": _project_probe_bullets(i)}
            for i in range(1, 5)  # 4 standalone projects x 12 bullets — all LONG
        ],
    }
)


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_cv_standalone_long_projects_break_after_head_plus_two_bullets(template):
    """The work_history invariant, asserted on `cv.projects`: a standalone
    project that spans a page boundary keeps its heading with bullets 1-2 and
    leaves >= 2 bullets on its last page, and no bullet splits mid-sentence."""
    html_out = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=CV_STANDALONE_PROJECTS_PROBE,
        color=_default_context(),
        lang="de",
        labels=cv_labels("de"),
    )
    pdf = await _html_to_pdf(html_out)
    pages = _pdf_pages_text(pdf)
    assert len(pages) >= 2, f"{template}: fixture rendered {len(pages)} page(s), need >=2"

    spanning_found = False
    for i in range(1, 5):
        tag = f"{i:02d}"
        head_needle = _ats_norm(f"Projekt PJ{tag}")
        head_hits = [p for p, text in enumerate(pages) if head_needle in text]
        assert head_hits, f"{template}: standalone project PJ{tag} dropped from the PDF"
        head_page = head_hits[0]

        bullet_pages = []
        for j in range(1, 13):
            needle = _ats_norm(f"P{tag}BULLET{j:02d}")
            hits = [p for p, text in enumerate(pages) if needle in text]
            assert hits, (
                f"{template}: standalone project PJ{tag} bullet {j:02d} not found intact "
                f"on any single page — it split mid-bullet"
            )
            bullet_pages.append(hits[0])

        touched = set(bullet_pages) | {head_page}
        if len(touched) <= 1:
            continue
        spanning_found = True

        assert bullet_pages[0] == head_page and bullet_pages[1] == head_page, (
            f"{template}: LONG standalone project PJ{tag} spans pages "
            f"{[p + 1 for p in sorted(touched)]} but its heading is on page {head_page + 1} "
            f"while bullets 1/2 are on pages {bullet_pages[0] + 1}/{bullet_pages[1] + 1} — "
            f"the .entry-lead group split (#622)."
        )
        last_page = max(touched)
        count_on_last = sum(1 for p in bullet_pages if p == last_page)
        assert count_on_last >= 2, (
            f"{template}: LONG standalone project PJ{tag}'s last page ({last_page + 1}) holds "
            f"only {count_on_last} of its bullets — the .entry-tail group split (#622)."
        )

    assert spanning_found, (
        f"{template}: no standalone project in the 4x12-bullet fixture spans two pages — "
        f"fixture not calibrated here, the invariant went untested"
    )
