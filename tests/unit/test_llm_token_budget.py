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

"""Token-budget robustness (ADR-009 amendment, token-budget robustness).

Two regressions are pinned here, found in a real-LLM walkthrough on a rich 2-CV
profile where ~1 in 5 aparse_json calls hit the 4096 output cap (finish=length →
LLMTruncatedError → HTTP 500/504):

  1. The under-budgeted chains (JD analysis, gap clustering, gap analysis pass 2,
     skill-year estimation) must thread a max_tokens budget WELL ABOVE the 4096
     provider default — they previously omitted it entirely.
  2. A general auto-retry-on-truncation safety net: a one-off truncation is retried
     once with a doubled budget before raising; an uncoverable truncation (already
     at the ceiling, or truncating again on the larger budget) still raises
     LLMTruncatedError and never loops forever.

Hermetic: providers/stubs are injected, get_provider() is never called, and every
stub absorbs the full ABC signature via **kwargs (AGENTS.md).
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.exceptions import LLMTruncatedError


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Stop(Exception):
    """Sentinel to short-circuit a service right after the LLM call, before the
    DB-record/response-validation work an injected stub can't satisfy."""


class _BudgetSpyProvider:
    """Records the max_tokens threaded into each call; returns a canned payload.

    Absorbs the full ABC signature via **kwargs so it stays valid if the provider
    contract grows (AGENTS.md). With ``stop_after=True`` it raises ``_Stop`` right
    after recording, so a test can assert the budget without driving the whole
    persistence path through a fake DB."""

    def __init__(self, json_response, *, stop_after=False):
        self._json_response = json_response
        self._stop_after = stop_after
        self.parse_budgets: list[int] = []
        self.complete_budgets: list[int] = []

    async def aparse_json(self, prompt, *, max_tokens=4096, **kwargs):
        self.parse_budgets.append(max_tokens)
        if self._stop_after:
            raise _Stop
        # Return a fresh copy so callers that mutate don't bleed across calls.
        if isinstance(self._json_response, list):
            return list(self._json_response)
        return dict(self._json_response)

    async def acomplete(self, prompt, *, max_tokens=4096, **kwargs):
        self.complete_budgets.append(max_tokens)
        return "stub completion"

    async def embed(self, text, **kwargs):  # used by analyze_jd's embedding path
        return None


# ---------------------------------------------------------------------------
# (a) Under-budgeted chains now request a budget well above 4096
# ---------------------------------------------------------------------------


def test_budget_constants_are_above_the_4096_default():
    """The tuned ceilings must sit well above the 4096 provider default that
    truncated rich-profile calls."""
    from applire.constants import (
        GAP_ANALYSIS_MAX_TOKENS,
        GAP_CLUSTERING_MAX_TOKENS,
        JD_ANALYSIS_MAX_TOKENS,
        SKILL_ESTIMATION_MAX_TOKENS,
    )

    for value in (
        JD_ANALYSIS_MAX_TOKENS,
        GAP_CLUSTERING_MAX_TOKENS,
        GAP_ANALYSIS_MAX_TOKENS,
        SKILL_ESTIMATION_MAX_TOKENS,
    ):
        assert value > 4096


def test_reconcile_budget_has_retry_headroom_below_ceiling():
    """The reconcile per-call budget must sit BELOW the truncation-retry ceiling so
    the safety net can actually step up. Bug: budget == ceiling (16384 == 16384) →
    ``retry_on_truncation`` re-raised immediately (``bigger <= max_tokens``), so a
    one-off overflow on a rich two-CV merge could not be recovered."""
    from applire.constants import RECONCILE_MAX_TOKENS
    from applire.providers.llm.base import TRUNCATION_RETRY_CEILING

    # A realistic two-CV + JD reconcile needs real headroom over the old 16384.
    assert RECONCILE_MAX_TOKENS >= 32768
    # And the ceiling must be strictly above the budget, or the retry has no room.
    assert TRUNCATION_RETRY_CEILING > RECONCILE_MAX_TOKENS


