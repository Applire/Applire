# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""U-10 (Hazelnut build 5, founder UAT 2026-10-28) — the skills chips wrap evenly.

The chip templates (``modern_swiss``, ``tech_developer``, ``classic_german``)
render the skills as one wrapping flex row of chips. #622's list-section break
policy split that row by ITEM COUNT into three lists (first 2 chips, the middle,
last 2) so the PDF would keep a lead and a tail group together — but each list
wraps on its own, so the first row always held two chips and a short row
appeared before the tail ("chips wrap with uneven spacing/rows").

Now: ONE list. Page-break protection moves to the row: a chip is unbreakable,
Chromium breaks a wrapping flex container only between its rows, and the
heading keeps its first row (``break-after: avoid`` on the heading, #357).

Both properties are measured on real renders, never inferred from CSS:
1. the rendered chip rows (``getBoundingClientRect``) carry no short row before
   the last one;
2. a filler sweep that pushes the section across a page break: the heading's
   page holds >= 1 chip and every chip lands whole on exactly one page.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from applire.schemas.cv import TailoredCVData
from applire.services.color_detection import _default_context
from applire.services.cv import _TEMPLATE_FILES as CV_TEMPLATES
from applire.services.cv import _html_to_pdf, _jinja_env
from applire.templates.labels import cv_labels

CHIP_TEMPLATES = ["modern_swiss", "tech_developer", "classic_german"]
_LIST_CLASS = {"modern_swiss": "skills-grid", "tech_developer": "skills-grid", "classic_german": "skills-list"}

# Synthetic, varied lengths — one probe token per chip so pdftotext finds it.
SKILLS = [
    f"S{i:02d}CHIP {name}"
    for i, name in enumerate(
        [
            "Python", "Kubernetes", "Qualitätsmanagement nach ISO 9001", "SQL",
            "Stakeholder-Management", "Lean Six Sigma", "Terraform",
            "Agile Methoden (Scrum, Kanban)", "Go", "CI/CD", "Lieferantenaudits",
            "Datenanalyse", "Teamführung", "Prozessoptimierung",
        ],
        start=1,
    )
]


def _fixture(n_filler: int) -> TailoredCVData:
    positions = [
        {
            "company": f"Fuellfirma {k:02d} GmbH",
            "role": f"Fuellrolle {k:02d}",
            "start_date": f"{2020 - k:04d}-01",
            "end_date": f"{2021 - k:04d}-01",
            "bullets": [f"F{k:02d}B{b} Fuelltext fuer die Seitenlaenge" for b in range(1, 3)],
        }
        for k in range(1, n_filler + 1)
    ]
    return TailoredCVData.model_validate(
        {
            "contact": {
                "name": "Skill Chip Probe",
                "email": "chip.probe@example.de",
                "phone": "+49 30 0000009",
                "location": "Berlin",
                "photo_url": None,
            },
            "show_photo": False,
            "work_history": positions,
            "skills": SKILLS,
        }
    )


def _render(template: str, n_filler: int = 0) -> str:
    return _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=_fixture(n_filler), color=_default_context(), lang="de", labels=cv_labels("de")
    )


@pytest.mark.parametrize("template", CHIP_TEMPLATES)
def test_skills_render_as_one_list(template):
    html_out = _render(template)
    cls = _LIST_CLASS[template]
    assert len(re.findall(rf'<ul class="{cls}"', html_out)) == 1, (
        f"{template}: the skills chips are split into several lists — each wraps on its own (U-10)"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("template", CHIP_TEMPLATES)
async def test_skills_chip_rows_have_no_short_row_before_the_last(template):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={"width": 794, "height": 1123})
            await page.set_content(_render(template))
            rows = await page.eval_on_selector_all(
                f"ul.{_LIST_CLASS[template]} > li",
                """els => { const r = {}; for (const e of els) {
                     const y = Math.round(e.getBoundingClientRect().top);
                     const w = e.getBoundingClientRect().width;
                     (r[y] = r[y] || []).push(w); }
                   return Object.keys(r).map(Number).sort((a, b) => a - b).map(k => r[k]); }""",
            )
            width = await page.eval_on_selector(
                f"ul.{_LIST_CLASS[template]}", "e => e.getBoundingClientRect().width"
            )
        finally:
            await browser.close()
    assert len(rows) >= 2, f"{template}: fixture did not wrap — not under test"
    # A row before the last one wraps only because the NEXT chip did not fit:
    # its used width + that chip must exceed the line. A split list breaks this.
    for i, row in enumerate(rows[:-1]):
        next_chip = rows[i + 1][0]
        assert sum(row) + next_chip > width * 0.85, (
            f"{template}: row {i + 1} wraps early ({len(row)} chips, {sum(row):.0f}px of "
            f"{width:.0f}px, next chip {next_chip:.0f}px) — rows: {[len(r) for r in rows]}"
        )


def _pdf_word_pages(pdf: bytes) -> list[list[str]]:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf)
        path = f.name
    try:
        out = subprocess.run(
            ["pdftotext", "-bbox-layout", path, "-"], capture_output=True, text=True, check=True
        ).stdout
    finally:
        Path(path).unlink(missing_ok=True)
    return [
        re.findall(r"<word [^>]*>(.*?)</word>", body, re.S)
        for body in re.findall(r"<page [^>]*>(.*?)</page>", out, re.S)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("template", ["modern_swiss", "classic_german"])
async def test_skills_section_breaks_between_rows_and_keeps_its_heading(template):
    """tech_developer renders skills above the work history, so filler cannot
    push its section across a page — the two templates whose skills follow
    the stations are swept."""
    heading = cv_labels("de")["skills"].split()[0].lower()
    straddled = 0
    for n_filler in range(4, 40):
        pages = _pdf_word_pages(await _html_to_pdf(_render(template, n_filler)))
        chip_pages = {
            i: [p for p, words in enumerate(pages) if f"S{i:02d}CHIP" in words]
            for i in range(1, len(SKILLS) + 1)
        }
        for i, hits in chip_pages.items():
            assert len(hits) == 1, f"{template} (filler={n_filler}): chip {i} on pages {hits}"
        spread = sorted({hits[0] for hits in chip_pages.values()})
        if len(spread) == 1:
            continue
        straddled += 1
        head_page = next(
            p for p, words in enumerate(pages) if any(w.lower().startswith(heading) for w in words)
        )
        on_head = sum(1 for hits in chip_pages.values() if hits[0] == head_page)
        assert on_head >= 1, (
            f"{template} (filler={n_filler}): the skills heading is orphaned on page "
            f"{head_page + 1} — chips on pages {[p + 1 for p in spread]}"
        )
        assert spread == [head_page, head_page + 1], (
            f"{template} (filler={n_filler}): chips spread over {[p + 1 for p in spread]}"
        )
        if straddled >= 2:
            break
    if straddled == 0:
        pytest.skip(f"{template}: no filler count put the skills section across a page break")
