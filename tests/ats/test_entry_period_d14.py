# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-14 (Hazelnut build 5, delivery run 3) — an ended role must never read "– Present".

Run 3's CV printed "03/2005 – Present, Software Test Engineer, Dade Behring": the
extraction dropped the end date (``end_date None``) and kept ``is_current False``,
and every renderer read a missing end date as "still there". The date range is a
FACT (ADR-062 clause 1) with ONE producer, ``templates.filters.period``, used by all
seven CV templates and the .docx export (the preview is the same HTML as the PDF):

* end date set                      -> "MM/YYYY – MM/YYYY";
* no end date, ``is_current`` True  -> "MM/YYYY – heute/Present";
* no end date, ``is_current`` None  -> "MM/YYYY – heute/Present" (unstated, legacy);
* no end date, ``is_current`` False -> the start date ALONE, no dash, no "Present".

Education carries no ``is_current``; it keeps the unstated reading.

Walks ``_TEMPLATE_FILES`` itself, in de and en, over the rendered HTML, the real
Chromium PDF's extracted text (ADR-039 ``extract_text``) and the .docx, and pins
that ``assemble_tailored_cv`` carries the vault's ``is_current``.
"""
import re
from io import BytesIO
from pathlib import Path

import pytest

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import extract_text
from applire.services.color_detection import _default_context
from applire.services.cv import _TEMPLATE_FILES as CV_TEMPLATES
from applire.services.cv import _html_to_pdf, _jinja_env, assemble_tailored_cv
from applire.services.office_export.cv_docx import render_cv_docx
from applire.templates.labels import cv_labels

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "backend" / "applire" / "templates"

# (company marker, start, end, is_current)
_ROLES = [
    ("ENDEDCO", "2005-03", None, False),   # the D-14 shape
    ("CURRCO", "2019-04", None, True),
    ("LEGACYCO", "2016-02", None, None),
    ("CLOSEDCO", "2010-01", "2015-12", False),
]


def _cv() -> TailoredCVData:
    return TailoredCVData.model_validate(
        {
            "contact": {"name": "Datum Probe", "email": "datum.probe@example.de", "photo_url": None},
            "show_photo": False,
            "work_history": [
                {
                    "company": f"{marker} GmbH",
                    "role": f"Rolle {k}",
                    "start_date": start,
                    "end_date": end,
                    "is_current": cur,
                    "bullets": [f"R{k}BULLET Verantwortung fuer den Bereich."],
                }
                for k, (marker, start, end, cur) in enumerate(_ROLES)
            ],
            "education": [
                {"institution": "EDUOPEN Hochschule", "degree": "M.Sc.", "field": "Informatik",
                 "start_date": "2001-10", "end_date": None},
                {"institution": "EDUDONE Universitaet", "degree": "B.Sc.", "field": "Physik",
                 "start_date": "1997-10", "end_date": "2001-09"},
            ],
            "skills": ["Python"],
        }
    )


def _flat(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("&nbsp;", " ")
    text = re.sub(r"<br\s*/?>", " ", text)
    return re.sub(r"\s+", " ", text)


def _expected(lang: str) -> dict[str, str]:
    present = cv_labels(lang)["present"]
    return {
        "CURRCO": f"04/2019 – {present}",
        "LEGACYCO": f"02/2016 – {present}",
        "CLOSEDCO": "01/2010 – 12/2015",
        "EDUOPEN": f"10/2001 – {present}",
        "EDUDONE": "10/1997 – 09/2001",
    }


def _assert_periods(text: str, lang: str, where: str) -> None:
    present = cv_labels(lang)["present"]
    for marker, period in _expected(lang).items():
        assert period in text, f"{where}: {marker}'s {period!r} missing"
    assert "03/2005" in text, f"{where}: the ended role's start date must still render"
    assert f"03/2005 – {present}" not in text, f"{where}: an ended role reads as current (D-14)"
    assert "03/2005 –" not in text, f"{where}: an ended role with no known end shows a dangling dash"
    # Exactly the two current/unstated work entries + the open education say "Present".
    assert text.count(present) == 3, f"{where}: {text.count(present)} × {present!r}"


def _html(template: str, lang: str) -> str:
    return _jinja_env.get_template(CV_TEMPLATES[template]).render(
        cv=_cv(), color=_default_context(), lang=lang, labels=cv_labels(lang)
    )


@pytest.mark.parametrize("lang", ["de", "en"])
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
def test_every_template_renders_an_ended_role_without_present(template, lang):
    body = _flat(_html(template, lang))
    body = body[body.index("<body"):]  # labels in <style>/<head> are not content
    _assert_periods(re.sub(r"<[^>]+>", "", body), lang, f"{template}/{lang}")


@pytest.mark.asyncio
@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
async def test_the_delivered_pdf_agrees(template):
    text = _flat(extract_text(await _html_to_pdf(_html(template, "en"))))
    _assert_periods(text, "en", f"{template}/pdf")


@pytest.mark.parametrize("lang", ["de", "en"])
def test_the_docx_export_agrees_with_the_templates(lang):
    from docx import Document

    doc = Document(BytesIO(render_cv_docx(_cv(), lang=lang, accent_color="#12233E")))
    text = "\n".join(_flat(p.text).strip() for p in doc.paragraphs)
    _assert_periods(text, lang, f"docx/{lang}")
    assert "03/2005" in text.splitlines()


@pytest.mark.parametrize("template", sorted(CV_TEMPLATES))
def test_no_template_source_keeps_the_end_date_shortcut(template):
    src = (_TEMPLATES_DIR / CV_TEMPLATES[template]).read_text(encoding="utf-8")
    assert "if job.end_date else" not in src and "if edu.end_date else" not in src
    assert "job | period(labels.present)" in src and "edu | period(labels.present)" in src


@pytest.mark.parametrize("cur", [True, False, None])
def test_assembly_carries_the_vaults_is_current(cur):
    profile = {
        "personal_info": {"name": "Datum Probe"},
        "work_experience": [
            {"id": "w1", "company": "Dade Behring", "role": "Software Test Engineer",
             "start_date": "2005-03", "end_date": None, "is_current": cur},
        ],
    }
    out = TailoredCVData.model_validate(
        assemble_tailored_cv({"summary": "", "work": [{"id": "w1", "bullets": ["x"]}]}, profile)
    )
    assert out.work_history[0].is_current is cur