@pytest.mark.asyncio
async def test_reconcile_engine_threads_raised_budget():
    """engine.reconcile must thread RECONCILE_MAX_TOKENS into aparse_json."""
    from applire.constants import RECONCILE_MAX_TOKENS
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.engine import reconcile

    spy = _BudgetSpyProvider({"ops": [], "ambiguities": []})
    await reconcile(MasterProfileData(), "new info", "cv_upload", spy)
    assert spy.parse_budgets == [RECONCILE_MAX_TOKENS]


@pytest.mark.asyncio
async def test_analyze_jd_threads_raised_budget():
    """analyze_jd must pass JD_ANALYSIS_MAX_TOKENS to the provider, not the 4096
    default that truncated a dense posting mid-JSON."""
    from applire.constants import JD_ANALYSIS_MAX_TOKENS
    from applire.services.job import analyze_jd

    spy = _BudgetSpyProvider({}, stop_after=True)

    db = AsyncMock()
    # No URL/hash dedup hit → both lookups return "not found".
    result_obj = MagicMock()
    result_obj.scalar_one_or_none.return_value = None
    db.execute.return_value = result_obj

    with pytest.raises(_Stop):
        await analyze_jd("a long job description", db=db, provider=spy)

    assert spy.parse_budgets == [JD_ANALYSIS_MAX_TOKENS]


@pytest.mark.asyncio
async def test_cluster_gaps_threads_raised_budget():
    from applire.constants import GAP_CLUSTERING_MAX_TOKENS
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.services.gap import cluster_gaps

    gap_analysis = MagicMock(spec=GapAnalysis)
    gap_analysis.category_b = ["Python basics"]
    gap_analysis.category_c = ["LLMs"]
    job = MagicMock(spec=JobAnalysis)
    job.required_skills = ["Python"]
    job.nice_to_have_skills = []

    spy = _BudgetSpyProvider([])
    db = MagicMock()
    db.commit = AsyncMock()

    with patch("applire.services.session.get_ui_language", new=AsyncMock(return_value="en")):
        await cluster_gaps(gap_analysis, job, spy, db)

    assert spy.parse_budgets == [GAP_CLUSTERING_MAX_TOKENS]


@pytest.mark.asyncio
async def test_gap_analysis_pass2_threads_raised_budget():
    from applire.constants import GAP_ANALYSIS_MAX_TOKENS
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.services.gap import _run_analysis

    job = MagicMock(spec=JobAnalysis)
    job.role_title = "Senior Engineer"
    job.required_skills = ["Python"]
    job.nice_to_have_skills = []
    job.keywords = []
    job.seniority_level = "Senior"
    job.company_culture_signals = []
    job.language_requirement = ""
    job.embedding = None

    profile = MagicMock(spec=MasterProfile)
    profile.profile_json = {"work_experience": []}
    profile.embedding = None

    spy = _BudgetSpyProvider({"classifications": []}, stop_after=True)
    db = AsyncMock()

    with pytest.raises(_Stop):
        await _run_analysis(job, profile, db, spy)

    assert spy.parse_budgets == [GAP_ANALYSIS_MAX_TOKENS]


# ---------------------------------------------------------------------------
# (b) General auto-retry-on-truncation safety net (base.retry_on_truncation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_on_truncation_retries_once_with_doubled_budget():
    """A one-off truncation is retried once with 2x the budget and then succeeds —
    no chain silently 500s on a freak truncation."""
    from applire.providers.llm.base import retry_on_truncation

    seen_budgets: list[int] = []

    async def attempt(budget: int) -> str:
        seen_budgets.append(budget)
        if len(seen_budgets) == 1:
            raise LLMTruncatedError("hit the budget")
        return "ok"

    result = await retry_on_truncation(attempt, max_tokens=4096, ceiling=16384)

    assert result == "ok"
    assert seen_budgets == [4096, 8192]  # doubled on the single retry


