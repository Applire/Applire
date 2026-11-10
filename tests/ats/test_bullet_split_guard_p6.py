# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ruling P-4 + P-6 (Hazelnut build 8): only a bullet of 6+ lines may split, with at
least 3 lines on each page; a shorter bullet never splits.

Chromium keeps ``orphans`` but drops ``widows`` when a paragraph has fewer lines than
orphans + widows (measured 2026-11-10: 4 lines split 3/1, 5 lines 3/2; from 6 lines on
every split is >= 3/3). So each CV template marks a bullet ``li-split`` only when it
has at least ``split_chars`` characters (6 x the template's widest bullet line x 1.15);
every other bullet is one unit (#622).

Per template, in German and English, own bullets AND a nested project bullet (the
narrower, further-indented column):
1. the estimate errs toward "stay whole": every bullet marked ``li-split`` renders in
   6+ lines;
2. a bullet just under ``split_chars`` never splits, wherever the page break falls;
3. a bullet just at ``split_chars`` (the fewest lines a marked bullet can have)
   renders 6+ lines and splits no worse than 3/3, wherever the page break falls.
Each bullet draws on its own vocabulary, so its lines are counted exactly from the PDF.
"""
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from applire.schemas.cv import TailoredCVData
from applire.services.color_detection import _default_context
from applire.services.cv import _TEMPLATE_FILES as CV_TEMPLATES
from applire.services.cv import _jinja_env
from applire.templates.labels import cv_labels

#: Disjoint vocabularies: a word tells which bullet it belongs to.
_VOCAB = {
    "de": {
        "under": "Abstimmung Lieferketten Qualitätsprüfung Dokumentation Wartungsverträge Schnittstellen "
                 "Kostenübersicht Rahmenvereinbarung Inbetriebnahme Störungsanalyse Freigabeprozess",
        "own": "Verantwortung Planungsrunde Steuerkreis Abnahmeprotokoll Fachbereichen Betriebsführung "
               "Partnerunternehmen Lieferantenauswahl Risikobewertung Anwenderschulung Berichtswesen",
        "proj": "Migrationspfad Datenbestand Altsystem Testumgebung Umstellungsplan Rückfallebene "
                "Lasttest Parallelbetrieb Nutzerakzeptanz Stichtag Abschlussbericht",
        "fill": "Werdegang Erfahrung Branche Kunden Projekte Teams Ergebnisse Ziele Methoden Werkzeuge",
    },
    "en": {
        "under": "coordinated supply chains quality checks documented maintenance contracts interfaces "
                 "cost overview framework agreements commissioning incident analysis approval process",
        "own": "responsible planning rounds steering board sign-off protocol business units operations "
               "partner companies supplier selection risk assessment staff training reporting",
        "proj": "migration path data inventory legacy system test environment cutover plan fallback "
                "load test parallel run user acceptance deadline closing report",
        "fill": "career experience industry clients projects teams results goals methods tools",
    },
}


def test_the_vocabularies_are_disjoint():
    """A word shared by two bullets would count one bullet's lines as the other's."""
    for lang, kinds in _VOCAB.items():
        seen: dict[str, str] = {}
        for kind, words in kinds.items():
            for w in {w.lower() for w in words.split()}:
                assert w not in seen, f"{lang}: '{w}' in {kind} and {seen[w]}"
                seen[w] = kind


def _text(lang: str, kind: str, n_chars: int) -> str:
    """Words of ``kind``'s vocabulary, as long as possible but at most ``n_chars``."""
    words, out = _VOCAB[lang][kind].split(), ""
    i = 0
    while True:
        nxt = (out + " " if out else "") + words[i % len(words)]
        if len(nxt + ".") > n_chars:
            return out + "."
        out, i = nxt, i + 1


def _text_from(lang: str, kind: str, n_chars: int) -> str:
    """The SHORTEST text of ``kind``'s vocabulary with at least ``n_chars``: the worst
    case for the estimate (fewest lines a marked bullet can have)."""
    words, out, i = _VOCAB[lang][kind].split(), "", 0
    while len(out + ".") < n_chars:
        out, i = (out + " " if out else "") + words[i % len(words)], i + 1
    return out + "."


def _cv(lang: str, split_chars: int, fill_words: int) -> tuple[TailoredCVData, dict[str, str]]:
    bullets = {
        "under": _text(lang, "under", split_chars - 1),    # just under: must never split
        "own": _text_from(lang, "own", split_chars),       # just at: marked li-split, own bullet
        "proj": _text_from(lang, "proj", split_chars),     # just at: marked, nested project
    }
    fill = " ".join((_VOCAB[lang]["fill"].split() * 200)[:fill_words]) + "."
    cv = TailoredCVData.model_validate({
        "contact": {"name": "Paula Probe", "email": "paula.probe@example.de", "location": "Berlin"},
        "show_photo": False, "summary": fill,
        "work_history": [
            {"company": "Probefirma A GmbH", "role": "Leitung A", "start_date": "2020-01", "end_date": None,
             "is_current": True, "bullets": [bullets["under"], bullets["own"]],
             "projects": [{"name": "Vorhaben A", "bullets": [bullets["proj"]]}]},
            {"company": "Probefirma B GmbH", "role": "Leitung B", "start_date": "2015-01", "end_date": "2019-12",
             "bullets": ["Kurz."]},
        ],
        "skills": ["Python"],
    })
    return cv, bullets


_PAGE_RE = re.compile(r'<page width="[\d.]+" height="[\d.]+">(.*?)</page>', re.S)
_WORD_RE = re.compile(r'<word xMin="[\d.]+" yMin="([\d.]+)" xMax="[\d.]+" yMax="[\d.]+">(.*?)</word>', re.S)


def _lines_per_page(pdf: bytes, lang: str, kind: str) -> list[int]:
    """Lines of the ``kind`` bullet on each page (a line = one distinct word top)."""
    vocab = {w.strip(".").lower() for w in _VOCAB[lang][kind].split()}
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf)
        path = f.name
    try:
        out = subprocess.run(["pdftotext", "-bbox-layout", path, "-"], capture_output=True, text=True, check=True).stdout
    finally:
        Path(path).unlink(missing_ok=True)
    res = []
    for body in _PAGE_RE.findall(out):
        tops = sorted({round(float(y)) for y, w in _WORD_RE.findall(body) if w.strip(".").lower() in vocab})
        merged = [t for i, t in enumerate(tops) if i == 0 or t - tops[i - 1] > 4]
        res.append(len(merged))
    return [n for n in res if n]


def _split_chars(template: str) -> int:
    src = (Path(_jinja_env.loader.searchpath[0]) / CV_TEMPLATES[template]).read_text()
    return int(re.search(r"\{% set split_chars = (\d+) %\}", src).group(1))


@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_class_reaches_the_rendered_html_only_from_split_chars(template, lang):
    """Seam: the class is assigned in the template; the rendered HTML carries it on
    the long own AND project bullets and not on the one just under the threshold."""
    cv, b = _cv(lang, _split_chars(template), 20)
    html = _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=cv, color=_default_context(), lang=lang, labels=cv_labels(lang))
    assert html.count('<li class="li-split">') == 2, template
    assert f'<li class="li-split">{b["own"]}</li>' in html
    assert f'<li class="li-split">{b["proj"]}</li>' in html
    assert f"<li>{b['under']}</li>" in html
    css = html[:html.index("</style>")]
    assert re.search(r"\.section \.entry-bullets li \{\s*break-inside: avoid;", css), template
    assert re.search(r"\.section \.entry-bullets li\.li-split \{[^}]*break-inside: auto;[^}]*orphans: 3;[^}]*widows: 3;", css), template


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
@pytest.mark.parametrize("lang", ["de", "en"])
async def test_only_a_six_line_bullet_splits_and_then_three_lines_each_side(template, lang):
    """The page break walks through the three bullets as the summary grows. One
    browser per case with ``cv._html_to_pdf``'s own ``page.pdf`` options (the
    provenance mark it adds afterwards does not touch the layout)."""
    from playwright.async_api import async_playwright

    split = _split_chars(template)
    seen_split = {"own": 0, "proj": 0}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            for fill_words in range(120, 900, 21):
                cv, _b = _cv(lang, split, fill_words)
                page = await browser.new_page()
                await page.set_content(_jinja_env.get_template(CV_TEMPLATES[template]).render(
                    cv=cv, color=_default_context(), lang=lang, labels=cv_labels(lang)), wait_until="networkidle")
                pdf = await page.pdf(format="A4", print_background=True,
                                     margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
                await page.close()
                under = _lines_per_page(pdf, lang, "under")
                assert len(under) == 1, f"{template}/{lang}: a bullet under split_chars split {under} (fill {fill_words})"
                for kind in ("own", "proj"):
                    per_page = _lines_per_page(pdf, lang, kind)
                    assert sum(per_page) >= 6, (
                        f"{template}/{lang}: the estimate called a {sum(per_page)}-line {kind} bullet long "
                        f"({split} chars) — it must err toward staying whole")
                    if len(per_page) > 1:
                        seen_split[kind] += 1
                        assert min(per_page) >= 3, f"{template}/{lang}: {kind} bullet split {per_page} (fill {fill_words})"
        finally:
            await browser.close()
    assert seen_split["own"] and seen_split["proj"], (
        f"{template}/{lang}: the page break never crossed a long own and project bullet: {seen_split}")
