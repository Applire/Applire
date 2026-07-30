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

"""Adversarial pass (2026-07-30), finding 2 — SF-CRITIC.1's log line.

Evidence: after a full real run the letter's ``critic_report`` was
``{"ran": true, "reason": null, "advisories": [...2 items...]}`` — the pass
genuinely ran and produced advisories — yet four separate greps of the
backend container log (``outcome_critic``, ``CRITIC``, ``pass_b``,
``Pass B``, ``RAN``, ``DID NOT RUN``, ``advisor``, ``candidates``) found no
critic line whatsoever. SF-CRITIC.1 requires the three states — "did not run
(reason)" / "ran, N candidates, M advisories" / "ran but the judgement call
failed" — to be distinguishable in the log; a silent pass and a working one
must never look the same.

**Diagnosis** (see the session report for the full walk-through): every
``run_pass_b`` return path DOES call ``logger.info``/``logger.warning``
immediately before its ``return`` — reproduced directly against the real
generation entrypoint (``_render_cover_letter_background``) via both
``caplog`` and ``--log-cli-level=INFO``, on every one of the five/six
branches, and the log line always fires. The three "likely candidates" the
finding names (never called on the success path / after an early return /
misconfigured logger) are each individually disproved by that reproduction.

The defect that DOES reproduce the evidence: the "did not run" and "ran, N/M"
transition summaries — the two states an operator most needs to tell a
silently-disabled critic apart from a quietly-working one — were logged at
INFO, while ONLY the "judgement call failed" path used WARNING. ``config.py``
documents ``LOG_LEVEL=WARNING`` as a fully supported operational setting
("DEBUG | INFO | WARNING | ERROR — applied to all applire.* loggers"); under
that (legitimate, documented) configuration the two INFO-level states vanish
identically — "a disabled pass and a working one currently look identical
from the outside" is EXACTLY what silent-under-WARNING INFO logging produces,
matching every one of the adversarial run's eight failed greps. The fix
promotes the state-defining lines to the SAME level (WARNING) the failure
path already uses, so all three SF-CRITIC.1 states survive together under
any operationally-supported log level — never a second log call bolted on
next to the existing (structurally live, but wrong-tier) one.
"""
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services import outcome_critic as oc  # noqa: E402

_LOGGER_NAME = "applire.services.outcome_critic"

_LEDGER = [
    {
        "concept": "ISO 9001",
        "surface_forms": ["ISO 9001", "ISO-9001"],
        "claimable": True,
        "status": "direct",
    }
]

CV_TAILORED = {
    "skills": ["ISO 9001"],
    "work_experience": [
        {"company": "Musterwerk GmbH", "achievements": ["ISO 9001 zertifiziert."]}
    ],
}

LETTER_DATA = {
    "body": {
        "paragraphs": [
            "Mit zehn Jahren ISO-9001-Auditpraxis bringe ich genau die "
            "Qualitätssicherungs-Expertise mit, die Sie suchen."
        ]
    }
}


def _fake_provider(result):
    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value=result)
    return provider


