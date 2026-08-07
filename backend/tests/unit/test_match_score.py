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

from applire.services.match_score import compute_match_score


def _cls(req, status):
    return {"requirement": req, "status": status, "reason": ""}


def test_all_direct_required_scores_1():
    req = ["A", "B", "C"]
    out = compute_match_score([_cls(r, "direct") for r in req], req, [])
    assert out["match_score"] == 1.0
    assert set(out["category_a"]) == {"A", "B", "C"}
    assert out["critical_gaps"] == []


def test_all_gap_scores_0():
    req = ["A", "B"]
    out = compute_match_score([_cls(r, "gap") for r in req], req, [])
    assert out["match_score"] == 0.0
    assert set(out["critical_gaps"]) == {"A", "B"}


def test_worked_example_12_direct_4_partial_7_gap():
    direct = [f"d{i}" for i in range(12)]
    partial = [f"p{i}" for i in range(4)]
    gap = [f"g{i}" for i in range(7)]
    required = direct + partial + gap
    classifications = (
        [_cls(r, "direct") for r in direct]
        + [_cls(r, "partial") for r in partial]
        + [_cls(r, "gap") for r in gap]
    )
    out = compute_match_score(classifications, required, [])
    assert round(out["match_score"], 4) == round(14 / 23, 4)


def test_nice_to_have_half_slot_weight():
    out = compute_match_score(
        [_cls("R", "direct"), _cls("N", "direct")], ["R"], ["N"]
    )
    assert out["match_score"] == 1.0


def test_nice_to_have_partial_quarter_credit():
    out = compute_match_score(
        [_cls("R", "direct"), _cls("N", "partial")], ["R"], ["N"]
    )
    assert round(out["match_score"], 4) == round(1.25 / 1.5, 4)
    assert "N" in out["minor_gaps"]


def test_unclassified_required_defaults_to_gap():
    out = compute_match_score([_cls("A", "direct")], ["A", "B"], [])
    assert "B" in out["category_c"]
    assert "B" in out["critical_gaps"]
    assert round(out["match_score"], 4) == round(1 / 2, 4)


def test_unmatched_llm_item_is_dropped():
    out = compute_match_score(
        [_cls("A", "direct"), _cls("Z", "direct")], ["A"], []
    )
    assert out["match_score"] == 1.0
    assert all(b["requirement"] != "Z" for b in out["requirement_breakdown"])


def test_empty_requirements_yields_none():
    out = compute_match_score([], [], [])
    assert out["match_score"] is None


def test_case_insensitive_requirement_match():
    out = compute_match_score([_cls("python", "direct")], ["Python"], [])
    assert out["match_score"] == 1.0
    assert out["category_a"] == ["Python"]


def test_breakdown_carries_source_status_slot_earned():
    out = compute_match_score([_cls("R", "partial")], ["R"], [])
    entry = out["requirement_breakdown"][0]
    assert entry["requirement"] == "R"
    assert entry["source"] == "required"
    assert entry["status"] == "partial"
    assert entry["slot"] == 1.0
    assert entry["earned"] == 0.5


# --- #383: `denied` is not `gap` at the categorisation seam ------------------
#
# ADR-059 (amended 2026-07-27) made `denied` a first-class requirement status
# precisely so that "the candidate told us no" stops being the same value as
# "we have no signal". Every consumer must honour it. This seam did not: the
# categorisation branch's trailing `else: # gap` was a catch-all, so a denied
# required concept landed in `critical_gaps` — the list the writer prompt used
# to render as "CRITICAL GAPS (acknowledge in summary if applicable)", directly
# against the same prompt's rule 3a ("EXPLICITLY DENIED … simply OMIT it").
# Concepts below are the verbatim denials from charter run #7 (ADR-059's
# 2026-07-27 amendment).

_DENIED_RUN7 = ["BaFin supervision", "PSD2/PSP integration"]


def test_denied_required_concept_never_enters_critical_gaps():
    required = _DENIED_RUN7 + ["Python"]
    out = compute_match_score(
        [_cls(c, "denied") for c in _DENIED_RUN7] + [_cls("Python", "direct")],
        required,
        [],
    )
    assert out["critical_gaps"] == []


def test_denied_required_concept_is_not_a_category_c_gap():
    required = _DENIED_RUN7 + ["Python"]
    out = compute_match_score(
        [_cls(c, "denied") for c in _DENIED_RUN7] + [_cls("Python", "direct")],
        required,
        [],
    )
    assert out["category_c"] == []
    assert out["category_a"] == ["Python"]
    assert out["category_b"] == []


def test_denied_nice_to_have_never_enters_minor_gaps():
    out = compute_match_score(
        [_cls("Python", "direct"), _cls("BaFin supervision", "denied")],
        ["Python"],
        ["BaFin supervision"],
    )
    assert out["minor_gaps"] == []


def test_denied_still_earns_zero_and_keeps_its_slot():
    """The score is unchanged by #383 — only the categorisation moves."""
    required = _DENIED_RUN7 + ["Python"]
    out = compute_match_score(
        [_cls(c, "denied") for c in _DENIED_RUN7] + [_cls("Python", "direct")],
        required,
        [],
    )
    assert round(out["match_score"], 4) == round(1 / 3, 4)


def test_denied_is_labelled_denied_in_the_breakdown():
    out = compute_match_score(
        [_cls("BaFin supervision", "denied")], ["BaFin supervision"], []
    )
    entry = out["requirement_breakdown"][0]
    assert entry["requirement"] == "BaFin supervision"
    assert entry["status"] == "denied"
    assert entry["earned"] == 0.0


def test_substring_requirements_no_classification_bleed():
    out = compute_match_score(
        [_cls("React", "direct"), _cls("React Native", "partial")],
        ["React", "React Native"],
        [],
    )
    bd = {b["requirement"]: b["status"] for b in out["requirement_breakdown"]}
    assert bd["React"] == "direct"
    assert bd["React Native"] == "partial"
