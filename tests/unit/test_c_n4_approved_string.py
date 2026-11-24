# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Holds re-probe N-4: ``"approved": "false"`` (a string) was read as an approval —
``bool("false")`` is True — and a rejected final draft shipped ``terminal-review-clean``."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from applire.services.review_issues import approved_flag  # noqa: E402
from test_c_mdg9_holds import REJECT, _loop_end  # noqa: E402

PLAIN = "The summary claims a team of 40 the profile does not show."


@pytest.mark.parametrize("value,expected", [
    (True, True), ("true", True), (" TRUE ", True),
    (False, False), ("false", False), ("False", False), ("no", False), ("", False),
    (None, False), (1, False), ("yes", False), ([], False),
])
def test_only_a_true_approval_is_an_approval(value, expected):
    assert approved_flag(value) is expected


@pytest.mark.asyncio
async def test_n4_a_string_false_final_verdict_is_not_a_clean_ship():
    stringly = {"approved": "false", "feedback": "fix it", "issues": [PLAIN]}
    out, st, check, held = await _loop_end([REJECT, stringly])
    assert st.approved is False
    assert check["details_key"] != "terminal-review-clean", check["details_key"]
    assert held is not None


@pytest.mark.asyncio
async def test_n4_twice_in_a_row_a_string_false_is_still_a_rejection():
    stringly = {"approved": "false", "feedback": "fix it", "issues": [PLAIN]}
    out, st, check, held = await _loop_end([stringly, stringly])
    assert st.approved is False and held is not None


@pytest.mark.asyncio
async def test_n4_a_string_true_approval_still_approves():
    out, st, check, held = await _loop_end([REJECT, {"approved": "true", "feedback": "", "issues": []}])
    assert st.approved is True and held is None