@pytest.mark.asyncio
async def test_a_disabled_critic_logs_at_warning_not_only_info(caplog):
    """SF-CRITIC.1 state 1: "did not run (reason)". Under a production
    deployment running at the fully-supported LOG_LEVEL=WARNING, this state
    must still be visible — silence here is indistinguishable from a
    critic that ran and simply had nothing to say."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)

    report = await oc.run_pass_b(
        cv_tailored=CV_TAILORED,
        letter_data=LETTER_DATA,
        keyword_ledger=_LEDGER,
        job_role_title="Qualitätsmanager",
        jd_excerpt=None,
        provider=_fake_provider({"findings": []}),
        enabled=False,
    )
    assert report.ran is False
    assert report.reason == "disabled"

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "DID NOT RUN" in r.message and "CRITIC_ENABLED=false" in r.message for r in warnings
    ), (
        "the 'did not run (disabled)' state produced no WARNING-or-above log "
        f"record; records seen: {[(r.levelname, r.message) for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_a_successful_run_logs_at_warning_not_only_info(caplog):
    """SF-CRITIC.1 state 2: "ran, N candidates, M advisories". The exact
    shape the adversarial run's persisted critic_report showed with zero
    matching log lines — must be visible at the same tier as a failure."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)

    judgement = {"findings": [{"concept": "ISO 9001", "worth_surfacing": True}]}
    report = await oc.run_pass_b(
        cv_tailored=CV_TAILORED,
        letter_data=LETTER_DATA,
        keyword_ledger=_LEDGER,
        job_role_title="Qualitätsmanager",
        jd_excerpt=None,
        provider=_fake_provider(judgement),
        enabled=True,
        max_rounds=1,
    )
    assert report.ran is True
    assert report.reason is None
    assert len(report.advisories) == 1

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "RAN" in r.message and "1 candidate" in r.message and "1 advisory" in r.message
        for r in warnings
    ), (
        "a successful run with real advisories produced no WARNING-or-above "
        f"log record; records seen: {[(r.levelname, r.message) for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_a_failed_judgement_call_logs_at_warning(caplog):
    """SF-CRITIC.1 state 3: "ran but the judgement call failed" — already
    WARNING before this fix; pinned here so a future edit can't quietly
    demote it while promoting the other two, which would just move the
    indistinguishability problem instead of fixing it."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)

    provider = MagicMock()
    provider.aparse_json = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    report = await oc.run_pass_b(
        cv_tailored=CV_TAILORED,
        letter_data=LETTER_DATA,
        keyword_ledger=_LEDGER,
        job_role_title="Qualitätsmanager",
        jd_excerpt=None,
        provider=provider,
        enabled=True,
        max_rounds=1,
    )
    assert report.ran is True
    assert report.reason == "judgement_error"

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("RAN" in r.message and "judgement call never succeeded" in r.message for r in warnings)


@pytest.mark.asyncio
async def test_the_three_states_are_distinguishable_at_the_same_log_level(caplog):
    """The FMEA row's own wording, directly pinned: run all three states at
    the SAME caplog level and confirm each produces a DIFFERENT, identifiable
    message — never all-silent, never collapsed into one shape."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)

    await oc.run_pass_b(
        cv_tailored=CV_TAILORED, letter_data=LETTER_DATA, keyword_ledger=_LEDGER,
        job_role_title=None, jd_excerpt=None, provider=_fake_provider({}), enabled=False,
    )
    disabled_messages = [r.message for r in caplog.records]
    caplog.clear()

    judgement = {"findings": [{"concept": "ISO 9001", "worth_surfacing": True}]}
    await oc.run_pass_b(
        cv_tailored=CV_TAILORED, letter_data=LETTER_DATA, keyword_ledger=_LEDGER,
        job_role_title=None, jd_excerpt=None, provider=_fake_provider(judgement),
        enabled=True, max_rounds=1,
    )
    ran_messages = [r.message for r in caplog.records]
    caplog.clear()

    failing_provider = MagicMock()
    failing_provider.aparse_json = AsyncMock(side_effect=RuntimeError("boom"))
    await oc.run_pass_b(
        cv_tailored=CV_TAILORED, letter_data=LETTER_DATA, keyword_ledger=_LEDGER,
        job_role_title=None, jd_excerpt=None, provider=failing_provider,
        enabled=True, max_rounds=1,
    )
    failed_messages = [r.message for r in caplog.records]

    assert disabled_messages, "disabled state produced nothing at WARNING"
    assert ran_messages, "successful-run state produced nothing at WARNING"
    assert failed_messages, "judgement-failure state produced nothing at WARNING"
    assert set(disabled_messages).isdisjoint(ran_messages)
    assert set(disabled_messages).isdisjoint(failed_messages)
    assert set(ran_messages).isdisjoint(failed_messages)
