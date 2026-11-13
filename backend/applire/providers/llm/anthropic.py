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
    object; every later model rejects a prefill with HTTP 400. On those, a caller's
    `json_schema` goes out as structured output (`output_config.format`, see
    `_output_format`); a call without one gets a fixed JSON-only text block after
    the prompt (`JSON_ONLY_INSTRUCTION`) and, when the reply still opens with prose,
    ONE corrective turn (`CORRECTIVE_TURN`). Measured on Claude Sonnet 5.5 against
    the UX-audit-4 log (2026-11-07): without either, 28 of 38 replays of the failed
    calls answered "Item 0: … Supported." prose before the JSON (4 more truncated).
  - `max_tokens` bounds thinking AND answer. A model that thinks by default gets
    `THINKING_HEADROOM` on top of the caller's budget, which sizes the answer
    (the terminal CV review truncated 3/3 at 2560, mostly thinking), and every
    call runs under the shared truncation net (`retry_on_truncation`).
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
    LLMResponseParseError,
    LLMTimeoutError,
)
from applire.providers.llm.base import (
    require_strict_key,
    LLMProvider,
    clamp_output_budget,
    is_schema_rejection_message,
    raise_if_truncated,
    retry_on_truncation,
    unclassified_provider_error,
)
from applire.providers.llm.json_reply import parse_json_reply
from applire.providers.llm.latches import ProcessLatch
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


def _thinks(model: str, disable_thinking: bool | None) -> bool:
    """Does this request leave the model thinking (so thinking shares max_tokens)?

    Models from generation 5 on (and an id this module does not recognise — the
    current surface) think adaptively unless the request turns it off; the 4.x
    models do not think unless asked, and Applire never asks. A per-call
    `disable_thinking` turns it off only where the model has a `thinking` switch —
    Opus 5.5 (lowest effort) and Fable/Mythos keep thinking.
    """
    gen = _generation(model)
    if gen is None:
        return True
    family, version = gen
    if family not in _ALWAYS_THINKING and version < (5, 0):
        return False
    return not (disable_thinking and "thinking" in _thinking_off_kwargs(model))


# What a thinking request adds to the caller's max_tokens. The callers size their
# budgets for the ANSWER (field expectations 300, a review verdict 2560); on the
# Messages API thinking spends the same budget, so on Sonnet 5.5 a 300-token
# classification truncated with nothing but a thinking block (4/24 replays) and the
# terminal CV review 3/3 at 2560 (UX audit 4, 2026-11-07). Same figure as the
# gateways' reasoning floor (`openrouter._REASONING_FALLBACK_MIN_TOKENS`).
# A ceiling, billed on actual output — unused headroom costs nothing.
THINKING_HEADROOM = 4096

# The fixed format instruction a schema-less JSON call on a no-prefill model ends
# with (its own text block after the prompt; the prompt's content is untouched).
# Measured 2026-11-07 on Claude Sonnet 5.5, replaying the failed round-4 calls:
# prose-first replies 28 → 0 of 38 (a system-prompt suffix left the single-claim
# Oracle call at 4/4 prose).
JSON_ONLY_INSTRUCTION = (
    "Reply with only the JSON object the instructions above describe: no prose, "
    "no explanation and no code fence before or after it."
)
# The one follow-up turn after a reply that holds no JSON value at its start.
CORRECTIVE_TURN = "Your reply was not valid JSON. Reply with only the JSON object."


def _is_open_object(node: dict[str, Any]) -> bool:
    """An object node whose keys are not all named (a free-form map)."""
    extra = node.get("additionalProperties")
    if extra is True or isinstance(extra, dict) or "patternProperties" in node:
        return True
    is_object = node.get("type") == "object" or "properties" in node
    return is_object and not node.get("properties") and extra is not False


def _has_open_object(node: Any) -> bool:
    if isinstance(node, dict):
        if _is_open_object(node):
            return True
        return any(_has_open_object(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_open_object(v) for v in node)
    return False


_OBJECT_KEYS = ("properties", "required", "additionalProperties")
_ARRAY_KEYS = ("items", "minItems", "maxItems")


def _split_type_lists(node: Any) -> Any:
    """`{"type": ["string", "null"]}` → `{"anyOf": [{"type": "string"}, {"type": "null"}]}`.

    Pydantic and the OpenAI-strict writer schema use type lists for nullables; the
    SDK's `transform_schema` reads `type` as one name and fails on a list. A purely
    syntactic rewrite: object/array keys move into their own variant.
    """
    if isinstance(node, list):
        return [_split_type_lists(v) for v in node]
    if not isinstance(node, dict):
        return node
    node = {k: _split_type_lists(v) for k, v in node.items()}
    types = node.get("type")
    if not isinstance(types, list):
        return node
    rest = {k: v for k, v in node.items() if k != "type"}
    variants = []
    for t in types:
        variant: dict[str, Any] = {"type": t}
        own = _OBJECT_KEYS if t == "object" else _ARRAY_KEYS if t == "array" else ()
        variant.update({k: rest[k] for k in own if k in rest})
        variants.append(variant)
    outer = {k: v for k, v in rest.items() if k not in _OBJECT_KEYS + _ARRAY_KEYS}
    return {**outer, "anyOf": variants}


def _output_format(json_schema: dict[str, Any] | None) -> dict[str, Any] | None:
    """The caller's OpenAI-style `json_schema` block as Anthropic structured output.

    Anthropic requires `additionalProperties: false` on every object and rejects
    some constraints; the SDK's `transform_schema` closes every object and moves an
    unsupported constraint into the description. A schema with a free-form object
    (the reconciler's `RequestConfirmation.context`) cannot be closed without
    forcing that object empty, so it is NOT sent — the call runs unconstrained, as
    it did before. Anything the transform cannot read is not sent either.
    """
    if not isinstance(json_schema, dict):
        return None
    schema = json_schema.get("schema")
    if not isinstance(schema, dict) or _has_open_object(schema):
        return None
    try:
        schema = anthropic.transform_schema(_split_type_lists(schema))
        return {"type": "json_schema", "schema": schema}
    except Exception:  # noqa: BLE001 — a schema is an optimisation, never a failure
        logger.debug("anthropic: response schema not convertible; sending none")
        return None


def _assistant_turn(blocks: list[Any], text: str) -> list[dict[str, Any]] | str:
    """The previous reply as an assistant turn — every block, unchanged.

    Thinking blocks go back as received (they are bound to the conversation that
    produced them); a reply without captured blocks goes back as its text.
    """
    if not blocks:
        return text
    return [b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b for b in blocks]


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
        strict_key: bool = False,
    ) -> None:
        super().__init__(timeout=timeout)
        if strict_key:
            # ADR-096 cl. 4: a user-paid provider never reads the instance's key.
            require_strict_key(api_key, model)
        # The SDK refuses a NON-streaming request whose max_tokens implies more than
        # 10 minutes (> ~21k tokens: "Streaming is required …", a ValueError) unless
        # the client carries an explicit timeout. Reconcile asks for 32768 and the
        # truncation retry doubles 16384, so both failed on every call (WP-0,
        # 2026-11-04). Applire bounds each call itself (asyncio.wait_for on
        # self._timeout), so the HTTP timeout is that same budget plus a margin.
        self._client = AsyncAnthropic(
            api_key=api_key if strict_key else (api_key or settings.anthropic_api_key),
            timeout=float(timeout) + 30.0,
        )
        self._model = model if strict_key else (model or settings.anthropic_model or _DEFAULT_MODEL)
        self._shape = _request_shape(self._model)

    # A model that rejected a response schema is not sent one again in this process
    # (the gateway providers' M-3 latch, keyed by provider class and model).
    _json_schema_rejected = ProcessLatch("json_schema")

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

        async def attempt(budget: int) -> str:
            return await asyncio.wait_for(
                self._create(messages, system, temperature, budget, disable_thinking),
                timeout=self._timeout,
            )

        try:
            return await retry_on_truncation(attempt, max_tokens=max_tokens, model=self._model)
        except Exception as exc:
            self._raise_mapped(exc)

    def _raise_mapped(self, exc: Exception) -> None:
        """Raise our error type for an SDK / transport exception (call in `except`)."""
        if isinstance(exc, asyncio.TimeoutError):
            raise LLMTimeoutError(f"Anthropic call timed out after {self._timeout}s")
        if isinstance(exc, anthropic.RateLimitError):
            raise LLMRateLimitError("Anthropic rate limit after 3 attempts") from exc
        if isinstance(exc, anthropic.APITimeoutError):
            raise LLMTimeoutError("Anthropic SDK reported timeout") from exc
        if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
            # #256: a genuine 5xx (e.g. "Overloaded") is a retryable outage —
            # never surface exc's raw provider-JSON body to the caller.
            raise LLMProviderUnavailableError(
                f"Anthropic is temporarily unavailable (HTTP {exc.status_code}). "
                "Retry the same request."
            ) from exc
        # Error detail hardening: an SDK / HTTP-client error the clauses above do
        # not classify leaves as our own type; ours (and the rest) pass unchanged.
        wrapped = unclassified_provider_error(exc, "Anthropic")
        if wrapped is None:
            raise exc
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
        fmt = None if self._json_schema_rejected else _output_format(json_schema)

        async def send(messages: list, budget: int, fmt: dict | None) -> tuple[str, list]:
            blocks: list[Any] = []
            text = await asyncio.wait_for(
                self._create(
                    messages, system, temperature, budget, disable_thinking,
                    output_format=fmt, blocks_out=blocks,
                ),
                timeout=self._timeout,
            )
            return text, blocks

        async def first(budget: int) -> tuple[list, str, list, dict | None, bool]:
            nonlocal fmt
            plain = self._json_messages(prompt, None)
            if fmt is not None:
                msgs = self._json_messages(prompt, fmt)
                try:
                    return (msgs, *await send(msgs, budget, fmt), fmt, False)
                except anthropic.BadRequestError as exc:
                    fmt = None
                    text, blocks = await self._without_schema(exc, lambda: send(plain, budget, None))
                    return (plain, text, blocks, None, self._shape.prefill)
            return (plain, *await send(plain, budget, None), None, self._shape.prefill)

        try:
            messages, text, blocks, used_fmt, prefilled = await retry_on_truncation(
                first, max_tokens=max_tokens, model=self._model
            )
            # R2-13: one tolerant parse for every provider (first JSON value; a
            # non-JSON tail is discarded, a second JSON value raises).
            reply = "{" + text if prefilled else text
            try:
                return parse_json_reply(reply, provider="Anthropic", model=self._model)
            except LLMResponseParseError as exc:
                # A second JSON value is half an answer, never re-asked (R2-13);
                # the prefill path keeps its old single-shot contract.
                if prefilled or exc.second_value:
                    raise
            # The reply opened with prose (Sonnet 5.5: "Item 0: The claim says …"):
            # ONE corrective turn on the same conversation, never a loop.
            logger.info("LLM_JSON_CORRECTIVE_TURN provider=Anthropic model=%s", self._model)
            follow = [
                *messages,
                {"role": "assistant", "content": _assistant_turn(blocks, text)},
                {"role": "user", "content": CORRECTIVE_TURN},
            ]

            async def again(budget: int) -> str:
                return (await send(follow, budget, used_fmt))[0]

            text = await retry_on_truncation(again, max_tokens=max_tokens, model=self._model)
            return parse_json_reply(text, provider="Anthropic", model=self._model)
        except Exception as exc:
            self._raise_mapped(exc)

    def _json_messages(self, prompt: str, fmt: dict | None) -> list[dict[str, Any]]:
        """The JSON call's turns: schema → prompt alone; prefill model → "{";
        otherwise the prompt plus the fixed JSON-only block."""
        if fmt is not None:
            return [{"role": "user", "content": prompt}]
        if self._shape.prefill:
            # Assistant-prefill "{" forces the model to continue a JSON object.
            return [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "{"},
            ]
        return [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "text", "text": JSON_ONLY_INSTRUCTION},
        ]}]

    async def _without_schema(self, exc: anthropic.BadRequestError, unconstrained):
        """A 400 on a schema call: run it once without the schema; latch on success.

        The gateways' M-3 / #756 rule (`requesty._retry_without_schema`): a 400
        worded as a schema rejection latches at once; any other 400 that disappears
        without the schema IS a schema rejection and latches too. A 400 that persists
        without the schema was not about it: the ORIGINAL error surfaces and the
        latch stays off. One retry, no loop.
        """
        worded = is_schema_rejection_message(str(getattr(exc, "message", None) or exc))
        if worded:
            self._json_schema_rejected = True
            logger.warning(
                "model=%s rejected the response schema; sending none for this process (%s)",
                self._model, exc,
            )
        try:
            result = await unconstrained()
        except anthropic.BadRequestError:
            if worded:
                raise
            logger.warning(
                "model=%s: a 400 on a schema call persisted without the schema; "
                "surfacing the original error, schema stays on (%s)", self._model, exc,
            )
            raise exc from None
        if not worded:
            self._json_schema_rejected = True
            logger.warning(
                "model=%s rejected the response schema (the call succeeded without it); "
                "sending none for this process (%s)", self._model, exc,
            )
        return result

    @_retry
    async def _create(
        self,
        messages: list,
        system: str | None,
        temperature: float,
        max_tokens: int,
        disable_thinking: bool | None = None,
        *,
        output_format: dict[str, Any] | None = None,
        blocks_out: list | None = None,
    ) -> str:
        if _thinks(self._model, disable_thinking):
            # Thinking spends max_tokens too; the caller's budget sizes the answer.
            max_tokens = clamp_output_budget(max_tokens + THINKING_HEADROOM)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,        # required by the Messages API
            "messages": messages,
        }
        if self._shape.sampling:
            kwargs["temperature"] = temperature
        if disable_thinking:
            kwargs.update(_thinking_off_kwargs(self._model))
        if output_format is not None:
            # Merged, never overwritten: Opus 5.5's thinking-off is output_config.effort.
            kwargs["output_config"] = {**kwargs.get("output_config", {}), "format": output_format}
        if system:
            # Top-level param, not a message. One cache breakpoint at its end
            # (ADR-009 amended 2026-11-06): the system prompt repeats byte-for-byte
            # per pipeline site, the user prompt almost never does — so no marker
            # in `messages`. A prefix below the model's minimum is simply not cached.
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        t0 = time.monotonic()
        response = await self._client.messages.create(**kwargs)
        elapsed = time.monotonic() - t0
        note_usage(response)  # ADR-086 clause 7 — token accounting seam
        usage = getattr(response, "usage", None)
        logger.info(
            "LLM response [anthropic] model=%s latency=%.2fs input_tokens=%s "
            "cache_read=%s cache_write=%s output_tokens=%s",
            self._model, elapsed,
            getattr(usage, "input_tokens", "?"),
            getattr(usage, "cache_read_input_tokens", "?"),
            getattr(usage, "cache_creation_input_tokens", "?"),
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
        if blocks_out is not None:
            blocks_out.extend(getattr(response, "content", None) or [])
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
