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
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import openai
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
    from applire.providers.llm import unwrap_provider
    from applire.providers.llm.requesty import RequestyProvider

    monkeypatch.setattr(cfg.settings, "llm_provider", "requesty")
    monkeypatch.setattr(cfg.settings, "llm_debug_log", False)  # selection test: no logging wrapper
    monkeypatch.setattr(cfg.settings, "requesty_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "requesty_model", "mistralai/mistral-large-latest")
    monkeypatch.setattr(cfg.settings, "requesty_base_url", "")
    with patch("openai.AsyncOpenAI"):
        provider = get_provider()
    assert isinstance(unwrap_provider(provider), RequestyProvider)


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


# ---------------------------------------------------------------------------
# Requesty — reasoning controls: top-level reasoning_effort + 400-fallback (#179)
# ---------------------------------------------------------------------------


class _CaptureClient:
    def __init__(self, response):
        self.calls = []
        self._response = response
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _ok_response(text="hi"):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=text))],
        usage=None,
    )


def _fake_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://router.eu.requesty.ai/v1/chat/completions")
    return httpx.Response(status_code, request=request)


@pytest.mark.asyncio
async def test_disable_thinking_sends_reasoning_effort_none(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_reasoning_effort", "", raising=False)
    monkeypatch.setattr(cfg.settings, "requesty_disable_thinking", False, raising=False)
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider
        p = RequestyProvider(api_key="k", model="m")
    p._client = _CaptureClient(_ok_response())
    await p.acomplete("q", disable_thinking=True)
    assert p._client.calls[0]["extra_body"] == {"reasoning_effort": "none"}


@pytest.mark.asyncio
async def test_configured_effort_sent_when_thinking_enabled(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_reasoning_effort", "", raising=False)
    monkeypatch.setattr(cfg.settings, "requesty_disable_thinking", False, raising=False)
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider
        p = RequestyProvider(api_key="k", model="m", reasoning_effort="low")
    p._client = _CaptureClient(_ok_response())
    await p.acomplete("q")
    assert p._client.calls[0]["extra_body"] == {"reasoning_effort": "low"}


@pytest.mark.asyncio
async def test_no_extra_body_by_default(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_reasoning_effort", "", raising=False)
    monkeypatch.setattr(cfg.settings, "requesty_disable_thinking", False, raising=False)
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider
        p = RequestyProvider(api_key="k", model="m")
    p._client = _CaptureClient(_ok_response())
    await p.acomplete("q")
    assert p._client.calls[0].get("extra_body") is None


@pytest.mark.asyncio
async def test_rejected_reasoning_effort_retries_without_it_and_floors_budget(monkeypatch):
    """A model/router that 400s on reasoning_effort must still work (mirrors OpenRouter)."""
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_reasoning_effort", "", raising=False)
    monkeypatch.setattr(cfg.settings, "requesty_disable_thinking", False, raising=False)
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider
        p = RequestyProvider(api_key="k", model="m")
    client = _CaptureClient(_ok_response())
    first = {"n": 0}
    real_create = client._create

    async def flaky(**kwargs):
        if first["n"] == 0:
            first["n"] += 1
            raise openai.BadRequestError(
                message="reasoning_effort is not supported", response=_fake_response(400), body=None
            )
        return await real_create(**kwargs)

    client.chat.completions.create = flaky
    p._client = client
    await p.acomplete("q", disable_thinking=True, max_tokens=512)
    assert client.calls[0].get("extra_body") is None          # retry dropped the field
    assert client.calls[0]["max_tokens"] >= 4096               # budget floored


@pytest.mark.asyncio
async def test_reasoning_rejection_is_cached_and_not_re_sent(monkeypatch):
    """#181: after one 400 on reasoning_effort, later calls strip it upfront rather
    than repeating the wasted reject-then-retry round-trip."""
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_reasoning_effort", "", raising=False)
    monkeypatch.setattr(cfg.settings, "requesty_disable_thinking", False, raising=False)
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider
        p = RequestyProvider(api_key="k", model="m")

    attempts = []

    async def always_reject_reasoning(**kwargs):
        attempts.append(kwargs)
        if (kwargs.get("extra_body") or {}).get("reasoning_effort"):
            raise openai.BadRequestError(
                message="reasoning_effort is not supported",
                response=_fake_response(400), body=None,
            )
        return _ok_response()

    p._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=always_reject_reasoning))
    )

    await p.acomplete("q", disable_thinking=True, max_tokens=512)
    assert len(attempts) == 2                      # rejected once, then stripped retry
    assert p._reasoning_rejected is True

    attempts.clear()
    await p.acomplete("q", disable_thinking=True, max_tokens=512)
    assert len(attempts) == 1                      # cached: stripped upfront, no reject
    assert attempts[0].get("extra_body") is None
    assert attempts[0]["max_tokens"] >= 4096       # budget still floored on the cached path


