# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet invite-gate, Lead C round 4 — re-probe R-2 / R-3 variations (MD-G14: the card
lists exactly the points the decision releases, D-9).

The adversary's repros (letter, one refusal / one failure) are in
``test_ig_adv_holds_reprobe.py``. These add the other document kind, the failure
repeated, and the decision: what "Passt so" names is what the card shows, no more.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from applire.services import audit_state, review_signals  # noqa: E402
from test_sorbet_l_letter_open_points import _claim, _truth  # noqa: E402

CLAIMS = ("Led a team of 40 engineers.", "Cut cloud cost by 70 %.", "Speaks fluent Japanese.")


def _cv():
    truth = _truth(*(_claim(c, "unbacked", location=f"work_history[0].bullets[{i}]") for i, c in enumerate(CLAIMS)))
    truth["document_kind"] = "cv"
    return SimpleNamespace(
        tailored_data={"summary": "Backend engineer.", "work_history": [{"bullets": list(CLAIMS)}]},
        content_snapshot=None, section_overrides=None, ats_report=None,
        truthfulness_report=truth, review_state=None, audit_state=None, origin="pipeline",
    )


def _letter():
    return SimpleNamespace(
        letter_data={"body": {"paragraphs": ["Dear Sir or Madam,", CLAIMS[0]]}},
        section_overrides=None, ats_report=None,
        truthfulness_report=_truth(_claim(CLAIMS[0], "unbacked")), review_state=None,
        audit_state=None, origin="pipeline",
    )


def _keys(check):
    return [n.get("key") for n in (check or {}).get("notes") or []]


def _texts(check):
    return sorted(n.get("text") for n in (check or {}).get("notes") or [] if n.get("key") != "audit.not_rechecked")


def _edit(rec):
    if hasattr(rec, "letter_data"):
        rec.letter_data = copy.deepcopy(rec.letter_data)
        rec.letter_data["body"]["paragraphs"][0] += " "
    else:
        rec.tailored_data = copy.deepcopy(rec.tailored_data)
        rec.tailored_data["summary"] += " "


def _refused_save(rec):
    """The section editor's save while the re-audit is refused (consent withdrawn)."""
    _edit(rec)
    audit_state.mark_pending(rec)
    carried = review_signals.open_points_check(rec)  # the seam's ``_carried``
    rec.truthfulness_report = None
    audit_state.record_audit(rec, ran=False, reason="log_consent_required", carried=carried)


def _failed_reaudit(rec):
    """A re-audit whose render crashed while the truth audit ran (the D-5 path)."""
    carried = review_signals.open_points_check(rec)
    audit_state.record_audit(rec, ran=False, reason="render_failed", carried=carried)


def _keep(rec, check):
    rec.review_state = {"decisions": [{
        "finding_key": review_signals.TERMINAL_REVIEW_KEY, "action": "kept",
        "basis": review_signals.decision_basis(check),
    }]}


def test_r2_a_cv_keeps_every_carried_point_over_three_refused_saves():
    rec = _cv()
    before = _texts(review_signals.open_points_check(rec))
    assert before == sorted(CLAIMS)
    counts = []
    for _ in range(3):
        _refused_save(rec)
        check = review_signals.open_points_check(rec)
        assert _texts(check) == before, _keys(check)
        assert _keys(check).count("audit.not_rechecked") == 1
        counts.append(check["details_params"]["count"])
    assert counts == [4, 4, 4]  # 3 statements + the one "not re-checked" line, every time


def test_r2_the_carried_points_stand_while_the_next_re_audit_runs():
    """Between a save and its re-audit (pending) the card still shows what stood."""
    rec = _letter()
    _refused_save(rec)
    _edit(rec)
    audit_state.mark_pending(rec)
    assert audit_state.pending(rec)
    assert _texts(review_signals.open_points_check(rec)) == [CLAIMS[0]]


def test_r3_a_cv_with_a_live_truth_report_counts_each_point_once_over_repeated_failures():
    rec = _cv()
    for _ in range(3):
        _failed_reaudit(rec)
        check = review_signals.open_points_check(rec)
        assert _texts(check) == sorted(CLAIMS), _keys(check)
        assert _keys(check).count("audit.not_rechecked") == 1, _keys(check)
        assert check["details_params"]["count"] == 4


def test_the_decision_releases_exactly_what_the_card_shows_and_no_more():
    """D-9 on the carried card: Passt so on the shown points releases; a point the card
    did not show (a new statement found by the next audit) holds again."""
    rec = _letter()
    _refused_save(rec)
    _refused_save(rec)
    check = review_signals.open_points_pending(rec)
    assert _texts(check) == [CLAIMS[0]]
    _keep(rec, check)
    assert review_signals.open_points_pending(rec) is None
    # the re-audit finally runs and finds a second statement: the decision does not cover it
    rec.truthfulness_report = _truth(_claim(CLAIMS[0], "unbacked"), _claim(CLAIMS[1], "unbacked"))
    audit_state.record_audit(rec, ran=True)
    assert _texts(review_signals.open_points_pending(rec)) == sorted(CLAIMS[:2])


def test_one_not_rechecked_line_across_different_failures_and_the_pending_window():
    """A render crash, then a refused save (another reason): the card says "not
    re-checked" once, for the current state; while the save's re-audit runs it shows
    the carried statements and no stale failure line."""
    rec = _cv()
    _failed_reaudit(rec)
    _failed_reaudit(rec)
    _edit(rec)
    audit_state.mark_pending(rec)
    pending_check = review_signals.open_points_check(rec)
    assert _keys(pending_check).count("audit.not_rechecked") == 0
    assert _texts(pending_check) == sorted(CLAIMS)
    carried = pending_check
    rec.truthfulness_report = None
    audit_state.record_audit(rec, ran=False, reason="log_consent_required", carried=carried)
    check = review_signals.open_points_check(rec)
    assert _keys(check).count("audit.not_rechecked") == 1
    assert [n["subjects"] for n in check["notes"] if n["key"] == "audit.not_rechecked"] == [["log_consent_required"]]
    assert _texts(check) == sorted(CLAIMS) and check["details_params"]["count"] == 4


def test_one_not_rechecked_line_after_two_failures_with_different_reasons():
    rec = _letter()
    _failed_reaudit(rec)  # render_failed
    carried = review_signals.open_points_check(rec)
    audit_state.record_audit(rec, ran=False, reason="audit_failed", carried=carried)
    check = review_signals.open_points_check(rec)
    assert [n["subjects"] for n in check["notes"] if n["key"] == "audit.not_rechecked"] == [["audit_failed"]]
    assert _texts(check) == [CLAIMS[0]] and check["details_params"]["count"] == 2
