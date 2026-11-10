# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RU-3 / ruling P-4 (Hazelnut build 8): long bullets must not leave pages blank.

The founder's re-UAT CV (Kreativ, Sonnet 5.5) had bullets of 50–78 words. Every
keep-together unit (a role's head + 2 bullets, a project's name + first bullet, a
"short" 3-bullet role, the projects travelling with the bullet before them) was
~10–35 lines tall, and each page break pushed a whole unit down: ~0.8 of a page
blank over 5 pages (p2 ¼, p3 ⅔, p5 ½). All seven templates copy the same break
policy and all showed it on this shape.

This fixture copies only that CV's SHAPE (6 roles, the words per bullet, projects
per role, 22 chips, 4 education entries); every word is invented. It renders
through the real Chromium path and reads the PDF: every page but the last is
filled (its lowest word sits at ≥ 80 % of the page height — the bottom margin caps
it near 95 %), and no line of a bullet is left alone (≥ 3 lines each side of a
split, CSS orphans/widows 3).

Before the fix (same fixture, 2026-11-10): Kreativ 6 pp [0.87, 0.91, 0.89, 0.62,
0.93, 0.62]; compact_pro 6 pp with p2 0.50; executive 6 pp with p4 0.52. After:
Kreativ 5 pp, every non-last page ≥ 0.90 (P-4); 4 pp after the P-5 rework."""
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

_WORDS = ("Verantwortung für Planung Steuerung und Abnahme eines bereichsübergreifenden Vorhabens "
          "mit messbarem Ergebnis für Fachbereiche Betrieb und externe Partner einschließlich "
          "Budgetplanung Lieferantenauswahl Risikobewertung Qualitätssicherung Schulung der "
          "Anwender und Übergabe in den laufenden Betrieb nach festgelegten Kriterien sowie "
          "regelmäßiger Berichterstattung an die Geschäftsführung und den Lenkungskreis").split()

#: (own bullet word counts, [project bullet word counts]) per role — the SHAPE only.
_SHAPE = [
    ([50, 65], [[78, 47, 72], [25]]),
    ([46, 31, 68], []),
    ([56], [[49, 53]]),
    ([40, 55], [[65], [44]]),
    ([30], []),
    ([9], []),
]


def _bullets(tag: str, lens: list[int]) -> list[str]:
    return [f"{tag}{i:02d} " + " ".join((_WORDS * 3)[i: i + n - 1]) + "." for i, n in enumerate(lens, 1)]


def _cv(long: bool = False) -> TailoredCVData:
    """``long``: every bullet is 110 words (~850 characters, 8+ lines in every
    template), so bullets MUST split somewhere — the probe for the 3/3 rule."""
    return TailoredCVData.model_validate({
        "contact": {"name": "Paula Probe", "email": "paula.probe@example.de", "phone": "+49 30 0000000",
                    "location": "Berlin", "photo_url": None},
        "show_photo": False,
        "summary": " ".join((_WORDS * 2)[:59]) + ".",
        "work_history": [
            {
                "company": f"Probefirma {k} GmbH", "role": f"Leitung Bereich {k}",
                "start_date": f"{2024 - 3 * k:04d}-01", "end_date": None if k == 0 else f"{2026 - 3 * k:04d}-12",
                "is_current": k == 0, "bullets": _bullets(f"R{k}B", [110] * len(own) if long else own),
                "team_size": 2 if k == 0 else None,
                "projects": [{"name": f"Projekt {k}.{j}", "bullets": _bullets(f"R{k}P{j}B", [110] * len(pl) if long else pl)}
                             for j, pl in enumerate(projects, 1)],
            }
            for k, (own, projects) in enumerate(_SHAPE)
        ],
        "projects": [{"name": "Eigenes Vorhaben", "bullets": _bullets("TP", [25, 33])}],
        "skills": [f"Kenntnis {i}" for i in range(1, 23)],
        "languages": [{"language": "Deutsch", "level": "Muttersprache"}, {"language": "Englisch", "level": "C1"}],
        "education": [{"institution": f"Probe-Hochschule {i}", "degree": d, "field": "",
                       "start_date": f"{1995 + 4 * i}-10", "end_date": f"{1999 + 4 * i}-09"}
                      for i, d in enumerate(["Abitur", "B.Sc. Informatik", "M.Sc. Informatik", "Zertifikatsstudium"])],
    })


#: Pages for this shape (before the fix in brackets), with ruling P-4 and the P-6
#: guard (a bullet splits only when it is surely 6+ lines): Kreativ 4 (6; the P-5
#: band), academic 5 (5; the projects never open a page alone, D-35 cl. 4),
#: classic 5 (5), compact_pro 5 (6), executive 4 (6), modern_swiss 4 (4), tech 4 (5).
_MAX_PAGES = {"creative_sidebar": 4, "academic": 5, "classic_german": 5, "compact_pro": 5,
              "executive": 4, "modern_swiss": 4, "tech_developer": 4}


#: The lowest fill of a non-last page, measured with the P-6 guard (a bullet splits
#: only when it is surely 6+ lines), minus 0.03. Before the fix: Kreativ 0.62,
#: compact_pro 0.50, executive 0.52, academic 0.51, tech 0.48, modern_swiss 0.59.
_MIN_FILL = {"creative_sidebar": 0.86, "academic": 0.59, "classic_german": 0.75, "compact_pro": 0.90,
             "executive": 0.90, "modern_swiss": 0.81, "tech_developer": 0.84}


_PAGE_RE = re.compile(r'<page width="[\d.]+" height="([\d.]+)">(.*?)</page>', re.S)
_WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="[\d.]+" yMax="([\d.]+)">(.*?)</word>', re.S)


def _pages(pdf: bytes) -> list[tuple[float, list[tuple[float, float, float, str]]]]:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf)
        path = f.name
    try:
        out = subprocess.run(["pdftotext", "-bbox-layout", path, "-"], capture_output=True, text=True, check=True).stdout
    finally:
        Path(path).unlink(missing_ok=True)
    return [
        (float(h), [(float(x0), float(y0), float(y1), w) for x0, y0, y1, w in _WORD_RE.findall(body)])
        for h, body in _PAGE_RE.findall(out)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_long_bullets_leave_no_page_half_empty(template):
    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=_cv(), color=_default_context(), lang="de", labels=cv_labels("de"))
    pages = _pages(await _html_to_pdf(html))
    assert len(pages) >= 3, f"{template}: fixture too short to test page fill ({len(pages)} pp)"
    fills = [round(max(y1 for _x, _y0, y1, _w in words) / h, 2) if words else 0.0 for h, words in pages]
    assert all(f >= _MIN_FILL[template] for f in fills[:-1]), f"{template}: a page is left part-empty: {fills}"
    assert len(pages) <= _MAX_PAGES[template], f"{template}: {len(pages)} pages for the RU-3 shape: {fills}"


def _split_lines(pages) -> list[tuple[int, int, int]]:
    """For every page break, the lines of text after the last marker of page k and
    before the first marker / heading of page k+1 — the two halves of a split bullet."""
    out = []
    for k in range(len(pages) - 1):
        _h, a = pages[k]
        _h2, b = pages[k + 1]
        main_a, main_b = a, b  # one column on every page (P-5: Kreativ has no sidebar)
        if not main_a or not main_b:
            continue
        marker = re.compile(r"^(R\d+(P\d+)?B|TP)\d{2}$")
        tops_a = sorted({round(w[1]) for w in main_a})
        last_marker = max((round(w[1]) for w in main_a if marker.match(w[3])), default=None)
        tops_b = sorted({round(w[1]) for w in main_b})
        first_marker = min((round(w[1]) for w in main_b if marker.match(w[3]) or w[3].lower().startswith(("leitung", "projekt", "ausbildung", "eigenes"))), default=None)
        if last_marker is None or first_marker is None:
            continue
        tail = len([t for t in tops_a if t >= last_marker - 4])
        head = len([t for t in tops_b if t < first_marker - 4])  # a bullet glyph sits up to ~3 pt above its text
        if head:  # page k+1 opens inside a bullet
            out.append((k + 1, tail, head))
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
@pytest.mark.parametrize("long", [False, True], ids=["re-uat-shape", "110-word-bullets"])
async def test_a_bullet_splits_only_with_three_lines_each_side(template, long):
    """Ruling P-4 (+ P-6 guard): a bullet may split only with >= 3 lines on each page.
    Chromium drops widows for a paragraph under orphans + widows lines (4 lines split
    3/1, 5 lines 3/2), so only a bullet of split_chars or more (surely 6+ lines) may
    split at all. On the 110-word shape bullets MUST split, so the probe is live."""
    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=_cv(long=long), color=_default_context(), lang="de", labels=cv_labels("de"))
    pages = _pages(await _html_to_pdf(html))
    splits = _split_lines(pages)
    if long:
        assert splits, f"{template}: 110-word bullets never split — the probe tests nothing"
    for page, tail, head in splits:
        assert tail >= 3 and head >= 3, f"{template}: break before p{page + 1}: {tail} line(s) / {head} line(s)"
