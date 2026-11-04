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
differs from OpenAI in ways that require adapter logic (the "provider-specific
adapter logic" Negative in ADR-009 made concrete):

  - `system` is a top-level parameter, not a message with role "system".
  - `max_tokens` is mandatory on every call.
  - There is no native `response_format=json_object`. Models before the 4.6
    generation get an **assistant-prefill** "{" so they can only continue a JSON
    object; every later model rejects a prefill with HTTP 400, so `aparse_json`
    sends the prompt alone and the tolerant `parse_json_reply` reads the reply.
  - The request surface depends on the model generation (see `_request_shape`):
    sampling parameters (`temperature`) are accepted only up to the 4.6
    generation — Claude Sonnet 5.5 answers any value with HTTP 400 "`temperature`
    is deprecated for this model" — and how thinking is turned off differs per
    model (`{type: "disabled"}` is itself a 400 on Sonnet 5.5 and Opus 5.5).

BYO-API-key only. A Claude Pro/Max/Team *subscription* cannot be used — Anthropic
prohibits subscription-OAuth tokens in third-party apps (enforced 2026-01-09).

Env vars consumed (see applire/config.py):
  ANTHROPIC_API_KEY  — required
  ANTHROPIC_MODEL    — optional, defaults to claude-sonnet-5-5
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

import anthropic
from anthropic import AsyncAnthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from applire.config import settings
from applire.exceptions import (
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMTimeoutError,
)
from applire.providers.llm.base import LLMProvider, raise_if_truncated, unclassified_provider_error
from applire.providers.llm.json_reply import parse_json_reply
from applire.providers.llm.reasoning import finalise_completion, note_trace
from applire.providers.llm.usage import note_usage

_DEFAULT_MODEL = "claude-sonnet-5-5"

# Generation of a Claude model id, e.g. "claude-sonnet-5-5" -> ("sonnet", (5, 5)),
# "claude-sonnet-4-20250514" -> ("sonnet", (4, 0)), "claude-3-5-haiku-20241022" ->
# ("haiku", (3, 5)). A Bedrock "anthropic." prefix and a Vertex "@date" suffix are
# ignored. An id this does not recognise parses to None and gets the CURRENT
# surface (no sampling, no prefill, thinking left at the model's default): the
# newer the model, the narrower the surface, so the unknown case is the safe one.
_NEW_ID = re.compile(r"^claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$")
_OLD_ID = re.compile(r"^claude-(\d+)(?:-(\d{1,2}))?-(opus|sonnet|haiku)(?:-\d{8})?$")


_BEDROCK_REGION = re.compile(r"^(?:us|eu|apac|global|jp|au)\.")
_BEDROCK_VERSION = re.compile(r"-v\d+(?::\d+)?$")


def _generation(model: str) -> tuple[str, tuple[int, int]] | None:
    # Vertex "@date", Bedrock "[region.]anthropic.<id>-v1:0" → the plain id.
    mid = model.strip().lower().split("@", 1)[0]
    mid = _BEDROCK_VERSION.sub("", _BEDROCK_REGION.sub("", mid).removeprefix("anthropic."))
    if m := _NEW_ID.match(mid):
        return m.group(1), (int(m.group(2)), int(m.group(3) or 0))
    if m := _OLD_ID.match(mid):
        return m.group(3), (int(m.group(1)), int(m.group(2) or 0))
    return None


# How `disable_thinking=True` is expressed per model (claude-api skill, model
# migration guide, 2026-11). Models absent here either do not think unless asked
# (the 4.6 generation and older) or cannot turn thinking off at all (Opus 5.5,
# Fable, Mythos — there the lowest effort is the closest request).
_THINKING_OFF: dict[tuple[str, tuple[int, int]], dict[str, Any]] = {
    ("sonnet", (5, 5)): {"thinking": {"type": "between_tools"}},  # "disabled" is a 400
    ("haiku", (5, 5)): {"thinking": {"type": "disabled"}},        # "between_tools" is a 400
    ("sonnet", (5, 0)): {"thinking": {"type": "disabled"}},
    ("opus", (5, 0)): {"thinking": {"type": "disabled"}},
    ("opus", (4, 8)): {"thinking": {"type": "disabled"}},
    ("opus", (4, 7)): {"thinking": {"type": "disabled"}},
    ("opus", (5, 5)): {"output_config": {"effort": "low"}},
}
_ALWAYS_THINKING = ("fable", "mythos")


@dataclass(frozen=True)
class _RequestShape:
    """What one model accepts on a Messages request (see `_request_shape`)."""

    sampling: bool   # temperature is sent
    prefill: bool    # aparse_json seeds the assistant turn with "{"


def _request_shape(model: str) -> _RequestShape:
    gen = _generation(model)
    if gen is None:
        return _RequestShape(sampling=False, prefill=False)
    _, version = gen
    return _RequestShape(sampling=version <= (4, 6), prefill=version < (4, 6))


def _thinking_off_kwargs(model: str) -> dict[str, Any]:
    gen = _generation(model)
    if gen is None:
        return {}
    if gen[0] in _ALWAYS_THINKING:
        return {"output_config": {"effort": "low"}}
    return dict(_THINKING_OFF.get(gen, {}))


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
        # The SDK refuses a NON-streaming request whose max_tokens implies more than
        # 10 minutes (> ~21k tokens: "Streaming is required …", a ValueError) unless
        # the client carries an explicit timeout. Reconcile asks for 32768 and the
        # truncation retry doubles 16384, so both failed on every call (WP-0,
        # 2026-11-04). Applire bounds each call itself (asyncio.wait_for on
        # self._timeout), so the HTTP timeout is that same budget plus a margin.
        self._client = AsyncAnthropic(
            api_key=api_key or settings.anthropic_api_key,
            timeout=float(timeout) + 30.0,
        )
        self._model = model or settings.anthropic_model or _DEFAULT_MODEL
        self._shape = _request_shape(self._model)

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
                self._create(messages, system, temperature, max_tokens, disable_thinking),
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
            wrapped = unclassified_provider_error(exc, "Anthropic")
            if wrapped is None:
                raise
            raise wrapped from None
        except Exception as exc:
            # Error detail hardening: an SDK / HTTP-client error the
            # clauses above do not classify leaves as our own type.
            wrapped = unclassified_provider_error(exc, "Anthropic")
            if wrapped is None:
                raise
            raise wrapped from None

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
        # Assistant-prefill "{" forces the model to continue a JSON object — on the
        # models that still accept a prefill. Later models get the prompt alone
        # (a prefill is a 400 there); `parse_json_reply` strips one opening fence.
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        if self._shape.prefill:
            messages.append({"role": "assistant", "content": "{"})
        try:
            text = await asyncio.wait_for(
                self._create(messages, system, temperature, max_tokens, disable_thinking),
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
            wrapped = unclassified_provider_error(exc, "Anthropic")
            if wrapped is None:
                raise
            raise wrapped from None
        except Exception as exc:
            # Error detail hardening: an SDK / HTTP-client error the
            # clauses above do not classify leaves as our own type.
            wrapped = unclassified_provider_error(exc, "Anthropic")
            if wrapped is None:
                raise
            raise wrapped from None
        # R2-13: the reply continues the prefilled "{"; one tolerant parse for
        # every provider (first JSON value; a non-JSON tail is discarded, a
        # second JSON value raises — the old cut at the LAST "}" kept neither).
        reply = "{" + text if self._shape.prefill else text
        return parse_json_reply(reply, provider="Anthropic", model=self._model)

    @_retry
    async def _create(
        self,
        messages: list,
        system: str | None,
        temperature: float,
        max_tokens: int,
        disable_thinking: bool | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,        # required by the Messages API
            "messages": messages,
        }
        if self._shape.sampling:
            kwargs["temperature"] = temperature
        if disable_thinking:
            kwargs.update(_thinking_off_kwargs(self._model))
        if system:
            kwargs["system"] = system        # top-level param, not a message
        t0 = time.monotonic()
        response = await self._client.messages.create(**kwargs)
        elapsed = time.monotonic() - t0
        note_usage(response)  # ADR-086 clause 7 — token accounting seam
        usage = getattr(response, "usage", None)
        logger.info(
            "LLM response [anthropic] model=%s latency=%.2fs input_tokens=%s output_tokens=%s",
            self._model, elapsed,
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "output_tokens", "?"),
        )
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            # A declined request carries no answer; never hand its empty text on.
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            logger.warning(
                "LLM refusal [anthropic] model=%s category=%s", self._model, category
            )
            raise LLMRefusalError("Anthropic", category=category, model=self._model)
        raise_if_truncated(stop_reason, model=self._model)
        return _text(response)


def _text(response: Any) -> str:
    """Concatenate the text blocks of a Messages-API response.

    #256: ``getattr(..., [])`` only covers a missing attribute — a malformed
    response with ``content=None`` (attribute present, value None) would still
    crash ``for block in None`` with a raw TypeError; the trailing ``or []``
    covers that shape too.
    """
    blocks = getattr(response, "content", None) or []
    parts = [
        block.text for block in blocks if getattr(block, "type", None) == "text"
    ]
    # The Messages API puts reasoning in its OWN block type, so the filter above
    # has always dropped it — this only hands it to the debug log so an operator
    # can see what the thinking budget bought (M-4). `redacted_thinking` blocks
    # carry no readable text and are counted, not stored.
    thinking = "".join(
        getattr(block, "thinking", "") or ""
        for block in blocks
        if getattr(block, "type", None) in ("thinking", "redacted_thinking")
    )
    if thinking:
        note_trace(thinking)
    return finalise_completion(response, "".join(parts), method="anthropic")
