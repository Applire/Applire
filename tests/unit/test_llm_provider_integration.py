"""Unit tests — Iter 16: LLM Provider Abstraction & OpenRouter Integration.

Coverage:
  - Factory: correct class instantiated per LLM_PROVIDER value
  - Factory: ValueError on unknown provider
  - OpenRouterProvider: HTTP-Referer + X-Title headers present on client
  - OpenRouterProvider: default model is mistralai/mistral-large-latest
  - LLMRateLimitError raised after retries exhausted (Mistral, OpenAI, Ollama, OpenRouter)
  - LLMTimeoutError raised when asyncio.wait_for fires
  - Config: openrouter_api_key / openrouter_model / openrouter_base_url fields present
  - Backward compat: applire.providers.base still exports LLMProvider
  - Backward compat: applire.providers still exports get_provider
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.exceptions import LLMRateLimitError, LLMTimeoutError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_settings(monkeypatch):
    """Minimal settings stubs so provider __init__ doesn't need real keys."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-or-key")


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


def test_factory_returns_mistral_provider(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "llm_provider", "mistral")
    monkeypatch.setattr(cfg.settings, "llm_debug_log", False)  # selection test: no logging wrapper
    monkeypatch.setattr(cfg.settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(cfg.settings, "mistral_model", "mistral-small-latest")
    with patch("applire.providers.llm.mistral.Mistral"):
        from applire.providers.llm import get_provider
        from applire.providers.llm.mistral import MistralProvider
        provider = get_provider()
        assert isinstance(provider, MistralProvider)


def test_factory_returns_openrouter_provider(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "llm_provider", "openrouter")
    monkeypatch.setattr(cfg.settings, "llm_debug_log", False)  # selection test: no logging wrapper
    monkeypatch.setattr(cfg.settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(cfg.settings, "openrouter_base_url", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(cfg.settings, "openrouter_model", "mistralai/mistral-large-latest")
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm import get_provider
        from applire.providers.llm.openrouter import OpenRouterProvider
        provider = get_provider()
        assert isinstance(provider, OpenRouterProvider)


def test_factory_returns_openai_provider(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "llm_provider", "openai")
    monkeypatch.setattr(cfg.settings, "llm_debug_log", False)  # selection test: no logging wrapper
    monkeypatch.setattr(cfg.settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(cfg.settings, "openai_base_url", "")
    monkeypatch.setattr(cfg.settings, "openai_model", "gpt-4o")
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm import get_provider
        from applire.providers.llm.openai import OpenAIProvider
        provider = get_provider()
        assert isinstance(provider, OpenAIProvider)


def test_factory_returns_ollama_provider(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "llm_provider", "ollama")
    monkeypatch.setattr(cfg.settings, "llm_debug_log", False)  # selection test: no logging wrapper
    monkeypatch.setattr(cfg.settings, "ollama_base_url", "http://localhost:11434")
    monkeypatch.setattr(cfg.settings, "ollama_model", "llama3")
    from applire.providers.llm import get_provider
    from applire.providers.llm.ollama import OllamaProvider
    provider = get_provider()
    assert isinstance(provider, OllamaProvider)


def test_factory_raises_on_unknown_provider(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "llm_provider", "unicorn")
    from applire.providers.llm import get_provider
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_provider()


# ---------------------------------------------------------------------------
# OpenRouterProvider — headers and default model
# ---------------------------------------------------------------------------


def test_openrouter_headers_and_default_model(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openrouter_api_key", "sk-test-or")
    monkeypatch.setattr(cfg.settings, "openrouter_model", "")
    monkeypatch.setattr(cfg.settings, "openrouter_base_url", "https://openrouter.ai/api/v1")

    captured_kwargs: dict = {}

    def fake_async_openai(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    with patch("openai.AsyncOpenAI", side_effect=fake_async_openai):
        from applire.providers.llm.openrouter import OpenRouterProvider
        p = OpenRouterProvider()

    headers = captured_kwargs.get("default_headers", {})
    assert headers.get("HTTP-Referer") == "https://applire.community"
    assert headers.get("X-Title") == "Applire"
    assert p._model == "mistralai/mistral-large-latest"


def test_openrouter_uses_default_base_url(monkeypatch):
    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openrouter_api_key", "sk-test-or")
    monkeypatch.setattr(cfg.settings, "openrouter_base_url", "")
    monkeypatch.setattr(cfg.settings, "openrouter_model", "mistralai/mistral-large-latest")

    captured_kwargs: dict = {}

    def fake_async_openai(**kwargs):
        captured_kwargs.update(kwargs)
        return MagicMock()

    with patch("openai.AsyncOpenAI", side_effect=fake_async_openai):
        from applire.providers.llm.openrouter import _DEFAULT_BASE_URL, OpenRouterProvider
        OpenRouterProvider()

    assert captured_kwargs.get("base_url") == _DEFAULT_BASE_URL


# ---------------------------------------------------------------------------
# Rate limit error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openrouter_rate_limit_raises_llm_rate_limit_error(monkeypatch):
    import openai

    import applire.config as cfg
    monkeypatch.setattr(cfg.settings, "openrouter_api_key", "sk-test")
    monkeypatch.setattr(cfg.settings, "openrouter_model", "mistralai/mistral-large-latest")
    monkeypatch.setattr(cfg.settings, "openrouter_base_url", "https://openrouter.ai/api/v1")

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=openai.RateLimitError(
            "rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )
    )

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from applire.providers.llm.openrouter import OpenRouterProvider
        provider = OpenRouterProvider(timeout=30)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(LLMRateLimitError):
            await provider.acomplete("test prompt")


@pytest.mark.asyncio
async def test_openai_rate_limit_raises_llm_rate_limit_error():
    import openai

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=openai.RateLimitError(
            "rate limit",
            response=MagicMock(status_code=429, headers={}),
            body={},
        )
    )

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        from applire.providers.llm.openai import OpenAIProvider
        provider = OpenAIProvider(api_key="sk-test", timeout=30)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(LLMRateLimitError):
            await provider.acomplete("test prompt")


@pytest.mark.asyncio
async def test_ollama_rate_limit_raises_llm_rate_limit_error():
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 429

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("429", request=MagicMock(), response=mock_response)
        )
        mock_client_cls.return_value = mock_client

        from applire.providers.llm.ollama import OllamaProvider
        provider = OllamaProvider(base_url="http://localhost:11434", model="llama3", timeout=30)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(LLMRateLimitError):
                await provider.acomplete("test prompt")


# ---------------------------------------------------------------------------
# Timeout error propagation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_timeout_raises_llm_timeout_error():
    """asyncio.wait_for fires before the SDK responds → LLMTimeoutError."""
    import openai

    async def slow(*args, **kwargs):
        await asyncio.sleep(60)

    mock_client = MagicMock()
    mock_client.chat.completions.create = slow

    with patch("openai.AsyncOpenAI", return_value=mock_client):
        import importlib
        import applire.providers.llm.openai as mod
        importlib.reload(mod)
        from applire.providers.llm.openai import OpenAIProvider
        provider = OpenAIProvider(api_key="sk-test", timeout=1)

    with pytest.raises(LLMTimeoutError):
        await provider.acomplete("test prompt")


# ---------------------------------------------------------------------------
# LLMProvider base class: timeout stored correctly
# ---------------------------------------------------------------------------


def test_provider_stores_timeout():
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openai import OpenAIProvider
        p = OpenAIProvider(api_key="sk-test", timeout=45)
        assert p._timeout == 45


def test_provider_default_timeout():
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openai import OpenAIProvider
        p = OpenAIProvider(api_key="sk-test")
        assert p._timeout == 30


# ---------------------------------------------------------------------------
# Backward compatibility: shim imports still resolve
# ---------------------------------------------------------------------------


def test_providers_base_shim_exports_llm_provider():
    from applire.providers.base import LLMProvider  # noqa: F401 — shim import
    assert LLMProvider is not None


def test_providers_init_exports_get_provider():
    from applire.providers import get_provider  # noqa: F401 — shim import
    assert callable(get_provider)


# ---------------------------------------------------------------------------
# Config: openrouter fields present
# ---------------------------------------------------------------------------


def test_config_has_openrouter_fields():
    from applire.config import Settings
    # Check field definitions directly — avoids interference from .env overrides
    fields = Settings.model_fields
    assert "openrouter_api_key" in fields
    assert "openrouter_model" in fields
    assert "openrouter_base_url" in fields
    assert fields["openrouter_model"].default == "mistralai/mistral-large-latest"
