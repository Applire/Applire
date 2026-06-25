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

"""Provider truncation guard + per-call reasoning toggle (ADR-009 amendment, F-B).

Thinking models (e.g. gemini-3.5-flash) spend the token budget on reasoning tokens,
silently truncating short generations. These tests pin two behaviours:
  1. Any provider stop reason meaning "hit the budget" raises LLMTruncatedError —
     so a half-generated CV/cover letter/question fails loud, never persists.
  2. disable_thinking is a per-call override (OpenRouter), defaulting to the
     provider's configured behaviour so serious generations keep thinking ON.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.exceptions import LLMTruncatedError


def _openai_response(content: str, finish_reason: str = "stop") -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return response


# ---------------------------------------------------------------------------
# The shared helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["length", "max_tokens"])
def test_raise_if_truncated_raises_on_budget_reasons(reason):
    from applire.providers.llm.base import raise_if_truncated

    with pytest.raises(LLMTruncatedError):
        raise_if_truncated(reason, model="m")


@pytest.mark.parametrize("reason", ["stop", "end_turn", None, MagicMock()])
def test_raise_if_truncated_noop_on_normal_or_unset(reason):
    from applire.providers.llm.base import raise_if_truncated

    raise_if_truncated(reason, model="m")  # must not raise


# ---------------------------------------------------------------------------
# OpenRouter — truncation guard on both call paths
# ---------------------------------------------------------------------------


def _openrouter(monkeypatch, **kwargs):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openrouter_model", "google/gemini-3.5-flash")
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openrouter import OpenRouterProvider
        return OpenRouterProvider(api_key="test-key", **kwargs)


@pytest.mark.asyncio
async def test_openrouter_acomplete_raises_on_truncation(monkeypatch):
    provider = _openrouter(monkeypatch)
    provider._client.chat.completions.create = AsyncMock(
        return_value=_openai_response("Are you still working as the Engineering", "length")
    )
    with pytest.raises(LLMTruncatedError):
        await provider.acomplete("ask a question", max_tokens=256)


@pytest.mark.asyncio
async def test_openrouter_aparse_json_raises_on_truncation(monkeypatch):
    provider = _openrouter(monkeypatch)
    provider._client.chat.completions.create = AsyncMock(
        return_value=_openai_response('{"summary": "half a c', "length")
    )
    with pytest.raises(LLMTruncatedError):
        await provider.aparse_json("generate cv", max_tokens=8192)


@pytest.mark.asyncio
async def test_openrouter_complete_passes_when_not_truncated(monkeypatch):
    provider = _openrouter(monkeypatch)
    provider._client.chat.completions.create = AsyncMock(
        return_value=_openai_response("a complete question?", "stop")
    )
    assert await provider.acomplete("ask") == "a complete question?"


# ---------------------------------------------------------------------------
# OpenRouter — per-call reasoning toggle
# ---------------------------------------------------------------------------


def _extra_body_of(create_mock) -> dict | None:
    return create_mock.call_args.kwargs.get("extra_body")


@pytest.mark.asyncio
async def test_disable_thinking_per_call_emits_reasoning_off(monkeypatch):
    provider = _openrouter(monkeypatch)  # default: thinking on (no reasoning block)
    provider._client.chat.completions.create = AsyncMock(
        return_value=_openai_response("ok", "stop")
    )
    await provider.acomplete("ask", disable_thinking=True)
    assert _extra_body_of(provider._client.chat.completions.create) == {
        "reasoning": {"enabled": False}
    }


@pytest.mark.asyncio
async def test_default_call_leaves_thinking_on(monkeypatch):
    provider = _openrouter(monkeypatch)  # construction default = thinking on
    provider._client.chat.completions.create = AsyncMock(
        return_value=_openai_response("ok", "stop")
    )
    await provider.acomplete("ask")  # no disable_thinking
    assert _extra_body_of(provider._client.chat.completions.create) is None


@pytest.mark.asyncio
async def test_mode_c_question_gen_disables_thinking_and_raises_budget():
    """Interview question generation (chrome) must suppress reasoning and pass a
    budget above the old 256 so a thinking model finishes the sentence (F-B)."""
    from applire.constants import INTERVIEW_QUESTION_MAX_TOKENS
    from applire.services.interview_graph import question_generator_with_profile

    calls: list[dict] = []

    class SpyProvider:
        async def acomplete(self, prompt, *, system=None, temperature=0.3,
                            max_tokens=4096, disable_thinking=None):
            calls.append({"max_tokens": max_tokens, "disable_thinking": disable_thinking})
            return "Could you share a specific achievement as Team Lead at Acme?"

        async def aparse_json(self, prompt, *, system=None, temperature=0.1,
                             max_tokens=4096, disable_thinking=None):
            return {"approved": True, "issues": [], "feedback": ""}

    state = {
        "mode": "profile_enrich",
        "critical_gaps": ["achievements: Team Lead @ Acme"],
        "current_gap_index": 0,
        "messages": [],
    }
    profile = {"work_experience": [{"role": "Team Lead", "company": "Acme"}]}
    await question_generator_with_profile(state, profile, SpyProvider(), lang="en")

    assert calls, "question generator did not call acomplete"
    assert calls[0]["disable_thinking"] is True
    assert calls[0]["max_tokens"] == INTERVIEW_QUESTION_MAX_TOKENS
    assert INTERVIEW_QUESTION_MAX_TOKENS > 256


@pytest.mark.asyncio
async def test_per_call_override_beats_construction_default(monkeypatch):
    # Provider constructed with thinking disabled globally...
    provider = _openrouter(monkeypatch, disable_thinking=True)
    provider._client.chat.completions.create = AsyncMock(
        return_value=_openai_response("ok", "stop")
    )
    # ...but a single call re-enables it.
    await provider.acomplete("ask", disable_thinking=False)
    assert _extra_body_of(provider._client.chat.completions.create) is None
