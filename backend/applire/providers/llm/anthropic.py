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

"""Anthropic provider — native Messages API (ADR 009, amended 2026-06-14).

Uses the native `anthropic` SDK rather than the OpenAI-compat shim. The Messages API
differs from OpenAI in three ways that require adapter logic (the "provider-specific
adapter logic" Negative in ADR-009 made concrete):

  - `system` is a top-level parameter, not a message with role "system".
  - `max_tokens` is mandatory on every call.
  - There is no native `response_format=json_object`. `aparse_json` therefore forces
    valid JSON via **assistant-prefill**: the assistant turn is seeded with "{", so
    the model can only continue a JSON object; we prepend "{" to the reply and parse.

BYO-API-key only. A Claude Pro/Max/Team *subscription* cannot be used — Anthropic
prohibits subscription-OAuth tokens in third-party apps (enforced 2026-01-09).

Env vars consumed (see applire/config.py):
  ANTHROPIC_API_KEY  — required
  ANTHROPIC_MODEL    — optional, defaults to claude-sonnet-4-6
"""

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

import anthropic
from anthropic import AsyncAnthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from applire.config import settings
from applire.exceptions import LLMProviderUnavailableError, LLMRateLimitError, LLMTimeoutError
from applire.providers.llm.base import LLMProvider, raise_if_truncated

_retry = retry(
    retry=retry_if_exception_type(anthropic.RateLimitError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=16),
    reraise=True,
)


class AnthropicProvider(LLMProvider):
    """Native Anthropic Messages-API provider (ADR 009, amended 2026-06-14)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 30,
    ) -> None:
        super().__init__(timeout=timeout)
        self._client = AsyncAnthropic(api_key=api_key or settings.anthropic_api_key)
        self._model = model or settings.anthropic_model or "claude-sonnet-4-6"

    async def acomplete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        disable_thinking: bool | None = None,
    ) -> str:
        messages = [{"role": "user", "content": prompt}]
        try:
            return await asyncio.wait_for(
                self._create(messages, system, temperature, max_tokens),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"Anthropic call timed out after {self._timeout}s")
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError("Anthropic rate limit after 3 attempts") from exc
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError("Anthropic SDK reported timeout") from exc
        except anthropic.APIStatusError as exc:
            # #256: a genuine 5xx (e.g. "Overloaded") is a retryable outage —
            # never surface exc's raw provider-JSON body to the caller.
            if exc.status_code >= 500:
                raise LLMProviderUnavailableError(
                    f"Anthropic is temporarily unavailable (HTTP {exc.status_code}). "
                    "Retry the same request."
                ) from exc
            raise

    async def aparse_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        disable_thinking: bool | None = None,
    ) -> dict[str, Any]:
        # Assistant-prefill "{" forces the model to continue a JSON object.
        messages = [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": "{"},
        ]
        try:
            text = await asyncio.wait_for(
                self._create(messages, system, temperature, max_tokens),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"Anthropic call timed out after {self._timeout}s")
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError("Anthropic rate limit after 3 attempts") from exc
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError("Anthropic SDK reported timeout") from exc
        except anthropic.APIStatusError as exc:
            # #256: a genuine 5xx (e.g. "Overloaded") is a retryable outage —
            # never surface exc's raw provider-JSON body to the caller.
            if exc.status_code >= 500:
                raise LLMProviderUnavailableError(
                    f"Anthropic is temporarily unavailable (HTTP {exc.status_code}). "
                    "Retry the same request."
                ) from exc
            raise
        raw = ("{" + text).strip()
        # Drop any trailing prose / code fence after the JSON object.
        end = raw.rfind("}")
        if end != -1:
            raw = raw[: end + 1]
        return json.loads(raw)

    @_retry
    async def _create(
        self, messages: list, system: str | None, temperature: float, max_tokens: int
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,        # required by the Messages API
            "temperature": temperature,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system        # top-level param, not a message
        t0 = time.monotonic()
        response = await self._client.messages.create(**kwargs)
        elapsed = time.monotonic() - t0
        usage = getattr(response, "usage", None)
        logger.info(
            "LLM response [anthropic] model=%s latency=%.2fs input_tokens=%s output_tokens=%s",
            self._model, elapsed,
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "output_tokens", "?"),
        )
        raise_if_truncated(getattr(response, "stop_reason", None), model=self._model)
        return _text(response)


def _text(response: Any) -> str:
    """Concatenate the text blocks of a Messages-API response.

    #256: ``getattr(..., [])`` only covers a missing attribute — a malformed
    response with ``content=None`` (attribute present, value None) would still
    crash ``for block in None`` with a raw TypeError; the trailing ``or []``
    covers that shape too.
    """
    parts = [
        block.text
        for block in (getattr(response, "content", None) or [])
        if getattr(block, "type", None) == "text"
    ]
    return "".join(parts)
