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

"""Ollama local LLM provider — zero cloud dependencies (ADR 009)."""

import asyncio
import json
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from applire.config import settings
from applire.exceptions import LLMProviderUnavailableError, LLMRateLimitError, LLMTimeoutError
from applire.providers.llm.base import LLMProvider, raise_if_truncated, retry_on_truncation


def _completion_text(data: dict, *, model: str) -> str:
    """Extract the message content, raising LLMProviderUnavailableError (never
    a raw KeyError/TypeError) on a malformed/empty local-server response
    (issue #256 — same crash class as the OpenAI-compat providers' blind
    ``response.choices[0]`` indexing, just shaped differently here)."""
    message = data.get("message") if isinstance(data, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise LLMProviderUnavailableError(
            f"{model or 'Ollama'} returned no completion (malformed response). "
            "Retry the same request."
        )
    return content

_CONNECT_TIMEOUT = 5.0   # fail fast if Ollama is not running


def _is_rate_limit(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        return True
    return False


_retry = retry(
    retry=retry_if_exception(_is_rate_limit),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=16),
    reraise=True,
)


class OllamaProvider(LLMProvider):
    """Ollama local LLM provider — zero cloud dependencies (ADR 009)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 30,
    ) -> None:
        super().__init__(timeout=timeout)
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._model = model or settings.ollama_model

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
            raise LLMTimeoutError(f"Ollama call timed out after {self._timeout}s")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise LLMRateLimitError("Ollama rate limit after 3 attempts") from exc
            if exc.response.status_code >= 500:
                # #256: never surface exc's raw response-body text (may embed
                # the local server's own error JSON) to the caller.
                raise LLMProviderUnavailableError(
                    f"Ollama is temporarily unavailable (HTTP {exc.response.status_code}). "
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
            raise LLMTimeoutError(f"Ollama call timed out after {self._timeout}s")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise LLMRateLimitError("Ollama rate limit after 3 attempts") from exc
            if exc.response.status_code >= 500:
                raise LLMProviderUnavailableError(
                    f"Ollama is temporarily unavailable (HTTP {exc.response.status_code}). "
                    "Retry the same request."
                ) from exc
            raise
        return json.loads(raw)

    @_retry
    async def _complete(self, messages: list, temperature: float, max_tokens: int) -> str:
        async with httpx.AsyncClient(timeout=self._timeout + _CONNECT_TIMEOUT) as client:
            response = await client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
            )
            response.raise_for_status()
        data = response.json()
        raise_if_truncated(data.get("done_reason"), model=self._model)
        return _completion_text(data, model=self._model)

    @_retry
    async def _parse_json(self, messages: list, temperature: float, max_tokens: int) -> str:
        async with httpx.AsyncClient(timeout=self._timeout + _CONNECT_TIMEOUT) as client:
            response = await client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": self._model,
                    "messages": messages,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                },
            )
            response.raise_for_status()
        data = response.json()
        raise_if_truncated(data.get("done_reason"), model=self._model)
        return _completion_text(data, model=self._model)


def _build_messages(prompt: str, system: str | None) -> list:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages
