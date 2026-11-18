# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A re-check of unchanged content keeps the stored truthfulness and critic
reports when their model work is refused or fails; after an edit the reports
describe the old text and are cleared as before. A step refused at the provider
seam (no consent, key or allowance) is logged as not run, not as a failure.
"""

import importlib.util
import logging
import pathlib
from unittest.mock import AsyncMock, patch

import pytest

from applire.services.allowance import AdmissionRefused

_spec = importlib.util.spec_from_file_location(
    "_truth_persistence", pathlib.Path(__file__).with_name("test_truthfulness_report_persistence.py")
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
db, seeded = _mod.db, _mod.seeded  # fixtures
_make_ats_report, _make_truthfulness_report_dict = _mod._make_ats_report, _mod._make_truthfulness_report_dict

CRITIC = {"ran": True, "reason": None, "findings": []}
TRUTH = _make_truthfulness_report_dict()


def _audit_patches(truth_error: Exception, critic_error: Exception):
    return (
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF")),
        patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 1)),
        patch("applire.services.ats_audit._audit_cv_text", return_value=_make_ats_report("cv")),
        # A refusal comes from the provider factory (the door), any other error
        # from inside the audit; the critic gets the same.
        patch("applire.services.cv.get_provider", side_effect=truth_error)
        if isinstance(truth_error, AdmissionRefused)
        else patch("applire.services.oracle.selfaudit.audit_document", side_effect=truth_error),
        patch("applire.services.outcome_critic.run_pass_a", side_effect=critic_error),
    )


async def _run(seeded, *, keep: bool, truth_error: Exception, critic_error: Exception):
    from applire.models.cv import GeneratedCV
    from applire.services.cv import _update_ats_report

    session = seeded["db"]
    record = await session.get(GeneratedCV, seeded["cv_id"])
    record.truthfulness_report = TRUTH
    record.critic_report = dict(CRITIC)
    await session.commit()
    p = _audit_patches(truth_error, critic_error)
    with p[0], p[1], p[2], p[3], p[4], p[5]:
        await _update_ats_report(record, session, keep_reports_on_failure=keep)
    session.expire_all()
    return await session.get(GeneratedCV, seeded["cv_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [AdmissionRefused("log_consent_required"), RuntimeError("provider down")]
)
async def test_a_recheck_that_cannot_run_keeps_the_stored_reports(seeded, error):
    record = await _run(seeded, keep=True, truth_error=error, critic_error=error)
    assert record.truthfulness_report == TRUTH
    assert record.critic_report == CRITIC
    assert record.ats_report is not None


@pytest.mark.asyncio
async def test_after_an_edit_the_old_reports_are_cleared(seeded):
    error = RuntimeError("provider down")
    record = await _run(seeded, keep=False, truth_error=error, critic_error=error)
    assert record.truthfulness_report is None and record.critic_report is None


@pytest.mark.asyncio
async def test_a_step_refused_at_the_door_is_logged_as_not_run(seeded, caplog):
    caplog.set_level(logging.INFO, logger="applire.services.cv")
    refused = AdmissionRefused("log_consent_required")
    await _run(seeded, keep=True, truth_error=refused, critic_error=refused)
    lines = [r for r in caplog.records if r.name == "applire.services.cv" and "not run" in r.getMessage()]
    assert len(lines) == 2 and all(r.levelno == logging.INFO for r in lines)
    assert not [r for r in caplog.records if r.name == "applire.services.cv" and r.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_the_recheck_action_asks_for_kept_reports(seeded):
    from applire.services import review_actions as ra

    with patch.object(ra, "reaudit", new=AsyncMock()) as reaudit:
        await ra.recheck("cv", seeded["cv_id"], seeded["db"], user_id=None)
    assert reaudit.await_args.kwargs == {"keep_reports_on_failure": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,target", [
    ("cv", "applire.services.cv._update_ats_report"),
    ("cover_letter", "applire.services.cover_letter._update_ats_report_letter"),
])
@pytest.mark.parametrize("keep", [True, False])
async def test_the_reaudit_hands_the_keep_flag_to_the_audit(kind, target, keep):
    from unittest.mock import MagicMock

    from applire.services import review_actions as ra

    db = MagicMock()
    db.refresh = AsyncMock()
    with patch(target, new=AsyncMock()) as audit, patch.object(ra, "note_audited"):
        await ra.reaudit(kind, MagicMock(), db, keep_reports_on_failure=keep)
    assert audit.await_args.kwargs == {"keep_reports_on_failure": keep}