@pytest.mark.asyncio
async def test_retry_on_truncation_passes_through_when_first_attempt_succeeds():
    from applire.providers.llm.base import retry_on_truncation

    calls: list[int] = []

    async def attempt(budget: int) -> str:
        calls.append(budget)
        return "fine"

    assert await retry_on_truncation(attempt, max_tokens=4096) == "fine"
    assert calls == [4096]  # no retry when it didn't truncate


@pytest.mark.asyncio
async def test_retry_on_truncation_caps_doubled_budget_at_ceiling():
    from applire.providers.llm.base import retry_on_truncation

    seen_budgets: list[int] = []

    async def attempt(budget: int) -> str:
        seen_budgets.append(budget)
        if len(seen_budgets) == 1:
            raise LLMTruncatedError("hit the budget")
        return "ok"

    # 2 * 12288 = 24576, but the ceiling clamps it to 16384.
    await retry_on_truncation(attempt, max_tokens=12288, ceiling=16384)
    assert seen_budgets == [12288, 16384]


@pytest.mark.asyncio
async def test_retry_on_truncation_does_not_retry_at_ceiling():
    """Already at the ceiling → doubling can't help → raise immediately, don't loop."""
    from applire.providers.llm.base import retry_on_truncation

    calls: list[int] = []

    async def attempt(budget: int) -> str:
        calls.append(budget)
        raise LLMTruncatedError("hit the budget")

    with pytest.raises(LLMTruncatedError):
        await retry_on_truncation(attempt, max_tokens=16384, ceiling=16384)

    assert calls == [16384]  # no second attempt — never loops forever


@pytest.mark.asyncio
async def test_retry_on_truncation_reraises_when_retry_also_truncates():
    """An uncoverable truncation (truncates again on the larger budget) still raises
    LLMTruncatedError after exactly one retry."""
    from applire.providers.llm.base import retry_on_truncation

    calls: list[int] = []

    async def attempt(budget: int) -> str:
        calls.append(budget)
        raise LLMTruncatedError("still too small")

    with pytest.raises(LLMTruncatedError):
        await retry_on_truncation(attempt, max_tokens=4096, ceiling=16384)

    assert calls == [4096, 8192]  # one retry, then give up


# ---------------------------------------------------------------------------
# (b) End-to-end through the OpenRouter provider (the env provider): a single
# truncation is recovered, an uncoverable one still raises.
# ---------------------------------------------------------------------------


def _openai_response(content: str, finish_reason: str = "stop") -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return response


def _openrouter(monkeypatch, **kwargs):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openrouter_model", "google/gemini-3.5-flash")
    monkeypatch.setattr(cfg.settings, "openrouter_reasoning_effort", "", raising=False)
    monkeypatch.setattr(cfg.settings, "openrouter_disable_thinking", False, raising=False)
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openrouter import OpenRouterProvider
        return OpenRouterProvider(api_key="test-key", **kwargs)


@pytest.mark.asyncio
async def test_openrouter_aparse_json_recovers_from_single_truncation(monkeypatch):
    """First call truncates (finish=length), retry with a doubled budget succeeds —
    the chain does not 500."""
    provider = _openrouter(monkeypatch)
    provider._client.chat.completions.create = AsyncMock(side_effect=[
        _openai_response('{"partial": "tru', "length"),
        _openai_response('{"ok": true}', "stop"),
    ])

    result = await provider.aparse_json("generate", max_tokens=4096)

    assert result == {"ok": True}
    calls = provider._client.chat.completions.create.call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["max_tokens"] == 4096
    assert calls[1].kwargs["max_tokens"] == 8192  # doubled retry


@pytest.mark.asyncio
async def test_openrouter_aparse_json_still_raises_on_uncoverable_truncation(monkeypatch):
    """Truncates on both the original and the doubled budget → LLMTruncatedError,
    after exactly one retry (never loops)."""
    provider = _openrouter(monkeypatch)
    provider._client.chat.completions.create = AsyncMock(
        return_value=_openai_response('{"still": "tru', "length")
    )

    with pytest.raises(LLMTruncatedError):
        await provider.aparse_json("generate", max_tokens=4096)

    assert provider._client.chat.completions.create.call_count == 2
