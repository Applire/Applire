# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial pass, Hazelnut build 7 — 790:90 settle guard is positional.

`_settle_language_shape` keeps a skills list whose LENGTH is unchanged, so a
language pass that reorders the chips reaches `_settle_posting_term_chips`.
The guard compares handed[i] with settled[i] by POSITION and overwrites
settled[i] with the posting term — a moved non-posting chip is then lost and
the posting term appears twice. Before the guard, the reordered list survived
intact. Synthetic data.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services import cv as cv_mod  # noqa: E402

HANDED = {
    "summary": "x",
    "work": [{"id": "w1", "bullets": ["b"], "projects": []}],
    "skills": ["Supply Chain", "SAP S/4HANA", "Excel"],
}


def test_reordered_chips_keep_every_handed_chip():
    settled = {**HANDED, "skills": ["SAP S/4HANA", "Supply Chain", "Excel"]}
    out = cv_mod._settle_after_language_pass(settled, HANDED, None, ("Supply Chain",))
    # every chip the pass was handed must still be on the CV, none duplicated
    assert sorted(out["skills"]) == sorted(HANDED["skills"]), out["skills"]


def test_reorder_without_posting_term_loss_changes_nothing():
    settled = {**HANDED, "skills": ["Excel", "SAP S/4HANA", "Supply Chain"]}
    out = cv_mod._settle_posting_term_chips(settled, HANDED, ("Supply Chain",))
    assert out["skills"] == ["Excel", "SAP S/4HANA", "Supply Chain"]


def test_no_reorder_translated_posting_term_is_still_restored():
    settled = {**HANDED, "skills": ["Lieferkette", "SAP S/4HANA", "Excel"]}
    out = cv_mod._settle_posting_term_chips(settled, HANDED, ("Supply Chain",))
    assert out["skills"] == ["Supply Chain", "SAP S/4HANA", "Excel"]


def test_reordered_and_translated_posting_term_is_restored_in_the_translated_slot():
    settled = {**HANDED, "skills": ["SAP S/4HANA", "Lieferkette", "Excel"]}
    out = cv_mod._settle_posting_term_chips(settled, HANDED, ("Supply Chain",))
    assert sorted(out["skills"]) == sorted(HANDED["skills"]), out["skills"]
