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

"""The audit tails relabel the LLM-log stage (#538/#539 refuter observation).

`review_and_refine` sets the per-task stage contextvar to its chain id and
never resets it, so the audit tail's own LLM calls (Oracle sentence triage,
outcome critic) used to inherit the LAST chain's label — on the 2026-08-16
evidence runs they logged as ``cv_terminal_review`` / ``letter_terminal_review``,
poisoning every log-based per-chain count. These tests prove both audit
entrypoints stamp their own stage label before any provider call can happen:
the audit bodies are driven with broken inputs (every block fails and is
caught — the audits' own fail-open contract), so what remains observable is
exactly the stage relabel."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


@pytest.mark.asyncio
async def test_letter_audit_relabels_the_stage():
    from applire.providers.llm.debug_log import current_call_site, set_stage
    from applire.services.cover_letter import _update_ats_report_letter

    set_stage("letter_terminal_review")  # what the last chain left behind
    cl = MagicMock()
    cl.letter_data = {}
    cl.section_overrides = {}
    db = AsyncMock()
    db.get.return_value = None
    await _update_ats_report_letter(cl, db, pdf=b"not-a-pdf")

    assert current_call_site()[0] == "letter_audit", (
        "the audit tail must not inherit the terminal chain's stage label"
    )


@pytest.mark.asyncio
async def test_cv_audit_relabels_the_stage():
    from applire.providers.llm.debug_log import current_call_site, set_stage
    from applire.services.cv import _update_ats_report

    set_stage("cv_terminal_review")
    record = MagicMock()
    record.tailored_data = {}
    record.section_overrides = {}
    db = AsyncMock()
    db.get.return_value = None
    await _update_ats_report(record, db, measured=None, commit=False)

    assert current_call_site()[0] == "cv_audit", (
        "the audit tail must not inherit the terminal chain's stage label"
    )
