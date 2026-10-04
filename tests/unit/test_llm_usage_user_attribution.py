# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``llm_usage.user_id`` — per-user visibility of the shared operator key (S-8;
ADR-092 table row ``llm_usage``; W0B-1 users list ``ai_tokens_30d``).

Every provider call made for a user is attributed to that user: an explicit
``llm_usage_context(user_id=…)`` wins, else the USER owner context of the call
(set by the auth dependency, the MCP identity and every background task). An
unscoped context (retention, tooling) or none leaves the row unattributed —
never guessed.
"""

from __future__ import annotations

import uuid

import pytest

from applire import ownership
from applire.providers.llm import usage


class _Inner:
    _model = "test-model"

    async def acomplete(self, prompt: str, **kwargs):  # noqa: ANN003
        return "ok"


@pytest.fixture
def rows():
    captured: list[dict] = []

    async def _sink(row):
        captured.append(row)

    usage.set_sink(_sink)
    yield captured
    usage.restore_sink()


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_a_call_under_a_user_context_is_attributed_to_that_user(rows):
    uid = uuid.uuid4()
    provider = usage.wrap_usage(_Inner())
    with ownership.owner_context(uid):
        await provider.acomplete("hello")
        with usage.llm_usage_context(stage="cv.write", document_kind="cv"):
            await provider.acomplete("hello")
    assert [r["user_id"] for r in rows] == [uid, uid]
    assert rows[1]["stage"] == "cv.write"


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_explicit_attribution_wins_and_unscoped_stays_unattributed(rows):
    uid, other = uuid.uuid4(), uuid.uuid4()
    provider = usage.wrap_usage(_Inner())
    with ownership.owner_context(other):
        with usage.llm_usage_context(stage="s", user_id=uid):
            await provider.acomplete("x")
    with ownership.unscoped("tooling"):
        await provider.acomplete("x")
    await provider.acomplete("x")  # no context at all
    assert [r["user_id"] for r in rows] == [uid, None, None]
