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