@pytest.mark.asyncio
async def test_unrelated_400_does_not_latch_reasoning_rejection(monkeypatch):
    """#181 (F1): a 400 raised for a reason OTHER than reasoning_effort must not
    permanently disable reasoning control — only a reasoning-specific rejection latches."""
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_reasoning_effort", "", raising=False)
    monkeypatch.setattr(cfg.settings, "requesty_disable_thinking", False, raising=False)
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider
        p = RequestyProvider(api_key="k", model="m")

    async def context_length_400(**kwargs):
        raise openai.BadRequestError(
            message="This model's maximum context length is 8192 tokens",
            response=_fake_response(400), body=None,
        )

    p._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=context_length_400))
    )
    from applire.exceptions import LLMProviderError

    with pytest.raises(LLMProviderError):  # error detail hardening: 4xx leaves typed
        await p.acomplete("q", disable_thinking=True, max_tokens=512)
    assert p._reasoning_rejected is False          # unrelated 400 must NOT latch


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
    from applire.providers.llm import unwrap_provider
    from applire.providers.llm.anthropic import AnthropicProvider

    monkeypatch.setattr(cfg.settings, "llm_provider", "anthropic")
    monkeypatch.setattr(cfg.settings, "llm_debug_log", False)  # selection test: no logging wrapper
    monkeypatch.setattr(cfg.settings, "anthropic_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "anthropic_model", "claude-sonnet-4-6")
    with patch("anthropic.AsyncAnthropic"):
        provider = get_provider()
    assert isinstance(unwrap_provider(provider), AnthropicProvider)


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
    assert kw["system"] == [
        {"type": "text", "text": "be terse", "cache_control": {"type": "ephemeral"}}
    ]
    assert kw["max_tokens"] == 1234           # Anthropic requires max_tokens
    assert kw["messages"] == [{"role": "user", "content": "Say hi"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["acomplete", "aparse_json"])
@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-haiku-4-5"])
async def test_anthropic_caches_the_system_prompt_only(monkeypatch, entry, model):
    # ADR-009 amended 2026-11-06: one breakpoint at the end of the system prompt;
    # the per-call user prompt (and a prefill) is never marked, and no top-level
    # automatic cache_control would move the breakpoint behind it.
    provider = _anthropic_provider(monkeypatch, model)
    reply = '"a": 1}' if provider._shape.prefill else '{"a": 1}'  # haiku-4-5 continues a "{"
    provider._client.messages.create = AsyncMock(return_value=_anthropic_response(reply))
    await getattr(provider, entry)("the varying task", system="the stable rules")
    kw = provider._client.messages.create.call_args.kwargs
    assert kw["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in kw
    assert "cache_control" not in str(kw["messages"])


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


def _anthropic_provider(monkeypatch, model: str):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "anthropic_model", model)
    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider
        return AnthropicProvider(api_key="test-key")


@pytest.mark.asyncio
async def test_anthropic_aparse_json_prefills_and_parses_on_a_prefill_model(monkeypatch):
    # Claude Haiku 4.5 still accepts an assistant prefill: the model continues "{".
    provider = _anthropic_provider(monkeypatch, "claude-haiku-4-5")
    provider._client.messages.create = AsyncMock(
        return_value=_anthropic_response('"skills": ["Python"], "u": "Fähigkeit"}')
    )
    result = await provider.aparse_json("parse")
    assert result == {"skills": ["Python"], "u": "Fähigkeit"}
    msgs = provider._client.messages.create.call_args.kwargs["messages"]
    assert msgs[-1] == {"role": "assistant", "content": "{"}


@pytest.mark.asyncio
async def test_anthropic_aparse_json_tolerates_trailing_fence(monkeypatch):
    provider = _anthropic_provider(monkeypatch, "claude-haiku-4-5")
    provider._client.messages.create = AsyncMock(
        return_value=_anthropic_response('"ok": true}\n```')
    )
    assert await provider.aparse_json("go") == {"ok": True}


# WP-0 (2026-11-04): Claude Sonnet 5.5 answered every call with HTTP 400
# "`temperature` is deprecated for this model" (req_011CghhUPQYFwLNhFFfKkP9G); an
# assistant prefill and `thinking: {type: "disabled"}` are 400s there too.
_CURRENT_MODELS = ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5", "claude-fable-5-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("model", _CURRENT_MODELS + ["claude-unknown-9"])
async def test_anthropic_current_models_get_no_sampling_and_no_prefill(monkeypatch, model):
    provider = _anthropic_provider(monkeypatch, model)
    provider._client.messages.create = AsyncMock(
        return_value=_anthropic_response('```json\n{"ok": true}\n```')
    )
    assert await provider.aparse_json("go", temperature=0.1) == {"ok": True}
    kw = provider._client.messages.create.call_args.kwargs
    assert "temperature" not in kw and "top_p" not in kw and "top_k" not in kw
    assert kw["messages"] == [{"role": "user", "content": "go"}]   # ends on the user turn
    assert "thinking" not in kw                                     # model default when not asked

    provider._client.messages.create = AsyncMock(return_value=_anthropic_response("hi"))
    await provider.acomplete("x", temperature=0.3)
    assert "temperature" not in provider._client.messages.create.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-haiku-4-5", "claude-3-5-haiku-20241022"])
async def test_anthropic_older_models_keep_temperature(monkeypatch, model):
    provider = _anthropic_provider(monkeypatch, model)
    provider._client.messages.create = AsyncMock(return_value=_anthropic_response("hi"))
    await provider.acomplete("x", temperature=0.3)
    assert provider._client.messages.create.call_args.kwargs["temperature"] == 0.3


@pytest.mark.asyncio
async def test_anthropic_sonnet_4_6_takes_no_prefill(monkeypatch):
    # The 4.6 generation still samples at a set temperature but rejects a prefill.
    provider = _anthropic_provider(monkeypatch, "claude-sonnet-4-6")
    provider._client.messages.create = AsyncMock(return_value=_anthropic_response('{"a": 1}'))
    assert await provider.aparse_json("go") == {"a": 1}
    kw = provider._client.messages.create.call_args.kwargs
    assert kw["messages"] == [{"role": "user", "content": "go"}]
    assert kw["temperature"] == 0.1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,expected",
    [
        ("claude-sonnet-5-5", {"thinking": {"type": "between_tools"}}),
        ("anthropic.claude-sonnet-5-5", {"thinking": {"type": "between_tools"}}),
        ("claude-haiku-5-5", {"thinking": {"type": "disabled"}}),
        ("claude-opus-4-8", {"thinking": {"type": "disabled"}}),
        ("claude-opus-5-5", {"output_config": {"effort": "low"}}),
        ("claude-fable-5-1", {"output_config": {"effort": "low"}}),
        ("claude-sonnet-4-6", {}),
        ("claude-unknown-9", {}),
    ],
)
async def test_anthropic_disable_thinking_shape_per_model(monkeypatch, model, expected):
    provider = _anthropic_provider(monkeypatch, model)
    provider._client.messages.create = AsyncMock(return_value=_anthropic_response('{"a": 1}'))
    await provider.aparse_json("go", disable_thinking=True)
    kw = provider._client.messages.create.call_args.kwargs
    assert {k: kw[k] for k in ("thinking", "output_config") if k in kw} == expected
    if model.endswith("sonnet-5-5"):
        assert "disabled" not in str(kw)          # a 400 on Sonnet 5.5


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["acomplete", "aparse_json"])
async def test_anthropic_refusal_raises_instead_of_returning_empty_text(monkeypatch, entry):
    from applire.exceptions import LLMRefusalError
    provider = _anthropic_provider(monkeypatch, "claude-sonnet-5-5")
    resp = MagicMock()
    resp.content = []
    resp.stop_reason = "refusal"
    resp.stop_details = MagicMock(category="reasoning_extraction")
    resp.usage = MagicMock(input_tokens=9392, output_tokens=0)
    provider._client.messages.create = AsyncMock(return_value=resp)
    with pytest.raises(LLMRefusalError) as info:
        await getattr(provider, entry)("go")
    assert info.value.category == "reasoning_extraction"


