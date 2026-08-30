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

"""LLM provider factory — ADR 009.

Controlled by the LLM_PROVIDER environment variable:
  mistral     — Mistral AI (EU-hosted, default)
  requesty    — Requesty EU-hosted gateway (Frankfurt, zero-retention)
  openrouter  — OpenRouter multi-model gateway (uses openai SDK)
  anthropic   — Anthropic Claude, native Messages API (BYO Console key)
  openai      — OpenAI or any OpenAI-compatible server (LM Studio, etc.)
  ollama      — Ollama local server (zero cloud dependencies)

Keep this list, the ValueError below, and .env.example in sync — a provider
documented in .env.example but absent here is the failure a self-hoster sees.
"""

from applire.config import settings
from applire.providers.llm.base import LLMProvider


def get_provider() -> LLMProvider:
    """Instantiate the configured LLM provider (wrapped with debug logging if enabled)."""
    from applire.providers.llm.debug_log import wrap_provider

    return wrap_provider(_build_provider(settings.llm_provider.lower()))


def _build_provider(provider: str) -> LLMProvider:
    if provider == "mistral":
        from applire.providers.llm.mistral import MistralProvider
        return MistralProvider(timeout=settings.llm_timeout)

    if provider == "openrouter":
        from applire.providers.llm.openrouter import OpenRouterProvider
        return OpenRouterProvider(timeout=settings.llm_timeout)

    if provider == "requesty":
        from applire.providers.llm.requesty import RequestyProvider
        return RequestyProvider(timeout=settings.llm_timeout)

    if provider == "anthropic":
        from applire.providers.llm.anthropic import AnthropicProvider
        return AnthropicProvider(timeout=settings.llm_timeout)

    if provider == "openai":
        from applire.providers.llm.openai import OpenAIProvider
        return OpenAIProvider(timeout=settings.llm_timeout)

    if provider == "ollama":
        from applire.providers.llm.ollama import OllamaProvider
        return OllamaProvider(timeout=settings.llm_timeout)

    if provider == "mock":
        from applire.providers.llm.mock import MockLLMProvider
        return MockLLMProvider()

    raise ValueError(
        f"Unknown LLM_PROVIDER '{settings.llm_provider}'. "
        "Valid values: mistral, openrouter, requesty, anthropic, openai, ollama"
    )
