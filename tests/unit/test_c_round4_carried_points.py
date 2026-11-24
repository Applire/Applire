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


def test_r3_a_cv_with_a_live_truth_report_counts_each_point_once_over_repeated_failures():
    rec = _cv()
    for _ in range(3):
        _failed_reaudit(rec)
        check = review_signals.open_points_check(rec)
        assert _texts(check) == sorted(CLAIMS), _keys(check)
        assert _keys(check).count("audit.not_rechecked") == 1, _keys(check)
        assert check["details_params"]["count"] == 4


def test_one_not_rechecked_line_after_two_failures_with_different_reasons():
    rec = _letter()
    _failed_reaudit(rec)  # render_failed
    carried = review_signals.open_points_check(rec)
    audit_state.record_audit(rec, ran=False, reason="audit_failed", carried=carried)
    check = review_signals.open_points_check(rec)
    assert [n["subjects"] for n in check["notes"] if n["key"] == "audit.not_rechecked"] == [["audit_failed"]]
    assert _texts(check) == [CLAIMS[0]] and check["details_params"]["count"] == 2