def test_anthropic_client_carries_an_explicit_timeout():
    # Without it the SDK raises ValueError ("Streaming is required …") for any
    # max_tokens above ~21k — every reconcile call (32768) failed on Sonnet 5.5.
    from applire.providers.llm.anthropic import AnthropicProvider
    with patch("applire.providers.llm.anthropic.AsyncAnthropic") as client_cls:
        AnthropicProvider(api_key="k", model="claude-sonnet-5-5", timeout=120)
    assert client_cls.call_args.kwargs["timeout"] == 150.0


def test_anthropic_sdk_accepts_a_reconcile_budget_without_streaming():
    # The real SDK guard, not a mock: an explicit client timeout skips it.
    import anthropic
    from anthropic._constants import DEFAULT_TIMEOUT
    from applire.constants import RECONCILE_MAX_TOKENS
    bare = anthropic.AsyncAnthropic(api_key="k")
    assert bare.timeout == DEFAULT_TIMEOUT
    with pytest.raises(ValueError):
        bare._calculate_nonstreaming_timeout(RECONCILE_MAX_TOKENS, None)
    from applire.providers.llm.anthropic import AnthropicProvider
    provider = AnthropicProvider(api_key="k", model="claude-sonnet-5-5", timeout=120)
    assert provider._client.timeout != DEFAULT_TIMEOUT


