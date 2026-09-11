# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""One response may not publish a concept as a STRENGTH and a CRITICAL GAP
at once (E-4 / SF-GAP.10, 2026-09-11).

`critical_gaps` / `category_*` are re-sourced from the keyword ledger — i.e.
from after the ADR-040 denial floor, the ADR-069 scope judgement and #318's
claimable-backing heal. `strengths` was the one published list still taken
verbatim from the model, so every deterministic downgrade split the response
in two. The 2026-09-10 delivery run
(`Documents/Runs/Nougat/build-1/delivery-run/artifacts/20-gaps-refresh.json`)
shipped `Produktion` and `Durchsetzungsstärke` in both lists; the 2026-09-09
pre-interview run (`08-gaps.json`) shipped `Führungserfahrung` in both, from
the model's own self-contradiction with no denial involved. The gaps screen
renders both lists side by side.
"""

from applire.services.gap import _strengths_the_ledger_supports


def _row(concept, claimable, surface_forms=None):
    return {
        "concept": concept,
        "surface_forms": surface_forms or [concept],
        "claimable": claimable,
        "status": "direct" if claimable else "gap",
    }


def test_a_floored_concept_is_dropped_from_strengths():
    """The E-4 instance: the denial floor downgraded `Produktion`, so the
    response may not still call it a strength."""
    out = _strengths_the_ledger_supports(
        ["Instandhaltung", "Produktion", "KVP"],
        [_row("Instandhaltung", True), _row("Produktion", False), _row("KVP", True)],
    )
    assert out == ["Instandhaltung", "KVP"]


def test_the_model_self_contradiction_is_dropped_too():
    """`Führungserfahrung` (08-gaps.json): classified `gap` by the model and
    listed under `strengths` by the same response, with no denial in play."""
    out = _strengths_the_ledger_supports(
        ["Führungserfahrung"], [_row("Führungserfahrung", False)]
    )
    assert out == []


def test_a_strength_matching_no_ledger_row_survives():
    """No fact contradicts it — the pass removes overclaims, it does not
    curate the model's list (ADR-062 clause 1: facts, never judgements)."""
    out = _strengths_the_ledger_supports(
        ["Hands-on-Mentalität"], [_row("Instandhaltung", True)]
    )
    assert out == ["Hands-on-Mentalität"]


def test_a_surface_form_match_counts_and_any_claimable_row_keeps_it():
    """Matching is over concept AND surface forms; when two rows claim the same
    name and one of them is claimable, the strength stands."""
    out = _strengths_the_ledger_supports(
        ["MES", "SAP"],
        [
            _row("MES-Systeme", False, ["MES-Systeme", "MES"]),
            _row("MES", True),
            _row("SAP", False),
        ],
    )
    assert out == ["MES"]


def test_matching_is_case_and_whitespace_insensitive():
    out = _strengths_the_ledger_supports(
        ["  produktion "], [_row("Produktion", False)]
    )
    assert out == []


def test_empty_and_malformed_inputs_are_tolerated():
    assert _strengths_the_ledger_supports([], None) == []
    assert _strengths_the_ledger_supports(None, []) == []
    assert _strengths_the_ledger_supports(["X"], [None, "junk", {}]) == ["X"]
