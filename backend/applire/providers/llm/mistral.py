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

"""Mistral AI provider — EU-hosted default (ADR 009)."""

import asyncio
import json
from typing import Any

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from mistralai import Mistral

from applire.config import settings
from applire.exceptions import LLMProviderUnavailableError, LLMRateLimitError, LLMTimeoutError
from applire.providers.llm.base import (
    LLMProvider,
    raise_if_no_completion,
    raise_if_truncated,
    retry_on_truncation,
)


def _is_rate_limit(exc: BaseException) -> bool:
    """Return True if exc is a Mistral 429 (speakeasy SDK SDKError)."""
    if getattr(exc, "status_code", None) == 429:
        return True
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) == 429:
        return True
    return False


def _unavailable_status_code(exc: BaseException) -> int | None:
    """Return the HTTP status code if exc is a Mistral 5xx (speakeasy SDK
    SDKError), else None (issue #256 — a genuine gateway/upstream outage,
    distinct from a 429 rate limit)."""
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None) if response is not None else None
    if isinstance(status, int) and status >= 500:
        return status
    return None


_retry = retry(
    retry=retry_if_exception(_is_rate_limit),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=16),
    reraise=True,
)


class MistralProvider(LLMProvider):
    """Mistral AI provider — EU-hosted default (ADR 009)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int = 30,
    ) -> None:
        super().__init__(timeout=timeout)
        self._client = Mistral(api_key=api_key or settings.mistral_api_key)
        self._model = model or settings.mistral_model

    async def acomplete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        disable_thinking: bool | None = None,
    ) -> str:
        messages = _build_messages(prompt, system)

        async def attempt(budget: int) -> str:
            return await asyncio.wait_for(
                self._complete(messages, temperature, budget),
                timeout=self._timeout,
            )

        try:
            return await retry_on_truncation(attempt, max_tokens=max_tokens, model=self._model)
        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"Mistral call timed out after {self._timeout}s")
        except Exception as exc:
            if _is_rate_limit(exc):
                raise LLMRateLimitError("Mistral rate limit after 3 attempts") from exc
            status = _unavailable_status_code(exc)
            if status is not None:
                # #256: never surface the raw SDK error text (embeds the
                # provider's JSON body) — only a static, status-code-only message.
                raise LLMProviderUnavailableError(
                    f"Mistral is temporarily unavailable (HTTP {status}). "
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
        messages = _build_messages(prompt, system)

        async def attempt(budget: int) -> str:
            return await asyncio.wait_for(
                self._parse_json(messages, temperature, budget),
                timeout=self._timeout,
            )

        try:
            raw = await retry_on_truncation(attempt, max_tokens=max_tokens, model=self._model)
        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"Mistral call timed out after {self._timeout}s")
        except Exception as exc:
            if _is_rate_limit(exc):
                raise LLMRateLimitError("Mistral rate limit after 3 attempts") from exc
            status = _unavailable_status_code(exc)
            if status is not None:
                # #256: never surface the raw SDK error text (embeds the
                # provider's JSON body) — only a static, status-code-only message.
                raise LLMProviderUnavailableError(
                    f"Mistral is temporarily unavailable (HTTP {status}). "
                    "Retry the same request."
                ) from exc
            raise
        return json.loads(raw)

    @_retry
    async def _complete(self, messages: list, temperature: float, max_tokens: int) -> str:
        response = await self._client.chat.complete_async(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        raise_if_no_completion(response, model=self._model)
        raise_if_truncated(response.choices[0].finish_reason, model=self._model)
        return response.choices[0].message.content

    @_retry
    async def _parse_json(self, messages: list, temperature: float, max_tokens: int) -> str:
        response = await self._client.chat.complete_async(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        raise_if_no_completion(response, model=self._model)
        raise_if_truncated(response.choices[0].finish_reason, model=self._model)
        return response.choices[0].message.content


def _build_messages(prompt: str, system: str | None) -> list:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages
