# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""RULING L-1 = A/B (founder 2026-10-24, ADR-075 amended), carried by G-1 (2026-11-04): a
letter names a recorded gap only where the posting's KEY requirement is that gap — never a
nice-to-have (IFS/BRC "von Vorteil", fit_weight 0.5) or a keyword-only concept (0.0).

Evidence (build-3 delivery log 2026-10-22, letter writer recs 77 Marcus / 172 Daniel): both
reached the REQUIRED: STATED LIMITS block, so every letter carried the admission. The
selection is a FACT, so the key-requirement cut is code — since G-1 it lives in
`key_requirement_gaps` (the old `key_requirement_ledger` / `build_stated_limits_entry` /
`render_required_limits_block` are deleted; their successors are `build_gap_positioning_entry`
and `render_gap_ladder_block`). The wording stays the writer's.
"""
from __future__ import annotations

from applire.prompts.cover_letter import SYSTEM_PROMPT
from applire.services.cover_letter import build_gap_positioning_entry
from applire.services.cross_document import (
    gaps_to_name,
    key_requirement_gaps,
    letter_constraint_only_limits,
    render_gap_ladder_block,
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
    {"concept": "IFS", "status": "denied", "claimable": False, "fit_weight": 0.5,
     "sources": ["nice_to_have"]},
    {"concept": "Führungsspanne 120", "status": "denied", "claimable": False,
     "fit_weight": 1.0, "sources": ["required"]},
    {"concept": "ISO 9001", "status": "direct", "claimable": True, "fit_weight": 1.0,
     "sources": ["required"]},
]
JD = "Führungsspanne 120 und ISO 9001; IFS ist von Vorteil."


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


def test_a_nice_to_have_denial_is_never_named_by_the_letter():
    gaps = key_requirement_gaps(LEDGER, DENIED, JD)
    assert [g.label for g in gaps] == ["Führungsspanne 120"]
    named = gaps_to_name(gaps)
    assert [g.statement for g in named] == [SPAN]
    # ... and it still reaches the writer, as a never-disclosed constraint
    assert letter_constraint_only_limits(DENIED, named) == [IFS]


def test_the_positioning_entry_carries_only_key_requirement_gaps():
    gaps = key_requirement_gaps(LEDGER, DENIED, JD)
    entry = build_gap_positioning_entry(gaps, gaps_to_name(gaps), "de")
    assert entry is not None and entry["required"] is True
    assert entry["named"] == [{"gap": "Führungsspanne 120", "statement": SPAN, "recorded": True}]
    only_nice = key_requirement_gaps(LEDGER[:1], DENIED[:1], JD)
    assert only_nice == []
    assert build_gap_positioning_entry(only_nice, gaps_to_name(only_nice), "de") is None


def test_non_key_rows_and_unweighted_denials_are_no_gap():
    rows = LEDGER + [{"concept": "X", "status": "denied", "claimable": False}]
    assert [g.label for g in key_requirement_gaps(rows, DENIED, JD)] == ["Führungsspanne 120"]
    assert key_requirement_gaps(None, DENIED, JD) == []


def test_every_door_says_a_gap_is_named_with_transfer_or_left_out():
    gaps = key_requirement_gaps(LEDGER, DENIED, JD)
    named = gaps_to_name(gaps)
    assert "in the same sentence" in _flat(render_gap_ladder_block(gaps, named, "de"))
    flat_writer = _flat(SYSTEM_PROMPT)
    assert "else the letter leaves it out — never stated as a lack" in flat_writer
    assert "the einarbeiten clause" in _flat(build_gap_positioning_entry(gaps, named, "de")["instruction"])


def test_the_ledger_denied_list_no_longer_licenses_an_unowed_admission():
    block = _flat(render_ledger_prompt_block([{"concept": "IFS", "status": "denied"}]))
    assert "only where its key-requirement gaps block names it" in block
