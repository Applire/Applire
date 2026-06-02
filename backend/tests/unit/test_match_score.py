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
