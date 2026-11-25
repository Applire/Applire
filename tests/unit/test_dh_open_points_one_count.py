# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""DH item 2 (Sorbet invite-gate delivery run, 2026-11-25): one count from one source.

Seen: the card said "3 Punkte" and listed six lines (three reviewer findings + three
code facts of the delivered draft); the agent relayed a bare "Maintenance". The shapes
below are the delivery DB's held rows (CV bba950da, CV 748c4399, letter f4aee5e2),
trimmed. The served check now marks each line ``point`` or not; the count is the
number of point lines, and a truth point carries a sentence (``plain``).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from applire.services import review_signals  # noqa: E402
from test_sorbet_l_letter_open_points import _ats, _claim, _truth  # noqa: E402

FINDINGS = [
    {"kind": "finding", "key": None, "text": f"finding {i}", "plain": f"Plain {i}.", "subjects": []}
    for i in range(3)
]
CODE_FACTS = [
    {"kind": "code", "key": "cv.claim_missing", "text": "claimable concept …", "subjects": ["Hohe Last"]},
    {"kind": "code", "key": "cv.skill_spelling", "text": "Skills entries …", "subjects": ["PSP-Integration"]},
    {"kind": "code", "key": "cv.skills_cap", "text": "The skills list …", "subjects": ["S3"]},
]
CLEAN = _truth(_claim("I mentor two junior engineers.", "grounded"))


def _cv(notes, *, count, present_unsupported=(), truth=CLEAN):
    ats = _ats("terminal-review-open", "fail", terminal_hash="c" * 16, terminal_count=count, notes=notes,
               present_unsupported=present_unsupported)
    ats["document"] = "cv"
    return SimpleNamespace(ats_report=ats, review_state=None, truthfulness_report=truth, origin="pipeline",
                           audit_state=None)


def _points(check):
    return [n for n in check["notes"] if n.get("point")]


def test_three_findings_and_three_code_facts_are_three_points_and_three_context_lines():
    check = review_signals.open_points_check(_cv(FINDINGS + CODE_FACTS, count=3))
    assert check["details_params"]["count"] == 3
    assert [n["text"] for n in _points(check)] == ["finding 0", "finding 1", "finding 2"]
    assert [n["key"] for n in check["notes"] if not n.get("point")] == [
        "cv.claim_missing", "cv.skill_spelling", "cv.skills_cap",
    ]
    held = review_signals.open_points_pending(_cv(FINDINGS + CODE_FACTS, count=3))
    assert review_signals.open_points_count(held) == 3  # the 409 and the agent's count


def test_a_posting_term_point_is_counted_once_and_carries_a_sentence():
    rec = _cv(FINDINGS + CODE_FACTS[:1], count=3, present_unsupported=["Maintenance"])
    check = review_signals.open_points_check(rec)
    assert check["details_params"]["count"] == 4 == len(_points(check))
    (term,) = [n for n in check["notes"] if n.get("key") == "oracle.posting_term"]
    assert term["text"] == "Maintenance" and term["finding_key"] == "ats:maintenance"
    assert term["plain"] == "“Maintenance” is a word from the job posting that the profile does not back."


def test_a_letter_held_only_by_a_posting_term_counts_one():
    ats = _ats(present_unsupported=["Maintenance"])
    letter = SimpleNamespace(letter_data={"body": {"paragraphs": ["x"]}}, ats_report=ats, review_state=None,
                             truthfulness_report=CLEAN, origin="pipeline", audit_state=None)
    check = review_signals.open_points_check(letter)
    assert check["details_params"]["count"] == 1 and len(_points(check)) == 1


def test_coverage_and_form_code_facts_alone_hold_nothing():
    """MD-G32 X-3: a code fact is classed by its check key — the three fixture keys are
    coverage/form, so a row with nothing else open is not held."""
    assert review_signals.open_points_check(_cv(CODE_FACTS, count=2)) is None


def test_truth_code_facts_are_the_points_next_to_advice():
    truth = {"kind": "code", "key": "cv.skill_untied", "text": "Skills entries with no tie …", "subjects": ["Kubernetes"]}
    check = review_signals.open_points_check(_cv(CODE_FACTS + [truth], count=2))
    assert check["details_params"]["count"] == 1 == len(_points(check))
    assert [n["key"] for n in _points(check)] == ["cv.skill_untied"]


def test_a_more_line_of_findings_counts_its_number():
    notes = FINDINGS + [{"kind": "finding", "key": "more", "count": 4}] + CODE_FACTS[:1]
    check = review_signals.open_points_check(_cv(notes, count=7))
    assert check["details_params"]["count"] == 7


def test_an_unbacked_claim_point_says_so_in_a_sentence():
    truth = _truth(_claim("I led a team of 120 people.", "unbacked"))
    check = review_signals.open_points_check(_cv(FINDINGS[:1], count=1, truth=truth))
    (claim,) = [n for n in check["notes"] if n.get("key") == "oracle.unbacked"]
    assert claim["plain"].endswith("the profile does not back this statement.")
    assert check["details_params"]["count"] == 2
