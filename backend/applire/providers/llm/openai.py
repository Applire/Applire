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

"""OpenAI / OpenAI-compatible provider (ADR 009).

Works with OpenAI directly, or any OpenAI-compatible server (LM Studio, etc.).
Set OPENAI_BASE_URL to redirect to a local server.
For OpenRouter specifically, use OpenRouterProvider (openrouter.py).
"""

import asyncio
import json
from typing import Any

import openai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from applire.config import settings
from applire.exceptions import LLMProviderUnavailableError, LLMRateLimitError, LLMTimeoutError
from applire.providers.llm.base import (
    LLMProvider,
    raise_if_no_completion,
    raise_if_truncated,
    retry_on_truncation,
)

_retry = retry(
    retry=retry_if_exception_type(openai.RateLimitError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=16),
    reraise=True,
)


class OpenAIProvider(LLMProvider):
    """OpenAI or OpenAI-compatible provider (ADR 009)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 30,
    ) -> None:
        super().__init__(timeout=timeout)
        resolved_base_url = base_url or settings.openai_base_url or None
        self._client = openai.AsyncOpenAI(
            api_key=api_key or settings.openai_api_key or "local",
            base_url=resolved_base_url,
        )
        self._model = model or settings.openai_model
        self._has_custom_base = bool(resolved_base_url)

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
            raise LLMTimeoutError(f"OpenAI call timed out after {self._timeout}s")
        except openai.RateLimitError as exc:
            raise LLMRateLimitError("OpenAI rate limit after 3 attempts") from exc
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError("OpenAI SDK reported timeout") from exc
        except openai.APIStatusError as exc:
            # #256: see openrouter.py's identical mapping — a genuine 5xx is a
            # retryable outage, never a reason to surface the raw provider-
            # JSON body to the caller. Also covers self-hosted OpenAI-
            # compatible servers (LM Studio etc.) returning a 5xx.
            if exc.status_code >= 500:
                raise LLMProviderUnavailableError(
                    f"{self._model or 'The LLM provider'} is temporarily unavailable "
                    f"(HTTP {exc.status_code}). Retry the same request."
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
            content = await retry_on_truncation(attempt, max_tokens=max_tokens, model=self._model)
        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"OpenAI call timed out after {self._timeout}s")
        except openai.RateLimitError as exc:
            raise LLMRateLimitError("OpenAI rate limit after 3 attempts") from exc
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError("OpenAI SDK reported timeout") from exc
        except openai.APIStatusError as exc:
            # #256: see openrouter.py's identical mapping — a genuine 5xx is a
            # retryable outage, never a reason to surface the raw provider-
            # JSON body to the caller. Also covers self-hosted OpenAI-
            # compatible servers (LM Studio etc.) returning a 5xx.
            if exc.status_code >= 500:
                raise LLMProviderUnavailableError(
                    f"{self._model or 'The LLM provider'} is temporarily unavailable "
                    f"(HTTP {exc.status_code}). Retry the same request."
                ) from exc
            raise
        # Strip markdown code fences (common with local models)
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content.strip())

    @_retry
    async def _complete(self, messages: list, temperature: float, max_tokens: int) -> str:
        response = await self._client.chat.completions.create(
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
        kwargs: dict = {}
        if not self._has_custom_base:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
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
