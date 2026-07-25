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

"""OpenRouter provider — multi-model gateway (Iter 16, ADR 009).

Uses the openai Python SDK pointed at OpenRouter's OpenAI-compatible endpoint.
Two required headers identify us to OpenRouter's usage-ranking and abuse-detection:
  HTTP-Referer  — canonical URL of the application
  X-Title       — human-readable application name

Default model: mistralai/mistral-large-latest
  Rationale: keeps model parity with the direct Mistral provider so our prompts
  behave identically. Switch OPENROUTER_MODEL once the plumbing is validated.

Env vars consumed (see applire/config.py):
  OPENROUTER_API_KEY   — required
  OPENROUTER_MODEL     — optional, defaults to mistralai/mistral-large-latest
  OPENROUTER_BASE_URL  — optional override (default: https://openrouter.ai/api/v1)
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
from applire.exceptions import LLMProviderUnavailableError, LLMRateLimitError, LLMTimeoutError
from applire.providers.llm.base import (
    LLMProvider,
    raise_if_no_completion,
    raise_if_truncated,
    retry_on_truncation,
)

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_HTTP_REFERER = "https://applire.community"
_X_TITLE = "Applire"

# Some models mandate reasoning and reject {"reasoning": {"enabled": False}} with a
# 400 (e.g. google/gemini-3.5-flash: "Reasoning is mandatory ... cannot be disabled").
# A self-hoster who points Applire at such a model shouldn't have to know that — we
# retry once with reasoning left ON, raising the budget to this floor so the now
# unavoidable reasoning tokens don't crowd out a short answer (→ truncation).
_REASONING_FALLBACK_MIN_TOKENS = 4096


def _is_reasoning_mandatory_error(exc: Exception) -> bool:
    """True when a 400 says the model won't let us turn reasoning off."""
    msg = str(getattr(exc, "message", None) or exc).lower()
    return "reasoning" in msg and (
        "mandatory" in msg or "cannot be disabled" in msg or "can't be disabled" in msg
    )

_retry = retry(
    retry=retry_if_exception_type(openai.RateLimitError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=16),
    reraise=True,
)


