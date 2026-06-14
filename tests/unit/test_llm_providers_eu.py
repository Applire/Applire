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

"""Unit tests for the EU/frontier LLM providers added in the LLM-Providers sprint
(ADR-009 amended 2026-06-14): Requesty (EU-hosted OpenAI-compat gateway) and
Anthropic (native Messages API). All tests mock the SDK clients — no real API calls.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _openai_response(content: str) -> MagicMock:
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return response


# ===========================================================================
# Requesty — OpenAI-compatible gateway, EU-residency default (US151)
# ===========================================================================


def test_factory_returns_requesty_provider(monkeypatch):
    import applire.config as cfg
    from applire.providers import get_provider
    from applire.providers.llm.requesty import RequestyProvider

    monkeypatch.setattr(cfg.settings, "llm_provider", "requesty")
    monkeypatch.setattr(cfg.settings, "requesty_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "requesty_model", "mistralai/mistral-large-latest")
    monkeypatch.setattr(cfg.settings, "requesty_base_url", "")
    with patch("openai.AsyncOpenAI"):
        provider = get_provider()
    assert isinstance(provider, RequestyProvider)


def test_requesty_defaults_to_eu_base_url(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "requesty_model", "mistralai/mistral-large-latest")
    monkeypatch.setattr(cfg.settings, "requesty_base_url", "")
    with patch("openai.AsyncOpenAI") as mock_cls:
        from applire.providers.llm.requesty import RequestyProvider
        RequestyProvider()
    assert mock_cls.call_args.kwargs["base_url"] == "https://router.eu.requesty.ai/v1"


def test_requesty_base_url_override(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "requesty_model", "x")
    monkeypatch.setattr(cfg.settings, "requesty_base_url", "https://router.requesty.ai/v1")
    with patch("openai.AsyncOpenAI") as mock_cls:
        from applire.providers.llm.requesty import RequestyProvider
        RequestyProvider()
    assert mock_cls.call_args.kwargs["base_url"] == "https://router.requesty.ai/v1"


@pytest.mark.asyncio
async def test_requesty_acomplete_returns_content(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_model", "m")
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider
        provider = RequestyProvider(api_key="test-key")
    provider._client.chat.completions.create = AsyncMock(return_value=_openai_response("hi EU"))
    result = await provider.acomplete("Say hi", system="be terse")
    assert result == "hi EU"
    msgs = provider._client.chat.completions.create.call_args.kwargs["messages"]
    assert msgs[0] == {"role": "system", "content": "be terse"}
    assert msgs[1] == {"role": "user", "content": "Say hi"}


@pytest.mark.asyncio
async def test_requesty_aparse_json_returns_dict_and_requests_json(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_model", "m")
    payload = {"skills": ["Python"], "umlaut": "Fähigkeit"}
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider
        provider = RequestyProvider(api_key="test-key")
    provider._client.chat.completions.create = AsyncMock(
        return_value=_openai_response(json.dumps(payload, ensure_ascii=False))
    )
    result = await provider.aparse_json("parse")
    assert result == payload
    assert provider._client.chat.completions.create.call_args.kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_requesty_rate_limit_maps_to_llm_error(monkeypatch):
    import openai
    from applire.exceptions import LLMRateLimitError
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_model", "m")
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider
        provider = RequestyProvider(api_key="test-key")
    err = openai.RateLimitError("rate", response=MagicMock(), body=None)
    provider._client.chat.completions.create = AsyncMock(side_effect=err)
    with pytest.raises(LLMRateLimitError):
        await provider.acomplete("x")


# ===========================================================================
# Anthropic — native Messages API, BYO-API-key (US150)
# ===========================================================================


def _anthropic_response(text: str) -> MagicMock:
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    resp.usage = MagicMock(input_tokens=1, output_tokens=1)
    return resp


def test_factory_returns_anthropic_provider(monkeypatch):
    import applire.config as cfg
    from applire.providers import get_provider
    from applire.providers.llm.anthropic import AnthropicProvider

    monkeypatch.setattr(cfg.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(cfg.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "anthropic_model", "claude-sonnet-4-6")
    with patch("anthropic.AsyncAnthropic"):
        provider = get_provider()
    assert isinstance(provider, AnthropicProvider)


@pytest.mark.asyncio
async def test_anthropic_acomplete_passes_system_and_max_tokens(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "anthropic_model", "claude-sonnet-4-6")
    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider(api_key="test-key")
    provider._client.messages.create = AsyncMock(return_value=_anthropic_response("hi"))
    result = await provider.acomplete("Say hi", system="be terse", max_tokens=1234)
    assert result == "hi"
    kw = provider._client.messages.create.call_args.kwargs
    assert kw["system"] == "be terse"
    assert kw["max_tokens"] == 1234           # Anthropic requires max_tokens
    assert kw["messages"] == [{"role": "user", "content": "Say hi"}]


@pytest.mark.asyncio
async def test_anthropic_acomplete_omits_system_when_none(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "anthropic_model", "m")
    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider(api_key="test-key")
    provider._client.messages.create = AsyncMock(return_value=_anthropic_response("pong"))
    await provider.acomplete("ping")
    assert "system" not in provider._client.messages.create.call_args.kwargs


@pytest.mark.asyncio
async def test_anthropic_aparse_json_prefills_and_parses(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "anthropic_model", "m")
    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider(api_key="test-key")
    # The model continues from the prefilled "{"
    provider._client.messages.create = AsyncMock(
        return_value=_anthropic_response('"skills": ["Python"], "u": "Fähigkeit"}')
    )
    result = await provider.aparse_json("parse")
    assert result == {"skills": ["Python"], "u": "Fähigkeit"}
    msgs = provider._client.messages.create.call_args.kwargs["messages"]
    assert msgs[-1] == {"role": "assistant", "content": "{"}


@pytest.mark.asyncio
async def test_anthropic_aparse_json_tolerates_trailing_fence(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "anthropic_model", "m")
    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider(api_key="test-key")
    provider._client.messages.create = AsyncMock(
        return_value=_anthropic_response('"ok": true}\n```')
    )
    assert await provider.aparse_json("go") == {"ok": True}


@pytest.mark.asyncio
async def test_anthropic_rate_limit_maps_to_llm_error(monkeypatch):
    import anthropic
    import httpx
    from applire.exceptions import LLMRateLimitError
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "anthropic_model", "m")
    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider(api_key="test-key")
    err = anthropic.RateLimitError(
        "rate", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None
    )
    provider._client.messages.create = AsyncMock(side_effect=err)
    with pytest.raises(LLMRateLimitError):
        await provider.acomplete("x")
