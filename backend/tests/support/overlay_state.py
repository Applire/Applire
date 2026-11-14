# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The harness stands in for a process that read its admin overrides (adv-K F2).

``services.instance_settings`` tracks whether the process ever loaded the panel
overrides; until it has, after a failed read, model work refuses with
``settings_unavailable`` (a panel ``LLM_KEY_MODE=user`` cannot be ruled out).
The unit tiers' hand-built databases have no ``instance_settings`` table, so the
ASGI middleware's refresh fails on every request. That is the harness, not the
state under test: each test starts as a process that loaded its overrides once.
Tests of the fresh-process state set ``ever_loaded`` back themselves.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def overrides_loaded_once():
    from applire.services import instance_settings

    state = instance_settings._state
    before = (state.ever_loaded, state.load_failed)
    state.ever_loaded, state.load_failed = True, False
    yield
    state.ever_loaded, state.load_failed = before
