# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-090 clause 6 — review decisions persisted per generated document.

Covers ``finding_key``/``split_key``, ``group_one_findings`` (the backend's
mirror of the frontend's Group 1), ``find_listed``, the decision-state writers
(``with_decision``/``without_decision``/``load_state``), and the load-bearing
live derivation ``derive_review`` (SF-REVIEW.9): a decision never hides a
finding the current report still lists.
"""
from __future__ import annotations

import asyncio

import pytest

from applire.services.review_state import (
    derive_review,
    document_lock,
    find_listed,
    finding_key,
    get_decision,
    group_one_findings,
    load_state,
    norm_quote,
    split_key,
    with_decision,
    with_walked,
    without_decision,
)

# ---------------------------------------------------------------------------
# finding_key / split_key
# ---------------------------------------------------------------------------


def test_finding_key_refolds_and_splits():
    key = finding_key("ats", "Code   Review")
    assert key == f"ats:{norm_quote('Code   Review')}"
    producer, text_norm = split_key(key)
    assert producer == "ats"
    assert text_norm == norm_quote("Code   Review")


def test_split_key_refolds_an_unnormalised_key():
    """A key sent with un-normalised text still resolves — split_key re-folds
    the text half rather than trusting the caller's own normalisation."""
    producer, text_norm = split_key("oracle:  Docker  ")
    assert producer == "oracle"
    assert text_norm == norm_quote("Docker")


@pytest.mark.parametrize(
    "bad_key",
    [
        "no-colon-here",
        "",
        "foo:bar",  # unknown producer
        "ats:",  # empty text after normalisation
        "ats:   ",  # whitespace-only text
        123,  # not even a string
        None,
    ],
)
def test_split_key_raises_on_malformed_key(bad_key):
    with pytest.raises(ValueError):
        split_key(bad_key)


def test_finding_key_rejects_unknown_producer():
    with pytest.raises(ValueError):
        finding_key("frontend", "Docker")


@pytest.mark.parametrize("producer", ["ats", "oracle"])
def test_finding_key_accepts_the_two_valid_producers(producer):
    key = finding_key(producer, "Docker")
    assert key.startswith(f"{producer}:")


# ---------------------------------------------------------------------------
# group_one_findings
# ---------------------------------------------------------------------------


def test_ats_terms_come_first():
    ats_report = {"keywords": {
        "present_unsupported": ["Docker", "Kubernetes"],
        "present_unsupported_matches": {
            "Docker": [{"form": "Docker", "stem": False}],
            "Kubernetes": [{"form": "Kubernetes", "stem": False}],
        },
        "claimable_concepts": [],
    }}
    truth_report = {"claims": [
        {"claim": {"text": "Led a team of 12", "kind": "text", "location": "summary"},
         "verdict": {"verdict": "inflated"}},
    ]}
    rows = group_one_findings(ats_report, truth_report)
    assert [r.key for r in rows[:2]] == ["ats:docker", "ats:kubernetes"]
    assert rows[2].producer == "oracle"


def test_oracle_claim_folds_into_the_ats_term_it_normalises_equal_to():
    ats_report = {"keywords": {
        "present_unsupported": ["Docker"],
        "present_unsupported_matches": {"Docker": [{"form": "Docker", "stem": False}]},
        "claimable_concepts": [],
    }}
    truth_report = {"claims": [
        {"claim": {"text": "Docker", "kind": "skill", "location": "skills"},
         "verdict": {"verdict": "unbacked"}},
    ]}
    rows = group_one_findings(ats_report, truth_report)
    assert len(rows) == 1
    row = rows[0]
    assert row.key == "ats:docker"
    assert row.producer == "ats"
    assert row.claim_text == "Docker"
    assert row.claim_location == "skills"
    assert row.matches == [{"form": "Docker", "stem": False}]


def test_nonmatching_flagged_claim_gets_its_own_oracle_row():
    ats_report = {"keywords": {
        "present_unsupported": ["Docker"],
        "present_unsupported_matches": {"Docker": [{"form": "Docker", "stem": False}]},
        "claimable_concepts": [],
    }}
    truth_report = {"claims": [
        {"claim": {"text": "Led a team of 12 engineers", "kind": "text", "location": "summary"},
         "verdict": {"verdict": "misattributed"}},
    ]}
    rows = group_one_findings(ats_report, truth_report)
    assert len(rows) == 2
    ats_row, oracle_row = rows
    assert ats_row.key == "ats:docker"
    assert oracle_row.producer == "oracle"
    assert oracle_row.key == f"oracle:{norm_quote('Led a team of 12 engineers')}"
    assert oracle_row.claim_text == "Led a team of 12 engineers"


def test_related_skill_claim_is_excluded():
    """unbacked + kind skill + text in claimable_concepts (case-insensitive) is
    a 'related' claim — never a row of its own, and it must not consume the
    ATS term (there is none here for it to fold into)."""
    ats_report = {"keywords": {
        "present_unsupported": [],
        "present_unsupported_matches": {},
        "claimable_concepts": ["Python"],
    }}
    truth_report = {"claims": [
        {"claim": {"text": "PYTHON", "kind": "skill", "location": "skills"},
         "verdict": {"verdict": "unbacked"}},
    ]}
    rows = group_one_findings(ats_report, truth_report)
    assert rows == []


def test_verdict_supported_is_excluded():
    ats_report = {"keywords": {"present_unsupported": [], "claimable_concepts": []}}
    truth_report = {"claims": [
        {"claim": {"text": "Led a team", "kind": "text", "location": "summary"},
         "verdict": {"verdict": "supported"}},
    ]}
    assert group_one_findings(ats_report, truth_report) == []


def test_matches_copied_verbatim_from_present_unsupported_matches():
    ats_report = {"keywords": {
        "present_unsupported": ["Docker"],
        "present_unsupported_matches": {"Docker": [
            {"form": "Docker", "stem": False}, {"form": "containerisation", "stem": True},
        ]},
        "claimable_concepts": [],
    }}
    rows = group_one_findings(ats_report, None)
    assert rows[0].matches == [
        {"form": "Docker", "stem": False}, {"form": "containerisation", "stem": True},
    ]


def test_wording_returns_matched_forms_then_claim_text_deduped():
    ats_report = {"keywords": {
        "present_unsupported": ["Docker"],
        "present_unsupported_matches": {"Docker": [
            {"form": "Docker", "stem": False}, {"form": "container platform", "stem": False},
        ]},
        "claimable_concepts": [],
    }}
    truth_report = {"claims": [
        {"claim": {"text": "Docker", "kind": "skill", "location": "skills"},
         "verdict": {"verdict": "unbacked"}},
    ]}
    rows = group_one_findings(ats_report, truth_report)
    row = rows[0]
    # claim text "Docker" normalises equal to the first matched form → deduped.
    assert row.wording() == ["Docker", "container platform"]


def test_wording_falls_back_to_the_term_for_a_legacy_report_without_matches():
    """A report predating ADR-090 clause 2 has no matches field at all (`None`)
    — wording() must fall back to the term itself, never crash or go empty."""
    ats_report = {"keywords": {
        "present_unsupported": ["Docker"],
        "present_unsupported_matches": None,
        "claimable_concepts": [],
    }}
    rows = group_one_findings(ats_report, None)
    row = rows[0]
    assert row.matches is None
    assert row.wording() == ["Docker"]


def test_wording_oracle_only_row_returns_its_claim_text():
    ats_report = {"keywords": {"present_unsupported": [], "claimable_concepts": []}}
    truth_report = {"claims": [
        {"claim": {"text": "Led a team of 12", "kind": "text", "location": "summary"},
         "verdict": {"verdict": "inflated"}},
    ]}
    row = group_one_findings(ats_report, truth_report)[0]
    assert row.wording() == ["Led a team of 12"]


# ---------------------------------------------------------------------------
# find_listed
# ---------------------------------------------------------------------------


def test_find_listed_accepts_either_producer_prefix_for_a_merged_row():
    ats_report = {"keywords": {
        "present_unsupported": ["Docker"],
        "present_unsupported_matches": {"Docker": [{"form": "Docker", "stem": False}]},
        "claimable_concepts": [],
    }}
    truth_report = {"claims": [
        {"claim": {"text": "Docker", "kind": "skill", "location": "skills"},
         "verdict": {"verdict": "unbacked"}},
    ]}
    findings = group_one_findings(ats_report, truth_report)
    assert findings[0].key == "ats:docker"
    by_ats = find_listed(findings, "ats:docker")
    by_oracle = find_listed(findings, "oracle:docker")
    assert by_ats is findings[0]
    assert by_oracle is findings[0]


def test_find_listed_returns_none_when_not_listed():
    findings = group_one_findings({"keywords": {"present_unsupported": [], "claimable_concepts": []}}, None)
    assert find_listed(findings, "ats:docker") is None


# ---------------------------------------------------------------------------
# with_decision / without_decision / load_state
# ---------------------------------------------------------------------------


def test_with_decision_replaces_an_earlier_decision_on_the_same_finding():
    state = load_state(None)
    state = with_decision(state, "ats:docker", label="Docker", action="taken_out", at="2026-09-01T00:00:00+00:00")
    state = with_decision(state, "ats:docker", label="Docker", action="edited", at="2026-09-02T00:00:00+00:00")
    assert len(state["decisions"]) == 1
    d = state["decisions"][0]
    assert d["action"] == "edited"
    assert d["at"] == "2026-09-02T00:00:00+00:00"


def test_with_decision_replaces_by_normalised_text_not_raw_key():
    """The same finding addressed with un-normalised text is still 'the same
    finding' — replace, don't append a second decision."""
    state = load_state(None)
    state = with_decision(state, "ats:Docker", label="Docker", action="taken_out")
    state = with_decision(state, "ats:  docker  ", label="Docker", action="edited")
    assert len(state["decisions"]) == 1
    assert state["decisions"][0]["action"] == "edited"


def test_with_decision_undo_shape_exact():
    state = with_decision(
        load_state(None), "ats:docker", label="Docker", action="edited",
        undo_sections=[{"section_id": "summary", "before": "old text", "extra": "dropped"}],
    )
    assert state["decisions"][0]["undo"] == {
        "sections": [{"section_id": "summary", "before": "old text"}]
    }


@pytest.mark.parametrize("undo_sections", [None, []])
def test_with_decision_undo_is_none_without_sections(undo_sections):
    state = with_decision(
        load_state(None), "ats:docker", label="Docker", action="taken_out",
        undo_sections=undo_sections,
    )
    assert state["decisions"][0]["undo"] is None


def test_with_decision_rejects_unknown_action():
    with pytest.raises(ValueError):
        with_decision(load_state(None), "ats:docker", label="Docker", action="deleted")


def test_without_decision_removes_only_the_matching_finding():
    state = with_decision(load_state(None), "ats:docker", label="Docker", action="taken_out")
    state = with_decision(state, "ats:kubernetes", label="Kubernetes", action="edited")
    state = without_decision(state, "ats:docker")
    keys = [d["finding_key"] for d in state["decisions"]]
    assert keys == ["ats:kubernetes"]


def test_get_decision_matches_by_normalised_text_either_producer():
    state = with_decision(load_state(None), "ats:docker", label="Docker", action="taken_out")
    assert get_decision(state, "oracle:docker") is not None
    assert get_decision(state, "ats:docker")["action"] == "taken_out"
    assert get_decision(state, "ats:nonexistent") is None


@pytest.mark.parametrize("raw", [None, "garbage", 42, [], {"decisions": "not-a-list"}])
def test_load_state_tolerates_none_and_garbage(raw):
    state = load_state(raw)
    assert state == {"walked_at": None, "decisions": []}


def test_load_state_keeps_walked_at_and_filters_invalid_decisions():
    raw = {
        "walked_at": "2026-09-01T00:00:00+00:00",
        "decisions": [
            {"finding_key": "ats:docker", "action": "added", "label": "Docker"},
            {"label": "no finding_key or action"},
            {"finding_key": "ats:weird", "action": "obliterated"},
            "not-even-a-dict",
        ],
    }
    state = load_state(raw)
    assert state["walked_at"] == "2026-09-01T00:00:00+00:00"
    assert len(state["decisions"]) == 1
    assert state["decisions"][0]["finding_key"] == "ats:docker"


def test_load_state_returns_a_copy_not_the_same_object():
    raw = {"walked_at": None, "decisions": [{"finding_key": "ats:docker", "action": "added"}]}
    state = load_state(raw)
    state["decisions"][0]["action"] = "mutated"
    assert raw["decisions"][0]["action"] == "added"


def test_with_walked_sets_timestamp():
    state = with_walked(load_state(None), at="2026-09-01T00:00:00+00:00")
    assert state["walked_at"] == "2026-09-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# derive_review — SF-REVIEW.9 (the load-bearing invariant)
# ---------------------------------------------------------------------------


def _ats_report_with_docker() -> dict:
    return {"keywords": {
        "present_unsupported": ["Docker"],
        "present_unsupported_matches": {"Docker": [{"form": "Docker", "stem": False}]},
        "claimable_concepts": [],
    }}


def _ats_report_without_docker() -> dict:
    return {"keywords": {
        "present_unsupported": [], "present_unsupported_matches": {}, "claimable_concepts": [],
    }}


def test_taken_out_decision_on_a_still_listed_finding_stays_open():
    """SF-REVIEW.9: a decision never hides a finding the LIVE report still
    lists — 'taken out' on a finding the document still carries is open, not
    decided, and counted in open_count."""
    state = with_decision(load_state(None), "ats:docker", label="Docker", action="taken_out")
    review = derive_review(state, _ats_report_with_docker(), {})
    assert review["open"] == [{"finding_key": "ats:docker", "label": "Docker"}]
    assert review["decided"] == []
    assert review["open_count"] == 1
    assert review["decided_count"] == 0
    assert review["total"] == 1
    assert review["unknown_producers"] == []


def test_same_decision_moves_to_decided_once_the_report_no_longer_lists_it():
    state = with_decision(
        load_state(None), "ats:docker", label="Docker", action="taken_out",
        at="2026-09-01T00:00:00+00:00",
    )
    review = derive_review(state, _ats_report_without_docker(), {})
    assert review["open"] == []
    assert review["decided"] == [{
        "finding_key": "ats:docker", "label": "Docker",
        "action": "taken_out", "at": "2026-09-01T00:00:00+00:00",
    }]
    assert review["open_count"] == 0
    assert review["decided_count"] == 1
    assert review["total"] == 1


@pytest.mark.parametrize(
    "ats_report,truth_report,expected",
    [
        (None, None, ["ats", "oracle"]),
        ({}, None, ["oracle"]),
        (None, {}, ["ats"]),
        ({}, {}, []),
    ],
)
def test_unknown_producers_names_a_missing_report(ats_report, truth_report, expected):
    review = derive_review(load_state(None), ats_report, truth_report)
    assert review["unknown_producers"] == expected


# ---------------------------------------------------------------------------
# document_lock
# ---------------------------------------------------------------------------


def test_document_lock_is_the_same_object_for_the_same_id_while_held():
    import uuid
    doc_id = uuid.uuid4()

    async def _run():
        lock1 = document_lock("cv", doc_id)
        async with lock1:
            lock2 = document_lock("cv", doc_id)
            assert lock1 is lock2

    asyncio.run(_run())


def test_document_lock_differs_across_ids_and_kinds():
    import uuid
    id_a, id_b = uuid.uuid4(), uuid.uuid4()

    async def _run():
        async with document_lock("cv", id_a):
            lock_b = document_lock("cv", id_b)
            lock_a_letter = document_lock("cover_letter", id_a)
            assert document_lock("cv", id_a) is not lock_b
            assert document_lock("cv", id_a) is not lock_a_letter

    asyncio.run(_run())
