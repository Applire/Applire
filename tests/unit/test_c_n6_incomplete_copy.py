# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Holds re-probe N-6: a legacy ``terminal-review-incomplete`` row from the old H-1
mapping (a blocking verdict DID read the shipped draft; the corrector failed) was
served with "No review read the delivered version." — false for that row. The line now
says what is true of every incomplete row: the final review did not finish on it."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from applire.services import review_signals

ROOT = Path(__file__).resolve().parents[2]
LEGACY_H1 = {  # U3 in the re-probe: status fail, a blocking note, no hash
    "id": "terminal-review", "status": "fail", "details": "x",
    "details_key": "terminal-review-incomplete", "details_params": {"count": 1},
    "notes": [{"kind": "finding", "text": "Bullet 'Led a team of 40' is not backed.", "plain": None}],
    "delivery_class": "A", "resolver": "decide_open",
}
H2 = {  # today's shape: no verdict read the delivered draft
    "id": "terminal-review", "status": "not_applicable", "details": "x",
    "details_key": "terminal-review-incomplete", "details_params": {}, "notes": [],
    "delivery_class": "U", "resolver": "recheck",
}


@pytest.mark.parametrize("row", [LEGACY_H1, H2], ids=["legacy-h1", "h2"])
@pytest.mark.parametrize("kind", ["cv", "cover_letter"])
def test_n6_the_incomplete_line_is_true_for_every_incomplete_row(row, kind):
    rec = SimpleNamespace(ats_report={"checks": [dict(row)]}, review_state=None, origin="pipeline",
                          truthfulness_report=None, audit_state=None)
    if kind == "cover_letter":
        rec.letter_data = {}
    check = review_signals.open_points_check(rec)
    line = [n for n in check["notes"] if n.get("key") == "review.incomplete"]
    assert [n["text"] for n in line] == ["The final review did not finish on the delivered version."]


def test_n6_the_card_copy_says_the_same_in_both_languages():
    for lang, text in (
        ("en", "The final review did not finish on the delivered version."),
        ("de", "Die Schlussprüfung wurde für die ausgelieferte Fassung nicht abgeschlossen."),
    ):
        catalog = json.loads((ROOT / "frontend" / "messages" / f"{lang}.json").read_text(encoding="utf-8"))
        found = []

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "review-incomplete" and isinstance(v, str):
                        found.append(v)
                    walk(v)

        walk(catalog)
        assert found == [text], (lang, found)
