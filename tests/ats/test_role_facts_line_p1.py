# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Founder ruling P-1 (Hazelnut build 5) — the per-role fact line.

A blind panel read ``Teamgröße: 12 · Budget: … · Branche: …`` under a job entry as
"template-generated". The ruling, on EVERY CV template and the .docx export:

1. the industry ("Branche: …" / "Industry: …") is never rendered;
2. the fact line stays only where it holds a figure (team size and/or budget),
   rendered WITHOUT labels — ``12 Personen · ca. 2 Mio. EUR``;
3. no figure -> no line (never the industry alone).

The templates and the .docx share ONE producer (``templates.filters.role_facts``).
These tests walk ``_TEMPLATE_FILES`` itself (an eighth template joins the walk
automatically), assert on the rendered HTML, on the real Chromium PDF's extracted
text (ADR-039 ``extract_text``), and on the .docx — PDF, preview (same HTML) and
export must agree.
"""
import re
from pathlib import Path

import pytest

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import extract_text
from applire.services.color_detection import _default_context
from applire.services.cv import _TEMPLATE_FILES as CV_TEMPLATES
from applire.services.cv import _html_to_pdf, _jinja_env
from applire.services.office_export.cv_docx import render_cv_docx
from applire.templates.labels import cv_labels

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "backend" / "applire" / "templates"
_FACTS_RE = re.compile(r'<div class="entry-facts">(.*?)</div>', re.S)

_ROLES = [
    # (company, team_size, budget_managed, industry_context)
    ("Alphawerk GmbH", 12, "ca. 2 Mio. EUR", "INDMARKA Logistik"),
    ("Betawerk GmbH", None, None, "INDMARKB Chemie"),       # industry alone -> no line
    ("Gammawerk GmbH", None, "bis zu 500 TEUR", None),       # budget alone
    ("Deltawerk GmbH", 1, "6000000", "INDMARKD Handel"),      # unit-less budget dropped
    ("Epsilonwerk GmbH", None, None, None),                  # nothing -> no line
]

EXPECTED = {
    "de": ["12 Personen · ca. 2 Mio. EUR", "bis zu 500 TEUR", "1 Person"],
    "en": ["12 people · ca. 2 Mio. EUR", "bis zu 500 TEUR", "1 person"],
}


def _cv() -> TailoredCVData:
    return TailoredCVData.model_validate(
        {
            "contact": {"name": "Fakt Zeile", "email": "fakt.zeile@example.de", "photo_url": None},
            "show_photo": False,
            "work_history": [
                {
                    "company": company,
                    "role": f"Leitung {k}",
                    "start_date": f"{2024 - 2 * k:04d}-01",
                    "end_date": f"{2025 - 2 * k:04d}-12",
                    "bullets": [f"R{k}BULLET Verantwortung fuer den Bereich."],
                    "team_size": team,
                    "budget_managed": budget,
                    "industry_context": industry,
                }
                for k, (company, team, budget, industry) in enumerate(_ROLES)
            ],
            "skills": ["Python"],
        }
    )


def _html(template: str, lang: str) -> str:
    return _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=_cv(), color=_default_context(), lang=lang, labels=cv_labels(lang)
    )


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " "))


def _no_labels_no_industry(text: str, lang: str) -> None:
    labels = cv_labels(lang)
    for key in ("role_team_size", "role_budget", "role_industry"):
        assert f"{labels[key]}:" not in text, f"label {labels[key]!r} still rendered"
    assert "INDMARK" not in text, "an industry line is still rendered"


@pytest.mark.parametrize("lang", ["de", "en"])
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
def test_every_template_renders_figures_only_without_labels(template, lang):
    html = _flat(_html(template, lang))
    facts = [f.strip() for f in _FACTS_RE.findall(html)]
    assert facts == EXPECTED[lang], f"{template}/{lang}: {facts}"
    _no_labels_no_industry(html, lang)


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_the_delivered_pdf_agrees(template):
    text = _flat(extract_text(await _html_to_pdf(_html(template, "de"))))
    for line in EXPECTED["de"]:
        assert line in text, f"{template}: {line!r} missing from the PDF text"
    _no_labels_no_industry(text, "de")


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_docx_export_agrees_with_the_templates(lang):
    from io import BytesIO

    from docx import Document

    doc = Document(BytesIO(render_cv_docx(_cv(), lang=lang, accent_color="#12233E")))
    paras = [_flat(p.text).strip() for p in doc.paragraphs]
    facts = [p for p in paras if p in EXPECTED[lang]]
    assert facts == EXPECTED[lang], paras
    _no_labels_no_industry("\n".join(paras), lang)


@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
def test_no_template_source_keeps_the_industry_or_a_label(template):
    """Source guard beside the render walk: the old three-append block cannot
    return in one template while the others stay fixed."""
    src = (_TEMPLATES_DIR / CV_TEMPLATES[template]).read_text(encoding="utf-8")
    for token in ("industry_context", "role_industry", "role_team_size", "role_budget"):
        assert token not in src, f"{template}: {token} still referenced"
    assert "| role_facts(lang)" in src
