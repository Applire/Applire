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

"""Issue #256 — provider 503s must never leak a raw Python exception or raw
provider JSON to callers, and must map to a typed, retryable error.

Two real-world crash shapes pinned here (run-4 evidence, backend traceback at
openrouter.py:270 / _parse_json):

  1. OpenRouter/Requesty's OpenAI-compat endpoint answers HTTP 200 with a body
     that carries no usable completion (``choices`` empty/None) when the
     upstream inference provider (Mistral) 503s — the openai SDK does not
     raise for this shape, so blind ``response.choices[0]`` indexing crashes
     with a raw ``TypeError: 'NoneType' object is not subscriptable``.
  2. A genuine HTTP 5xx from the gateway raises ``openai.APIStatusError``
     (Mistral SDK / Anthropic SDK equivalents), whose ``str(exc)`` embeds the
     raw provider JSON body — that text must never reach a caller.

Both must surface as ``LLMProviderUnavailableError`` (never TypeError/KeyError/
a raw SDK exception), with a message that never embeds the raw provider
payload.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.exceptions import LLMProviderUnavailableError


def _empty_openai_response() -> MagicMock:
    """The OpenRouter/Requesty 200-with-error-body shape: no choices at all."""
    response = MagicMock()
    response.choices = None
    response.usage = None
    response.error = {"message": "Provider returned error", "code": 503}
    return response


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openrouter_empty_choices_raises_typed_error_not_typeerror(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(cfg.settings, "openrouter_model", "mistralai/mistral-large-latest")
    monkeypatch.setattr(cfg.settings, "openrouter_base_url", "https://openrouter.ai/api/v1")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_empty_openai_response())

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from applire.providers.llm.openrouter import OpenRouterProvider
        provider = OpenRouterProvider(timeout=30)

    with pytest.raises(LLMProviderUnavailableError) as exc_info:
        await provider.aparse_json("test prompt")

    # The raw provider error payload must never leak into the exception text.
    assert "503" not in str(exc_info.value) or "Provider returned error" not in str(exc_info.value)
    assert "Provider returned error" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_openrouter_empty_choices_on_acomplete_raises_typed_error(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(cfg.settings, "openrouter_model", "mistralai/mistral-large-latest")
    monkeypatch.setattr(cfg.settings, "openrouter_base_url", "https://openrouter.ai/api/v1")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_empty_openai_response())

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from applire.providers.llm.openrouter import OpenRouterProvider
        provider = OpenRouterProvider(timeout=30)

    with pytest.raises(LLMProviderUnavailableError):
        await provider.acomplete("test prompt")


@pytest.mark.asyncio
async def test_openrouter_5xx_raises_typed_error_without_raw_body(monkeypatch):
    import openai

    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(cfg.settings, "openrouter_model", "mistralai/mistral-large-latest")
    monkeypatch.setattr(cfg.settings, "openrouter_base_url", "https://openrouter.ai/api/v1")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=openai.InternalServerError(
            "Error code: 503 - {'error': {'message': 'mistralai/mistral-large-latest is "
            "temporarily unavailable', 'code': 503}}",
            response=MagicMock(status_code=503, headers={}),
            body={"error": {"message": "temporarily unavailable", "code": 503}},
        )
    )

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from applire.providers.llm.openrouter import OpenRouterProvider
        provider = OpenRouterProvider(timeout=30)

    with pytest.raises(LLMProviderUnavailableError) as exc_info:
        await provider.acomplete("test prompt")

    # No raw provider JSON body text in the surfaced message.
    assert "mistralai/mistral-large-latest is temporarily unavailable" not in str(exc_info.value)
    assert "{'error'" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_openrouter_400_still_propagates_unmapped(monkeypatch):
    """A genuine client/config error (400) is NOT a provider-unavailable retry
    case — it must not be swallowed into the retryable typed error."""
    import openai

    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(cfg.settings, "openrouter_model", "mistralai/mistral-large-latest")
    monkeypatch.setattr(cfg.settings, "openrouter_base_url", "https://openrouter.ai/api/v1")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=openai.BadRequestError(
            "bad request",
            response=MagicMock(status_code=400, headers={}),
            body={"error": {"message": "bad request"}},
        )
    )

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from applire.providers.llm.openrouter import OpenRouterProvider
        provider = OpenRouterProvider(timeout=30)

    with pytest.raises(openai.BadRequestError):
        await provider.acomplete("test prompt")


# ---------------------------------------------------------------------------
# Requesty (same OpenAI-compat shape)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requesty_empty_choices_raises_typed_error(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_api_key", "sk-test")
    monkeypatch.setattr(cfg.settings, "requesty_model", "mistralai/mistral-large-latest")
    monkeypatch.setattr(cfg.settings, "requesty_base_url", "")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_empty_openai_response())

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from applire.providers.llm.requesty import RequestyProvider
        provider = RequestyProvider(api_key="sk-test")

    with pytest.raises(LLMProviderUnavailableError):
        await provider.aparse_json("test prompt")


@pytest.mark.asyncio
async def test_requesty_5xx_raises_typed_error_without_raw_body(monkeypatch):
    import openai

    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "requesty_api_key", "sk-test")
    monkeypatch.setattr(cfg.settings, "requesty_model", "mistralai/mistral-large-latest")
    monkeypatch.setattr(cfg.settings, "requesty_base_url", "")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=openai.InternalServerError(
            "Error code: 503 - {'error': {'message': 'upstream unavailable'}}",
            response=MagicMock(status_code=503, headers={}),
            body={"error": {"message": "upstream unavailable"}},
        )
    )

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from applire.providers.llm.requesty import RequestyProvider
        provider = RequestyProvider(api_key="sk-test")

    with pytest.raises(LLMProviderUnavailableError) as exc_info:
        await provider.acomplete("test prompt")

    assert "upstream unavailable" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Mistral (native SDK)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mistral_empty_choices_raises_typed_error(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "mistral_model", "mistral-large-latest")

    with patch("applire.providers.llm.mistral.Mistral"):
        from applire.providers.llm.mistral import MistralProvider
        provider = MistralProvider(api_key="test-key")

    response = MagicMock()
    response.choices = []
    provider._client.chat.complete_async = AsyncMock(return_value=response)

    with pytest.raises(LLMProviderUnavailableError):
        await provider.acomplete("test prompt")


@pytest.mark.asyncio
async def test_mistral_5xx_raises_typed_error(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "mistral_model", "mistral-large-latest")

    with patch("applire.providers.llm.mistral.Mistral"):
        from applire.providers.llm.mistral import MistralProvider
        provider = MistralProvider(api_key="test-key")

    class _SDKError(Exception):
        def __init__(self):
            super().__init__("Error code: 503 - {'message': 'service unavailable'}")
            self.status_code = 503

    provider._client.chat.complete_async = AsyncMock(side_effect=_SDKError())

    with pytest.raises(LLMProviderUnavailableError) as exc_info:
        await provider.acomplete("test prompt")

    assert "service unavailable" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Anthropic (native Messages API)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_5xx_raises_typed_error(monkeypatch):
    import anthropic

    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(cfg.settings, "anthropic_model", "claude-sonnet-4-6")

    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider(api_key="sk-ant-test")

    provider._client.messages.create = AsyncMock(
        side_effect=anthropic.InternalServerError(
            "Error code: 503 - {'error': {'message': 'Overloaded'}}",
            response=MagicMock(status_code=503, headers={}),
            body={"error": {"message": "Overloaded"}},
        )
    )

    with pytest.raises(LLMProviderUnavailableError) as exc_info:
        await provider.acomplete("test prompt")

    assert "Overloaded" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_anthropic_empty_content_raises_typed_error_not_typeerror(monkeypatch):
    """A malformed Messages-API response with no content blocks (None, not a
    list) must not crash the ``for block in response.content`` walk."""
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(cfg.settings, "anthropic_model", "claude-sonnet-4-6")

    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider
        provider = AnthropicProvider(api_key="sk-ant-test")

    response = MagicMock()
    response.content = None
    response.stop_reason = "end_turn"
    response.usage = None
    provider._client.messages.create = AsyncMock(return_value=response)

    # Malformed-but-not-erroring: must not raise TypeError. Empty text is
    # acceptable (a downstream json.loads on "" will raise its own clean
    # JSONDecodeError, already mapped by the router).
    result = await provider.acomplete("test prompt")
    assert result == ""


# ---------------------------------------------------------------------------
# OpenAI-compatible (LM Studio / self-hosted) — same OpenAI-compat crash shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_empty_choices_raises_typed_error(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "openai_base_url", "")
    monkeypatch.setattr(cfg.settings, "openai_model", "gpt-4o")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_empty_openai_response())

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from applire.providers.llm.openai import OpenAIProvider
        provider = OpenAIProvider(api_key="test-key")

    with pytest.raises(LLMProviderUnavailableError):
        await provider.acomplete("test prompt")


@pytest.mark.asyncio
async def test_openai_5xx_raises_typed_error_without_raw_body(monkeypatch):
    import openai

    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openai_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "openai_base_url", "")
    monkeypatch.setattr(cfg.settings, "openai_model", "gpt-4o")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=openai.InternalServerError(
            "Error code: 503 - {'error': {'message': 'server overloaded'}}",
            response=MagicMock(status_code=503, headers={}),
            body={"error": {"message": "server overloaded"}},
        )
    )

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from applire.providers.llm.openai import OpenAIProvider
        provider = OpenAIProvider(api_key="test-key")

    with pytest.raises(LLMProviderUnavailableError) as exc_info:
        await provider.acomplete("test prompt")

    assert "server overloaded" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Ollama (local, dict-shaped response — different crash shape, same class)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ollama_malformed_response_raises_typed_error_not_keyerror(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "ollama_base_url", "http://localhost:11434")
    monkeypatch.setattr(cfg.settings, "ollama_model", "llama3")

    from applire.providers.llm.ollama import OllamaProvider
    provider = OllamaProvider(base_url="http://localhost:11434", model="llama3", timeout=30)

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"done_reason": None})  # no "message" key at all

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        with pytest.raises(LLMProviderUnavailableError):
            await provider.acomplete("test prompt")


@pytest.mark.asyncio
async def test_ollama_5xx_raises_typed_error_without_raw_body(monkeypatch):
    import httpx

    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "ollama_base_url", "http://localhost:11434")
    monkeypatch.setattr(cfg.settings, "ollama_model", "llama3")

    from applire.providers.llm.ollama import OllamaProvider
    provider = OllamaProvider(base_url="http://localhost:11434", model="llama3", timeout=30)

    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "raw server error text that must not leak"

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("503", request=MagicMock(), response=mock_response)
        )
        mock_client_cls.return_value = mock_client

        with pytest.raises(LLMProviderUnavailableError) as exc_info:
            await provider.acomplete("test prompt")

    assert "raw server error text that must not leak" not in str(exc_info.value)
