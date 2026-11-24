# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-G13 at the audit seam: a truth report whose placement judgement went
unanswered (``placement_unavailable`` > 0) is an audit that did not finish. The
document is held as "not re-checked" with the reason code ``placement_unavailable``
(MD-G9's generic note), and a good re-check releases it again. Letter and CV, the
generation audit and the re-check that keeps reports on failure.

Point COUNTS on this path are not asserted here: the live-report + not-run path's
point list is Lead C's (review_signals ``_audit_points``) and is verified after the
merge.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services import review_signals  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_c_mdg9_holds import _cv_patches  # noqa: E402
from test_cv_docx_endpoint import client, db, ready_cv  # noqa: E402,F401
from test_sorbet_l_letter_open_points import _claim, _truth, red_letter  # noqa: E402,F401

_REASON = "placement_unavailable"


def _green(kind, placement_unavailable=0):
    t = _truth(_claim("I integrated Stripe checkout with webhooks.", "grounded"))
    t["document_kind"] = kind
    t["placement_unavailable"] = placement_unavailable
    return t


def _not_rechecked(record):
    held = review_signals.open_points_pending(record)
    if held is None:
        return None
    lines = [n for n in held.get("notes") or [] if n.get("key") == "audit.not_rechecked"]
    return lines[0] if lines else None


async def _reaudit_cv(db, record, *, truth, **kw):
    from contextlib import ExitStack

    from applire.services import cv as cv_mod

    with ExitStack() as stack:
        for p in _cv_patches(truth=truth):
            stack.enter_context(p)
        await cv_mod._update_ats_report(record, db, critic=False, **kw)
    await db.refresh(record)
    return record


async def _reaudit_letter(db, cl, truth, **kw):
    from applire.services import cover_letter as cl_mod

    with patch.object(cl_mod, "_letter_truth_audit", new=AsyncMock(return_value=truth)), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"pdf")), \
         patch("applire.services.ats_audit.extract_text_and_pages", new=MagicMock(return_value=("t", 1))):
        await cl_mod._update_ats_report_letter(cl, db, critic=False, **kw)
    await db.refresh(cl)
    return cl


@pytest.mark.asyncio
@pytest.mark.parametrize("keep", [False, True])
async def test_a_letter_whose_placement_went_unanswered_is_held_until_a_good_recheck(db, red_letter, keep):  # noqa: F811
    await _reaudit_letter(db, red_letter, _green("cover_letter"))
    assert review_signals.open_points_pending(red_letter) is None, "the clean baseline must be released"

    await _reaudit_letter(db, red_letter, _green("cover_letter", placement_unavailable=1),
                          keep_reports_on_failure=keep)
    assert red_letter.audit_state["state"] == "not_run"
    assert red_letter.audit_state["reason"] == _REASON
    line = _not_rechecked(red_letter)
    assert line is not None and line["subjects"] == [_REASON], review_signals.open_points_pending(red_letter)
    # no accusation came from the outage
    assert not any(c["verdict"]["verdict"] == "misattributed" for c in red_letter.truthfulness_report["claims"])

    await _reaudit_letter(db, red_letter, _green("cover_letter"), keep_reports_on_failure=keep)
    assert red_letter.audit_state["state"] == "done"
    assert review_signals.open_points_pending(red_letter) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("keep", [False, True])
async def test_a_cv_whose_report_says_placement_went_unanswered_is_held_until_a_good_recheck(db, ready_cv, keep):  # noqa: F811
    await _reaudit_cv(db, ready_cv, truth=_green("cv"))
    assert review_signals.open_points_pending(ready_cv) is None

    await _reaudit_cv(db, ready_cv, truth=_green("cv", placement_unavailable=2), keep_reports_on_failure=keep)
    assert ready_cv.audit_state["state"] == "not_run"
    line = _not_rechecked(ready_cv)
    assert line is not None and line["subjects"] == [_REASON]

    await _reaudit_cv(db, ready_cv, truth=_green("cv"), keep_reports_on_failure=keep)
    assert ready_cv.audit_state["state"] == "done"
    assert review_signals.open_points_pending(ready_cv) is None


@pytest.mark.asyncio
async def test_a_legacy_report_without_the_field_is_not_incomplete(db, red_letter):  # noqa: F811
    legacy = _green("cover_letter")
    del legacy["placement_unavailable"]
    await _reaudit_letter(db, red_letter, legacy)
    assert red_letter.audit_state["state"] == "done"
    assert review_signals.open_points_pending(red_letter) is None


@pytest.mark.asyncio
async def test_the_person_can_decide_the_not_rechecked_letter(db, red_letter):  # noqa: F811
    await _reaudit_letter(db, red_letter, _green("cover_letter", placement_unavailable=1))
    held = review_signals.open_points_pending(red_letter)
    assert held is not None
    out = await review_signals.keep("cover_letter", red_letter.id, review_signals.TERMINAL_REVIEW_KEY, db,
                                    basis=held["open_points_hash"], check_basis=True)
    assert review_signals.open_points_pending(out.record) is None