class OpenRouterProvider(LLMProvider):
    """OpenRouter multi-model gateway provider (ADR 009, Iter 16)."""

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
            api_key=api_key or settings.openrouter_api_key,
            base_url=base_url or settings.openrouter_base_url or _DEFAULT_BASE_URL,
            default_headers={
                "HTTP-Referer": _HTTP_REFERER,
                "X-Title": _X_TITLE,
            },
        )
        self._model = model or settings.openrouter_model or "mistralai/mistral-large-latest"
        # When True, passes enable_thinking=False via extra_body — suppresses Qwen3/DeepSeek-R1
        # chain-of-thought overhead on deterministic structured-extraction tasks.
        self._disable_thinking = (
            disable_thinking if disable_thinking is not None
            else settings.openrouter_disable_thinking
        )
        # When reasoning stays ON, bound its effort (low/medium/high) so it doesn't
        # eat the max_tokens budget. "" = unset (let the model decide). Accepted even by
        # models that mandate reasoning, so it doubles as the fallback when a model
        # rejects reasoning:{enabled:false} (ADR-009 amendment, F-B follow-up).
        self._reasoning_effort = (
            reasoning_effort if reasoning_effort is not None
            else settings.openrouter_reasoning_effort
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
            raise LLMTimeoutError(f"OpenRouter call timed out after {self._timeout}s")
        except openai.RateLimitError as exc:
            raise LLMRateLimitError("OpenRouter rate limit after 3 attempts") from exc
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError("OpenRouter SDK reported timeout") from exc
        except openai.APIStatusError as exc:
            # #256: a genuine 5xx from the gateway/upstream provider is a
            # retryable outage, not a config/client error — never surface
            # exc's raw provider-JSON body text to the caller. Anything below
            # 500 (bad request, auth, etc.) is a real caller/config problem —
            # let it propagate as-is.
            if exc.status_code >= 500:
                raise LLMProviderUnavailableError(
                    f"OpenRouter is temporarily unavailable (HTTP {exc.status_code}). "
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
        extra_body = self._extra_body(disable_thinking)

        async def attempt(budget: int) -> str:
            return await asyncio.wait_for(
                self._parse_json(messages, temperature, budget, extra_body),
                timeout=self._timeout,
            )

        try:
            content = await retry_on_truncation(attempt, max_tokens=max_tokens, model=self._model)
        except asyncio.TimeoutError:
            raise LLMTimeoutError(f"OpenRouter call timed out after {self._timeout}s")
        except openai.RateLimitError as exc:
            raise LLMRateLimitError("OpenRouter rate limit after 3 attempts") from exc
        except openai.APITimeoutError as exc:
            raise LLMTimeoutError("OpenRouter SDK reported timeout") from exc
        except openai.APIStatusError as exc:
            # #256: see acomplete's identical mapping above.
            if exc.status_code >= 500:
                raise LLMProviderUnavailableError(
                    f"OpenRouter is temporarily unavailable (HTTP {exc.status_code}). "
                    "Retry the same request."
                ) from exc
            raise
        # Strip markdown code fences some models emit
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content.strip())

    def _extra_body(self, disable_thinking: bool | None = None) -> dict | None:
        # Per-call override wins; otherwise the provider's configured default.
        # OpenRouter's unified `reasoning` param normalises across vendors (Gemini
        # thinkingBudget, Qwen enable_thinking, etc.) — unlike the vendor-specific
        # enable_thinking key, this actually reaches Gemini thinking models.
        effective = disable_thinking if disable_thinking is not None else self._disable_thinking
        if effective:
            return {"reasoning": {"enabled": False}}
        if self._reasoning_effort:
            return {"reasoning": {"effort": self._reasoning_effort}}
        return None

    async def _create(self, *, max_tokens: int, extra_body: dict | None, **kwargs):
        """Call the chat-completions endpoint, degrading gracefully when the model
        mandates reasoning. If we asked to disable reasoning and the model 400s for
        that reason, retry once with reasoning left on and a budget floor — so a
        thinking model the operator chose still works without any configuration."""
        try:
            return await self._client.chat.completions.create(
                max_tokens=max_tokens, extra_body=extra_body, **kwargs
            )
        except openai.BadRequestError as exc:
            tried_disable = bool(
                extra_body and extra_body.get("reasoning", {}).get("enabled") is False
            )
            if tried_disable and _is_reasoning_mandatory_error(exc):
                # The model won't let us turn reasoning off. Retry with it bounded to
                # the configured effort (so it doesn't run away), or — if no effort is
                # configured — drop the block and rely on the raised budget floor.
                base_extra = {k: v for k, v in extra_body.items() if k != "reasoning"}
                if self._reasoning_effort:
                    base_extra["reasoning"] = {"effort": self._reasoning_effort}
                logger.warning(
                    "model=%s mandates reasoning; retrying with thinking on "
                    "(effort=%s), max_tokens>=%d (%s)",
                    self._model, self._reasoning_effort or "model-default",
                    _REASONING_FALLBACK_MIN_TOKENS, exc,
                )
                return await self._client.chat.completions.create(
                    max_tokens=max(max_tokens, _REASONING_FALLBACK_MIN_TOKENS),
                    extra_body=base_extra or None,
                    **kwargs,
                )
            raise

    @_retry
    async def _complete(
        self, messages: list, temperature: float, max_tokens: int, extra_body: dict | None
    ) -> str:
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        logger.debug(
            "LLM request [acomplete] model=%s temperature=%s max_tokens=%d messages=%d prompt_chars=%d",
            self._model, temperature, max_tokens, len(messages), prompt_chars,
        )
        t0 = time.monotonic()
        response = await self._create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body=extra_body,
        )
        elapsed = time.monotonic() - t0
        raise_if_no_completion(response, model=self._model)
        content = response.choices[0].message.content
        usage = response.usage
        finish = response.choices[0].finish_reason
        logger.info(
            "LLM response [acomplete] model=%s latency=%.2fs prompt_tokens=%s completion_tokens=%s finish=%s",
            self._model, elapsed,
            usage.prompt_tokens if usage else "?",
            usage.completion_tokens if usage else "?",
            finish,
        )
        logger.debug("LLM response content (first 500 chars): %.500s", content or "")
        raise_if_truncated(finish, model=self._model)
        return content

    @_retry
    async def _parse_json(
        self, messages: list, temperature: float, max_tokens: int, extra_body: dict | None
    ) -> str:
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        logger.debug(
            "LLM request [aparse_json] model=%s temperature=%s max_tokens=%d messages=%d prompt_chars=%d",
            self._model, temperature, max_tokens, len(messages), prompt_chars,
        )
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
        raise_if_no_completion(response, model=self._model)
        content = response.choices[0].message.content
        usage = response.usage
        finish = response.choices[0].finish_reason
        logger.info(
            "LLM response [aparse_json] model=%s latency=%.2fs prompt_tokens=%s completion_tokens=%s finish=%s",
            self._model, elapsed,
            usage.prompt_tokens if usage else "?",
            usage.completion_tokens if usage else "?",
            finish,
        )
        logger.debug("LLM response content (first 500 chars): %.500s", content or "")
        raise_if_truncated(finish, model=self._model)
        return content


def _build_messages(prompt: str, system: str | None) -> list:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages
