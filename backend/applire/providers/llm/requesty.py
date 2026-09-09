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
from applire.exceptions import LLMProviderUnavailableError, LLMRateLimitError, LLMTimeoutError
from applire.providers.llm.base import (
    LLMProvider,
    is_schema_rejection_message,
    raise_if_no_completion,
    raise_if_truncated,
    retry_on_truncation,
)
from applire.providers.llm.reasoning import finalise_completion
from applire.providers.llm.usage import note_usage

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
        # #181: once the configured model has 400'd on reasoning_effort we know it
        # never will, so cache the rejection and stop re-sending it. Without this the
        # reject-then-retry round-trip repeats on every truncation-doubled attempt and
        # on every subsequent call — a wasted API round-trip each time on that path.
        self._reasoning_rejected = False

    # ── M-3: structured output, with the rejection latched ───────────────────
    _json_schema_rejected = False

    @staticmethod
    def _response_format(json_schema: dict | None) -> dict:
        """`json_schema` when the caller supplied one, else today's `json_object`."""
        if json_schema:
            return {"type": "json_schema", "json_schema": json_schema}
        return {"type": "json_object"}

    def _note_schema_rejection(self, exc: Exception) -> bool:
        """True when this 400 is about the response schema, and latch it.

        Same shape and same reason as the mandatory-reasoning latch above: an
        endpoint that cannot take a schema will never take one, so it costs a
        single wasted request per process instead of one per call. The wording
        check itself lives once in ``base.is_schema_rejection_message`` — see
        that docstring for the adversarial-pass finding it fixes (a bare
        substring match latching on an UNRELATED 400 that merely lists
        `response_format` among the request's field names) and its own
        documented limit (a genuine rejection phrased with none of the
        matched words still slips through un-latched).
        """
        msg = str(getattr(exc, "message", None) or exc)
        if is_schema_rejection_message(msg):
            self._json_schema_rejected = True
            logger.warning(
                "model=%s rejected the response json_schema; falling back to "
                "plain JSON mode for this process (%s)",
                self._model, exc,
            )
            return True
        return False

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
        except openai.APIStatusError as exc:
            # #256: see openrouter.py's identical mapping — a genuine 5xx from
            # the gateway/upstream provider is a retryable outage, never a
            # reason to surface the raw provider-JSON body to the caller.
            if exc.status_code >= 500:
                raise LLMProviderUnavailableError(
                    f"Requesty is temporarily unavailable (HTTP {exc.status_code}). "
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
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        messages = _build_messages(prompt, system)
        extra_body = self._extra_body(disable_thinking)
        # M-3: schema guidance, latched off after the first rejection so an
        # endpoint without structured output costs one 400 per process.
        schema = None if self._json_schema_rejected else json_schema

        async def attempt(budget: int) -> str:
            return await asyncio.wait_for(
                self._parse_json(messages, temperature, budget, extra_body, schema),
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
        except openai.APIStatusError as exc:
            # #256: see acomplete's identical mapping above.
            if exc.status_code >= 500:
                raise LLMProviderUnavailableError(
                    f"Requesty is temporarily unavailable (HTTP {exc.status_code}). "
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
        effective = disable_thinking if disable_thinking is not None else self._disable_thinking
        if effective:
            return {"reasoning_effort": "none"}
        if self._reasoning_effort:
            return {"reasoning_effort": self._reasoning_effort}
        return None

    async def _create(self, *, max_tokens: int, extra_body: dict | None, **kwargs):
        # #181: skip the doomed reasoning_effort request once this model has rejected
        # it — strip it upfront and floor the budget, so neither a truncation retry nor
        # a later call repeats the wasted reject-then-retry round-trip.
        if self._reasoning_rejected and extra_body and "reasoning_effort" in extra_body:
            extra_body = {k: v for k, v in extra_body.items() if k != "reasoning_effort"} or None
            max_tokens = max(max_tokens, _REASONING_FALLBACK_MIN_TOKENS)
        try:
            return await self._client.chat.completions.create(
                max_tokens=max_tokens, extra_body=extra_body, **kwargs
            )
        except openai.BadRequestError as exc:
            # M-3 — a schema-shaped 400 retries the SAME call without the
            # schema, so structured output can never cost a turn.
            if (
                isinstance(kwargs.get("response_format"), dict)
                and kwargs["response_format"].get("type") == "json_schema"
                and self._note_schema_rejection(exc)
            ):
                retry_kwargs = {**kwargs, "response_format": {"type": "json_object"}}
                return await self._client.chat.completions.create(
                    max_tokens=max_tokens, extra_body=extra_body, **retry_kwargs
                )
            if extra_body and "reasoning_effort" in extra_body:
                # Latch the rejection only when the 400 is actually about reasoning —
                # an unrelated 400 (context-length, bad response_format) that merely
                # co-occurs with reasoning_effort must not permanently disable
                # reasoning control for this instance. The one-shot stripped retry
                # below still runs either way (harmless if the real cause persists).
                if "reasoning" in str(exc).lower():
                    self._reasoning_rejected = True
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
        note_usage(response)  # ADR-086 clause 7 — token accounting seam
        usage = response.usage
        logger.info(
            "LLM response [acomplete] model=%s latency=%.2fs prompt_tokens=%s completion_tokens=%s",
            self._model, elapsed,
            usage.prompt_tokens if usage else "?",
            usage.completion_tokens if usage else "?",
        )
        raise_if_no_completion(response, model=self._model)
        raise_if_truncated(response.choices[0].finish_reason, model=self._model)
        return finalise_completion(
            response, response.choices[0].message.content,
            model=self._model, method="acomplete",
        )

    @_retry
    async def _parse_json(
        self,
        messages: list,
        temperature: float,
        max_tokens: int,
        extra_body: dict | None,
        json_schema: dict | None = None,
    ) -> str:
        t0 = time.monotonic()
        response = await self._create(
            model=self._model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=self._response_format(json_schema),
            extra_body=extra_body,
        )
        elapsed = time.monotonic() - t0
        note_usage(response)  # ADR-086 clause 7 — token accounting seam
        usage = response.usage
        logger.info(
            "LLM response [aparse_json] model=%s latency=%.2fs prompt_tokens=%s completion_tokens=%s",
            self._model, elapsed,
            usage.prompt_tokens if usage else "?",
            usage.completion_tokens if usage else "?",
        )
        raise_if_no_completion(response, model=self._model)
        raise_if_truncated(response.choices[0].finish_reason, model=self._model)
        return finalise_completion(
            response, response.choices[0].message.content,
            model=self._model, method="aparse_json",
        )


def _build_messages(prompt: str, system: str | None) -> list:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    return messages
