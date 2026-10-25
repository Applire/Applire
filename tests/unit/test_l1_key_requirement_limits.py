# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""RULING L-1 = A (founder 2026-10-24, ADR-075 amended): a letter names a recorded
gap only where the posting's KEY requirement is that gap and a transfer argument
exists — gap and transfer in one sentence; otherwise the gap is left out, never a
bare admission.

Evidence (build-3 delivery log 2026-10-22, letter writer recs 77 Marcus / 172
Daniel): IFS/BRC is "von Vorteil" (nice_to_have, fit_weight 0.5) and "Payment
institution" a keyword-only concept (0.0), yet both reached the REQUIRED: STATED
LIMITS block, so every letter carried the admission. The selection is a FACT, so
the key-requirement cut is code (`key_requirement_ledger`); the wording stays the
writer's.
"""
from __future__ import annotations

from applire.prompts.cover_letter import SYSTEM_PROMPT
from applire.services.cover_letter import build_stated_limits_entry, key_requirement_ledger
from applire.services.cross_document import (
    letter_constraint_only_limits,
    render_required_limits_block,
    select_jd_relevant_limits,
)
from applire.services.keyword_ledger import render_ledger_prompt_block

IFS = ("Nein, mit IFS oder BRC habe ich keine Erfahrung. Was ich anbieten kann: "
       "zehn Jahre ISO-9001-Audit-Praxis.")
SPAN = "Direkt geführt habe ich 38 Mitarbeitende; die Spanne von 120 wäre neu."
DENIED = [
    {"concept": "IFS", "statement": IFS},
    {"concept": "Führungsspanne 120", "statement": SPAN},
]
LEDGER = [
    {"concept": "IFS", "status": "denied", "fit_weight": 0.5},  # nice_to_have
    {"concept": "Führungsspanne 120", "status": "denied", "fit_weight": 1.0},  # required
    {"concept": "ISO 9001", "status": "direct", "fit_weight": 1.0},
]


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


def test_a_nice_to_have_denial_is_never_owed_by_the_letter():
    owed = select_jd_relevant_limits(DENIED, key_requirement_ledger(LEDGER))
    assert owed == [SPAN]
    # ... and it still reaches the writer, as a never-disclosed constraint
    assert letter_constraint_only_limits(DENIED, key_requirement_ledger(LEDGER)) == [IFS]


def test_the_positioning_entry_carries_only_key_requirement_limits():
    entry = build_stated_limits_entry(DENIED, LEDGER)
    assert entry is not None and entry["limits"] == [SPAN]
    assert build_stated_limits_entry(DENIED[:1], LEDGER[:1]) is None


def test_non_denied_rows_and_unweighted_denials_are_handled():
    rows = key_requirement_ledger(LEDGER + [{"concept": "X", "status": "denied"}])
    assert {r["concept"] for r in rows} == {"Führungsspanne 120", "ISO 9001"}
    assert key_requirement_ledger(None) == []


def test_every_door_says_a_limit_without_transfer_is_left_out():
    assert "leaves the limit out" in _flat(render_required_limits_block([SPAN]))
    assert "leaves it out — never a bare admission" in _flat(SYSTEM_PROMPT)
    assert "a bare admission is never the decision" in _flat(
        build_stated_limits_entry(DENIED, LEDGER)["instruction"])


def test_the_ledger_denied_list_no_longer_licenses_an_unowed_admission():
    block = _flat(render_ledger_prompt_block([{"concept": "IFS", "status": "denied"}]))
    assert "only where a required: stated limits block lists it" in block
