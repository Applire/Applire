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
    """Instantiate the configured LLM provider.

    Two transparent wrappers, innermost first:

    * ``wrap_usage`` (ADR-086 clause 7) writes one ``llm_usage`` row per call —
      token counts, a stage label and opaque ids, never any text. On by default;
      ``LLM_USAGE_TRACKING=off`` returns the bare provider.
    * ``wrap_provider`` (the debug log) records full prompts and responses. Off
      by default; it carries CV PII.

    The order matters: the debug logger reads ``self._inner._model``, and the
    usage wrapper exposes ``_model`` through to the real provider, so the debug
    records are byte-identical to what they were before the usage seam existed.
    """
    from applire.providers.llm.debug_log import wrap_provider
    from applire.providers.llm.usage import wrap_usage

    return wrap_provider(wrap_usage(_build_provider(settings.llm_provider.lower())))


def unwrap_provider(provider: LLMProvider) -> LLMProvider:
    """Return the concrete provider underneath any transparent wrappers.

    ``get_provider()`` may hand back a usage recorder inside a debug logger
    (ADR-086 clause 7). Both are pass-throughs, so anything that needs to know
    *which backend was selected* — a selection test, a capability probe —
    unwraps first rather than asserting on the outermost type.
    """
    seen: set[int] = set()
    while True:
        inner = getattr(provider, "_inner", None)
        if inner is None or id(inner) in seen:
            return provider
        seen.add(id(inner))
        provider = inner


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
