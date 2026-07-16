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

"""Requesty provider — EU-hosted multi-model gateway (ADR 009, amended 2026-06-14).

Uses the openai Python SDK pointed at Requesty's OpenAI-compatible endpoint. Defaults
to the EU-residency router (Frankfurt / AWS eu-central-1, zero retention, GDPR +
SOC 2 Type II). This is both an EU-hosted alternative to OpenRouter and an EU-resident
path to the frontier US models (Claude on Bedrock eu-central-1, GPT on Azure France,
Gemini on Vertex europe-west).

The EU endpoint guarantees Requesty's own processing stays in the EU; for *full* data
residency the configured model must itself be an EU-region deployment
(e.g. `bedrock/claude-sonnet-4-5-v2@eu-central-1`).

Env vars consumed (see applire/config.py):
  REQUESTY_API_KEY   — required
  REQUESTY_MODEL     — optional, defaults to mistralai/mistral-large-latest
  REQUESTY_BASE_URL  — optional override (default: https://router.eu.requesty.ai/v1)
"""

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

import openai
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from applire.config import settings
from applire.exceptions import LLMRateLimitError, LLMTimeoutError
from applire.providers.llm.base import LLMProvider, raise_if_truncated, retry_on_truncation

_DEFAULT_BASE_URL = "https://router.eu.requesty.ai/v1"
_HTTP_REFERER = "https://applire.community"
_X_TITLE = "Applire"

# Some models mandate reasoning or predate the parameter; when the router 400s on
# reasoning_effort we retry once without it, flooring the budget so the now
# unavoidable reasoning tokens don't crowd out a short answer (→ truncation).
_REASONING_FALLBACK_MIN_TOKENS = 4096

_retry = retry(
    retry=retry_if_exception_type(openai.RateLimitError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=16),
    reraise=True,
)


class RequestyProvider(LLMProvider):
    """Requesty EU-hosted gateway provider (ADR 009, amended 2026-06-14)."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        timeout: int = 30,
        disable_thinking: bool | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self._client = openai.AsyncOpenAI(
            api_key=api_key or settings.requesty_api_key,
            base_url=base_url or settings.requesty_base_url or _DEFAULT_BASE_URL,
            default_headers={
                "HTTP-Referer": _HTTP_REFERER,
                "X-Title": _X_TITLE,
            },
        )
        self._model = model or settings.requesty_model or "mistralai/mistral-large-latest"
        # #179: Requesty accepted disable_thinking in the signature but never sent
        # anything — a reasoning model burned the whole output budget on hidden
        # chain-of-thought. Requesty's router takes a TOP-LEVEL reasoning_effort;
        # "none"/"min" disable or minimise reasoning where the model allows it
        # (docs.requesty.ai/features/reasoning). Sent via extra_body so the openai
        # SDK version doesn't gate the field.
        self._disable_thinking = (
            disable_thinking if disable_thinking is not None
            else settings.requesty_disable_thinking
        )
        self._reasoning_effort = (
            reasoning_effort if reasoning_effort is not None
            else settings.requesty_reasoning_effort
        ) or ""

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
        extra_body = self._extra_body(disable_thinking)

        async def attempt(budget: int) -> str:
            return await asyncio.wait_for(
                self._complete(messages, temperature, budget, extra_body),
                timeout=self._timeout,
            )

        try:
            return await retry_on_truncation(attempt, max_tokens=max_tokens, model=self._model)
        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"Requesty call timed out after {self._timeout}s")
        except openai.RateLimitError as exc:
            raise LLMRateLimitError("Requesty rate limit after 3 attempts") from exc
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError("Requesty SDK reported timeout") from exc

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
        extra_body = self._extra_body(disable_thinking)

        async def attempt(budget: int) -> str:
            return await asyncio.wait_for(
                self._parse_json(messages, temperature, budget, extra_body),
                timeout=self._timeout,
            )

        try:
            content = await retry_on_truncation(attempt, max_tokens=max_tokens, model=self._model)
        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"Requesty call timed out after {self._timeout}s")
        except openai.RateLimitError as exc:
            raise LLMRateLimitError("Requesty rate limit after 3 attempts") from exc
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError("Requesty SDK reported timeout") from exc
        # Strip markdown code fences some models emit
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content.strip())

    def _extra_body(self, disable_thinking: bool | None = None) -> dict | None:
        effective = disable_thinking if disable_thinking is not None else self._disable_thinking
        if effective:
            return {"reasoning_effort": "none"}
        if self._reasoning_effort:
            return {"reasoning_effort": self._reasoning_effort}
        return None

    async def _create(self, *, max_tokens: int, extra_body: dict | None, **kwargs):
        try:
            return await self._client.chat.completions.create(
                max_tokens=max_tokens, extra_body=extra_body, **kwargs
            )
        except openai.BadRequestError as exc:
            if extra_body and "reasoning_effort" in extra_body:
                logger.warning(
                    "model=%s rejected reasoning_effort=%s; retrying without it, "
                    "max_tokens>=%d (%s)",
                    self._model, extra_body["reasoning_effort"],
                    _REASONING_FALLBACK_MIN_TOKENS, exc,
                )
                stripped = {k: v for k, v in extra_body.items() if k != "reasoning_effort"}
                return await self._client.chat.completions.create(
                    max_tokens=max(max_tokens, _REASONING_FALLBACK_MIN_TOKENS),
                    extra_body=stripped or None,
                    **kwargs,
                )
            raise

    @_retry
    async def _complete(
        self, messages: list, temperature: float, max_tokens: int, extra_body: dict | None
    ) -> str:
        t0 = time.monotonic()
        response = await self._create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra_body,
        )
        elapsed = time.monotonic() - t0
        usage = response.usage
        logger.info(
            "LLM response [acomplete] model=%s latency=%.2fs prompt_tokens=%s completion_tokens=%s",
            self._model, elapsed,
            usage.prompt_tokens if usage else "?",
            usage.completion_tokens if usage else "?",
        )
        raise_if_truncated(response.choices[0].finish_reason, model=self._model)
        return response.choices[0].message.content

    @_retry
    async def _parse_json(
        self, messages: list, temperature: float, max_tokens: int, extra_body: dict | None
    ) -> str:
        t0 = time.monotonic()
        response = await self._create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            extra_body=extra_body,
        )
        elapsed = time.monotonic() - t0
        usage = response.usage
        logger.info(
            "LLM response [aparse_json] model=%s latency=%.2fs prompt_tokens=%s completion_tokens=%s",
            self._model, elapsed,
            usage.prompt_tokens if usage else "?",
            usage.completion_tokens if usage else "?",
        )
        raise_if_truncated(response.choices[0].finish_reason, model=self._model)
        return response.choices[0].message.content


def _build_messages(prompt: str, system: str | None) -> list:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages
