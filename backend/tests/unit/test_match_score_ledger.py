# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Unit tests for ledger-sourced match scoring (ADR-048 §5, E037/US199).

The match score is re-sourced from the Keyword Ledger's fit-weighted slice — the
single source of truth — rather than from a parallel classification list. The
ADR-035 formula and weights are unchanged; only the input source moves. These
tests pin that the ledger-sourced score equals the classifications-sourced one.
"""

from applire.services.keyword_ledger import build_keyword_ledger
from applire.services.match_score import (
    compute_match_score,
    compute_match_score_from_ledger,
)


def _cls(req, status, surface_forms=None):
    item = {"requirement": req, "status": status, "reason": ""}
    if surface_forms is not None:
        item["surface_forms"] = surface_forms
    return item


def _to_ledger_input(classifications):
    """Adapt compute_match_score classification dicts to ledger input shape."""
    return [
        {
            "concept": c.get("requirement", ""),
            "status": c.get("status", "gap"),
            "evidence": c.get("reason", ""),
            "surface_forms": c.get("surface_forms"),
        }
        for c in classifications
    ]


def _mock_inputs():
    """The MockLLMProvider gap + job analysis, in compute_match_score shape."""
    from applire.providers.llm.mock import (
        _GAP_ANALYSIS_RESPONSE,
        _JOB_ANALYSIS_RESPONSE,
    )

    classifications = _GAP_ANALYSIS_RESPONSE["classifications"]
    required = _JOB_ANALYSIS_RESPONSE["required_skills"]
    nice = _JOB_ANALYSIS_RESPONSE["nice_to_have_skills"]
    keywords = _JOB_ANALYSIS_RESPONSE["keywords"]
    return classifications, required, nice, keywords


# --- PARITY: the key invariant ----------------------------------------------


def test_parity_with_mock_data():
    classifications, required, nice, keywords = _mock_inputs()

    direct = compute_match_score(classifications, required, nice)
    ledger = build_keyword_ledger(
        _to_ledger_input(classifications), required, nice, keywords
    )
    via_ledger = compute_match_score_from_ledger(ledger)

    assert via_ledger["match_score"] == direct["match_score"]
    assert set(via_ledger["category_a"]) == set(direct["category_a"])
    assert set(via_ledger["category_b"]) == set(direct["category_b"])
    assert set(via_ledger["category_c"]) == set(direct["category_c"])
    assert set(via_ledger["critical_gaps"]) == set(direct["critical_gaps"])
    assert set(via_ledger["minor_gaps"]) == set(direct["minor_gaps"])


def test_parity_with_hand_built_mixed_case():
    required = ["Python", "Docker", "AWS"]
    nice = ["Kubernetes", "Terraform"]
    keywords = ["agile"]
    classifications = [
        _cls("Python", "direct"),
        _cls("Docker", "partial"),
        _cls("AWS", "gap"),
        _cls("Kubernetes", "direct"),
        _cls("Terraform", "gap"),
        _cls("agile", "direct"),
    ]

    direct = compute_match_score(classifications, required, nice)
    ledger = build_keyword_ledger(
        _to_ledger_input(classifications), required, nice, keywords
    )
    via_ledger = compute_match_score_from_ledger(ledger)

    assert via_ledger["match_score"] == direct["match_score"]
    assert set(via_ledger["category_a"]) == set(direct["category_a"])
    assert set(via_ledger["category_b"]) == set(direct["category_b"])
    assert set(via_ledger["category_c"]) == set(direct["category_c"])
    assert set(via_ledger["critical_gaps"]) == set(direct["critical_gaps"])
    assert set(via_ledger["minor_gaps"]) == set(direct["minor_gaps"])


# --- Behaviour pinned directly on the ledger source -------------------------


def test_keyword_only_entries_do_not_affect_score():
    # One required (direct) + one keyword-only (whatever its status) → 1.0,
    # because the keyword entry has fit_weight 0 and is excluded from the slice.
    ledger = build_keyword_ledger(
        _to_ledger_input([_cls("Python", "direct"), _cls("agile", "gap")]),
        ["Python"],
        [],
        ["agile"],
    )
    out = compute_match_score_from_ledger(ledger)
    assert out["match_score"] == 1.0
    assert "agile" not in out["category_a"]
    assert "agile" not in out["category_c"]


def test_all_direct_required_scores_1():
    ledger = build_keyword_ledger(
        _to_ledger_input([_cls(r, "direct") for r in ["A", "B", "C"]]),
        ["A", "B", "C"],
        [],
        [],
    )
    out = compute_match_score_from_ledger(ledger)
    assert out["match_score"] == 1.0
    assert set(out["category_a"]) == {"A", "B", "C"}
    assert out["critical_gaps"] == []


def test_all_gap_scores_0():
    ledger = build_keyword_ledger(
        _to_ledger_input([_cls(r, "gap") for r in ["A", "B"]]),
        ["A", "B"],
        [],
        [],
    )
    out = compute_match_score_from_ledger(ledger)
    assert out["match_score"] == 0.0
    assert set(out["critical_gaps"]) == {"A", "B"}


def test_empty_ledger_yields_none():
    out = compute_match_score_from_ledger([])
    assert out["match_score"] is None


def test_ledger_with_only_keyword_entries_yields_none():
    ledger = build_keyword_ledger(
        _to_ledger_input([_cls("agile", "direct")]),
        [],
        [],
        ["agile"],
    )
    out = compute_match_score_from_ledger(ledger)
    assert out["match_score"] is None


def test_nice_to_have_partial_quarter_credit():
    ledger = build_keyword_ledger(
        _to_ledger_input([_cls("R", "direct"), _cls("N", "partial")]),
        ["R"],
        ["N"],
        [],
    )
    out = compute_match_score_from_ledger(ledger)
    assert round(out["match_score"], 4) == round(1.25 / 1.5, 4)
    assert "N" in out["minor_gaps"]