def test_anthropic_generation_parses_dated_and_legacy_ids():
    from applire.providers.llm.anthropic import _generation
    assert _generation("claude-sonnet-4-20250514") == ("sonnet", (4, 0))
    assert _generation("claude-sonnet-4-5-20250929") == ("sonnet", (4, 5))
    assert _generation("claude-3-7-sonnet-20250219") == ("sonnet", (3, 7))
    assert _generation("claude-opus-4-5@20251101") == ("opus", (4, 5))
    assert _generation("gpt-6-luna") is None


def test_anthropic_generation_parses_bedrock_ids():
    from applire.providers.llm.anthropic import _generation
    assert _generation("anthropic.claude-sonnet-4-5-20250929-v1:0") == ("sonnet", (4, 5))
    assert _generation("us.anthropic.claude-3-5-sonnet-20241022-v2:0") == ("sonnet", (3, 5))
    assert _generation("eu.anthropic.claude-haiku-5-5") == ("haiku", (5, 5))
    assert _generation(" Claude-Sonnet-5-5 ") == ("sonnet", (5, 5))


def test_anthropic_default_model_is_current():
    from applire.config import Settings
    from applire.providers.llm.anthropic import _DEFAULT_MODEL
    assert Settings.model_fields["anthropic_model"].default == _DEFAULT_MODEL == "claude-sonnet-5-5"


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
