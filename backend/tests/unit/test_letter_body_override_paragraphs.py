# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RULING B-2 (ADR-090 run, 2026-09-24) — a saved cover-letter body override
keeps its paragraphs.

The body override is the Edit tab's text (``paragraphs.join("\\n\\n")``) and,
since ADR-090, *take it out for me*'s rewritten body (WP-B returns the original
blank-line separators byte for byte). ``_apply_section_overrides`` used to set
``body.paragraphs = [content]``; every letter template renders one ``<p>`` per
paragraph, so the delivered letter collapsed into ONE paragraph on every save.
"""
import re
from pathlib import Path

import pytest

from applire.services.cover_letter import _apply_section_overrides
from applire.templates.filters import build_template_env
from applire.templates.labels import cover_letter_labels

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "applire" / "templates"
LETTER_TEMPLATES = (
    "lebenslauf_letter.html.j2",
    "modern_swiss_letter.html.j2",
    "executive_letter.html.j2",
    "tech_developer_letter.html.j2",
    "creative_sidebar_letter.html.j2",
    "academic_letter.html.j2",
    "compact_pro_letter.html.j2",
)
COLOR = {
    "primary": "#12233E", "primary_tint": "#e8ecf3", "surface": "#12233E",
    "surface_text": "#ffffff", "secondary": "#12233E", "accent": "#12233E", "tint": "#e8ecf3",
}
LETTER = {
    "header": {"name": "Erika Musterfrau", "address": "Musterstraße 1, 10115 Berlin",
               "phone": "+49 30 1234567", "email": "erika@example.com"},
    "recipient": {"name": "Herr Fenrich", "title": "Personalleiter", "company": "Zielfirma GmbH",
                  "address": "Zielstraße 2, 10117 Berlin", "date": "11. September 2026"},
    "body": {"paragraphs": ["Eins.", "Zwei."]},
    "signature": {"closing": "Mit freundlichen Grüßen", "name": "Erika Musterfrau"},
}
THREE = "Erster Absatz mit Einleitung.\n\nZweiter Absatz,\nmit Zeilenumbruch.\n\nDritter Absatz."


def _body_paragraphs(html: str) -> list[str]:
    m = re.search(r'<div class="body">(.*?)</div>', html, re.S)
    assert m, "letter template has no body div"
    return re.findall(r"<p>(.*?)</p>", m.group(1), re.S)


def test_override_splits_on_blank_lines():
    data = _apply_section_overrides(LETTER, {"body": THREE})
    assert data["body"]["paragraphs"] == [
        "Erster Absatz mit Einleitung.",
        "Zweiter Absatz,\nmit Zeilenumbruch.",
        "Dritter Absatz.",
    ]


def test_whitespace_only_separator_lines_and_runs_still_split():
    data = _apply_section_overrides(LETTER, {"body": "A.\n  \nB.\n\n\n\nC.\n"})
    assert data["body"]["paragraphs"] == ["A.", "B.", "C."]


def test_single_paragraph_and_empty_override_keep_their_shape():
    assert _apply_section_overrides(LETTER, {"body": "Nur einer."})["body"]["paragraphs"] == ["Nur einer."]
    assert _apply_section_overrides(LETTER, {"body": ""})["body"]["paragraphs"] == [""]


def test_join_then_override_round_trips_the_paragraphs():
    """What the Edit tab (and take-out) send back is `paragraphs.join("\\n\\n")`."""
    original = ["Eins.", "Zwei, mit Komma.", "Drei."]
    data = _apply_section_overrides(LETTER, {"body": "\n\n".join(original)})
    assert data["body"]["paragraphs"] == original


@pytest.mark.parametrize("name", LETTER_TEMPLATES)
def test_multi_paragraph_override_renders_one_p_per_paragraph(name):
    env = build_template_env(TEMPLATES_DIR)
    html = env.get_template(name).render(
        letter=_apply_section_overrides(LETTER, {"body": THREE}),
        color=COLOR, lang="de", labels=cover_letter_labels("de"),
        subject="Bewerbung als Senior Developerin",
    )
    paras = _body_paragraphs(html)
    assert len(paras) == 3, paras
    assert paras[0].startswith("Erster Absatz") and paras[2] == "Dritter Absatz."
