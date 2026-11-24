# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""adv/holds re-probe 2026-11-24 (ecabac48) — the adversary's repros, red on that head
(copied from wt-adv-ig-holds; the report numbers them R-3 and R-2, the placement
repro R-4 is Lead L's and lives with the Oracle).

R-3 (``test_r1_…``): a re-audit that did not run while the truth report is still live
     lists every statement twice on the joined card (carried + live), and counts it twice.
R-2 (``test_r2_…``): a second save while the re-audit is refused drops the carried
     statements: the audit seam reads ``open_points_check`` while the save's
     ``pending`` marker stands, and ``_audit_points`` ignores a fresh pending state.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "tests" / "unit" / "services"))

from applire.services import audit_state, review_signals  # noqa: E402
from applire.services.oracle import audit_document  # noqa: E402

from test_oracle_letter_placement import PROFILE, RUN  # noqa: E402

UNBACKED = "I also led a team of 40 engineers."


async def _letter_record(sentence: str):
    letter = {"body": {"paragraphs": ["Dear Sir or Madam,", RUN + sentence]}}
    rep = await audit_document("cover_letter", PROFILE, letter_data=letter)
    return SimpleNamespace(
        letter_data=letter, section_overrides=None, ats_report=None,
        truthfulness_report=rep.model_dump(mode="json"), review_state=None,
        audit_state=None, origin="pipeline",
    )


def _keys(check):
    return [n.get("key") for n in (check or {}).get("notes") or []]


@pytest.mark.asyncio
async def test_r1_a_failed_re_audit_with_a_live_truth_report_does_not_double_the_points():
    rec = await _letter_record(UNBACKED)
    before = review_signals.open_points_check(rec)
    assert before is not None and _keys(before).count("oracle.unbacked") == 1
    # the ATS render crashed (D-5 path); the truth audit ran and still lists the point
    audit_state.record_audit(rec, ran=False, reason="render_failed", carried=before)
    after = review_signals.open_points_check(rec)
    assert _keys(after).count("oracle.unbacked") == 1, _keys(after)
    assert after["details_params"]["count"] == 2, after["details_params"]


@pytest.mark.asyncio
async def test_r2_a_second_save_while_the_re_audit_is_refused_keeps_the_carried_points():
    rec = await _letter_record(UNBACKED)
    # save #1: the re-audit is refused (consent withdrawn) → truth report gone
    rec.letter_data = copy.deepcopy(rec.letter_data)
    rec.letter_data["body"]["paragraphs"][1] += " "
    audit_state.mark_pending(rec)
    carried = review_signals.open_points_check(rec)  # what the audit seam reads as _carried
    rec.truthfulness_report = None
    audit_state.record_audit(rec, ran=False, reason="log_consent_required", carried=carried)
    assert "oracle.unbacked" in _keys(review_signals.open_points_check(rec))
    # save #2: same refusal
    rec.letter_data = copy.deepcopy(rec.letter_data)
    rec.letter_data["body"]["paragraphs"][1] += " "
    audit_state.mark_pending(rec)
    carried = review_signals.open_points_check(rec)
    audit_state.record_audit(rec, ran=False, reason="log_consent_required", carried=carried)
    assert "oracle.unbacked" in _keys(review_signals.open_points_check(rec)), _keys(
        review_signals.open_points_check(rec)
    )
