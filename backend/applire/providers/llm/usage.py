# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Token accounting at the provider seam (ADR-086 clause 7, US313).

The debug log's neighbour, not the debug log. ``debug_log.py`` records full
prompts and responses, carries CV PII and is off by default; this module records
**numbers and opaque ids only** and is on by default. They stay separate
artefacts with separate lifetimes (E060 task 1.3, ``SF-OPS.9``).

Three parts:

``extract_usage(response)``
    The vendor-normalising reader. It exists because the providers disagree:
    OpenAI-shaped clients (Mistral, OpenAI, OpenRouter, Requesty) report
    ``usage.prompt_tokens`` / ``usage.completion_tokens``, Anthropic reports
    ``usage.input_tokens`` / ``usage.output_tokens``, and Ollama reports
    ``prompt_eval_count`` / ``eval_count`` in the JSON body. This is the same
    role ``base.py``'s ``raise_if_truncated`` already plays for
    ``finish_reason`` / ``stop_reason`` / ``done_reason``.

``note_usage(response)``
    Called by each provider at the single point where the raw response object
    exists, before the code reduces it to text. It stashes the normalised counts
    in a ContextVar. ``await`` does not copy the context, so a value set inside
    the provider's coroutine is visible to the wrapper that awaited it.

``UsageRecordingProvider``
    Installed unconditionally by ``get_provider()``. It times the call, reads
    whatever ``note_usage`` left behind — falling back to a character-length
    estimate flagged ``estimated=True`` — and writes one row.

Attribution: the provider ABC has no slot for a call label (``base.py:177``),
and none of the ~39 call sites passes one. ``llm_usage_context(...)`` is the
side channel, deliberately a **set-and-restore** contextmanager rather than
``debug_log.set_stage``, whose imperative no-restore form is documented at
``services/cv.py:4073`` as leaking a stale label across unrelated calls in the
same task. When no context is open the recorder falls back to
``debug_log.current_call_site()``'s stage; a row with no document id is
degraded attribution, never a wrong number (``SF-OPS.7``).

Nothing in this module may raise into the caller. A cost record may never be the
reason a generation fails.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterator

from applire.providers.llm.base import LLMProvider
from applire.services.ops.config import usage_tracking_enabled

logger = logging.getLogger(__name__)

# Rough character-per-token ratio used only when a provider reports no usage.
# Deliberately crude: the number is flagged `estimated` and is there to keep a
# day's total from silently reading as zero, not to reconcile an invoice.
_CHARS_PER_TOKEN = 4

# How many consecutive persistence failures disable the sink for this process.
# Without it, an instance whose migration has not run yet pays a failed DB
# round-trip on every LLM call (the "zero overhead" property of US313's AC).
_MAX_CONSECUTIVE_SINK_FAILURES = 3


@dataclass(frozen=True)
class Usage:
    """Normalised token counts for one provider call."""

    prompt_tokens: int
    completion_tokens: int
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class UsageAttribution:
    """What a call belonged to. Every field is optional by design."""

    stage: str = ""
    document_kind: str = ""
    document_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None


_last_usage: ContextVar[Usage | None] = ContextVar("llm_last_usage", default=None)
_attribution: ContextVar[UsageAttribution | None] = ContextVar(
    "llm_usage_attribution", default=None
)

# Set to False after _MAX_CONSECUTIVE_SINK_FAILURES; see _persist().
_sink_failures = 0
_sink_disabled = False


# ── vendor normalisation ──────────────────────────────────────────────────────


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):  # bool is an int; never a token count
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def extract_usage(response: Any) -> Usage | None:
    """Read token counts off a raw provider response. Never raises.

    Handles the three shapes that exist in this codebase:

    * OpenAI-shaped SDK object — ``response.usage.prompt_tokens`` /
      ``.completion_tokens`` (Mistral, OpenAI, OpenRouter, Requesty);
    * Anthropic Messages SDK object — ``response.usage.input_tokens`` /
      ``.output_tokens``;
    * Ollama's parsed JSON body — ``prompt_eval_count`` / ``eval_count``.

    Returns ``None`` when the response reports nothing, which is the normal case
    for the mock and replay providers and for any vendor that omits usage. The
    caller then estimates and flags it.
    """
    if response is None:
        return None

    # Ollama: a plain dict, counts at the top level.
    if isinstance(response, dict):
        prompt = _as_int(response.get("prompt_eval_count"))
        completion = _as_int(response.get("eval_count"))
        if prompt is None and completion is None:
            # Some OpenAI-compatible endpoints hand back a dict, not an object.
            nested = response.get("usage")
            if isinstance(nested, dict):
                prompt = _as_int(nested.get("prompt_tokens")) or _as_int(
                    nested.get("input_tokens")
                )
                completion = _as_int(nested.get("completion_tokens")) or _as_int(
                    nested.get("output_tokens")
                )
        if prompt is None and completion is None:
            return None
        return Usage(prompt or 0, completion or 0)

    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    prompt = _as_int(getattr(usage, "prompt_tokens", None))
    completion = _as_int(getattr(usage, "completion_tokens", None))
    if prompt is None and completion is None:
        # Anthropic names them differently.
        prompt = _as_int(getattr(usage, "input_tokens", None))
        completion = _as_int(getattr(usage, "output_tokens", None))
    if prompt is None and completion is None:
        return None
    return Usage(prompt or 0, completion or 0)


def note_usage(response: Any) -> None:
    """Record the usage of the response a provider just received.

    Called from inside each concrete provider at the one line where the raw
    response object exists. Silent and total: any failure here is a monitoring
    failure and must not touch the generation.
    """
    try:
        usage = extract_usage(response)
    except Exception:  # pragma: no cover - defensive; extract_usage cannot raise
        usage = None
    if usage is not None:
        _last_usage.set(usage)


def _estimate(prompt_chars: int, completion_chars: int) -> Usage:
    return Usage(
        prompt_tokens=prompt_chars // _CHARS_PER_TOKEN,
        completion_tokens=completion_chars // _CHARS_PER_TOKEN,
        estimated=True,
    )


# ── attribution ───────────────────────────────────────────────────────────────


@contextmanager
def llm_usage_context(
    *,
    stage: str = "",
    document_kind: str = "",
    document_id: uuid.UUID | str | None = None,
    application_id: uuid.UUID | str | None = None,
) -> Iterator[None]:
    """Attribute every provider call made inside this block.

    Set-and-restore, unlike ``debug_log.set_stage``: leaving the block puts the
    previous attribution back, so an audit tail that runs after a generation
    cannot inherit the generation's document id.
    """

    def _uuid(value: uuid.UUID | str | None) -> uuid.UUID | None:
        if value is None or isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            return None

    token = _attribution.set(
        UsageAttribution(
            stage=stage,
            document_kind=document_kind,
            document_id=_uuid(document_id),
            application_id=_uuid(application_id),
        )
    )
    try:
        yield
    finally:
        _attribution.reset(token)


def current_attribution() -> UsageAttribution:
    """The open attribution, falling back to the debug log's stage label."""
    attribution = _attribution.get()
    if attribution is not None and attribution.stage:
        return attribution
    fallback_stage = ""
    try:
        from applire.providers.llm.debug_log import current_call_site

        fallback_stage = current_call_site()[0] or ""
    except Exception:  # pragma: no cover - defensive
        fallback_stage = ""
    if attribution is None:
        return UsageAttribution(stage=fallback_stage)
    return UsageAttribution(
        stage=fallback_stage,
        document_kind=attribution.document_kind,
        document_id=attribution.document_id,
        application_id=attribution.application_id,
    )


# ── the sink ──────────────────────────────────────────────────────────────────


async def _persist(row: dict[str, Any]) -> None:
    """Write one row. Swallows everything; disables itself after repeated failure."""
    global _sink_failures, _sink_disabled
    if _sink_disabled:
        return
    try:
        from applire.db.session import AsyncSessionLocal
        from applire.models.llm_usage import LlmUsage

        async with AsyncSessionLocal() as db:
            db.add(LlmUsage(**row))
            await db.commit()
        _sink_failures = 0
    except Exception as exc:
        _sink_failures += 1
        if _sink_failures >= _MAX_CONSECUTIVE_SINK_FAILURES:
            _sink_disabled = True
            logger.warning(
                "llm_usage accounting disabled for this process after %d "
                "consecutive write failures (last: %s: %s). Token figures on "
                "the operator panel will not update until the backend is "
                "restarted.",
                _sink_failures,
                type(exc).__name__,
                exc,
            )
        else:
            logger.debug("llm_usage write failed: %s: %s", type(exc).__name__, exc)


def reset_sink_state() -> None:
    """Test hook: forget the failure counter and re-enable the sink."""
    global _sink_failures, _sink_disabled
    _sink_failures = 0
    _sink_disabled = False


# ── the wrapper ───────────────────────────────────────────────────────────────


class UsageRecordingProvider(LLMProvider):
    """Transparent wrapper that writes one ``llm_usage`` row per call.

    Installed **innermost** by ``get_provider()`` — i.e. the debug logger wraps
    this — so ``_LoggingProvider``'s ``getattr(self._inner, "_model", None)``
    keeps working through the ``_model`` property below and the debug log's
    records are byte-identical to before this class existed.
    """

    def __init__(self, inner: LLMProvider) -> None:
        super().__init__(timeout=getattr(inner, "_timeout", 30))
        self._inner = inner

    # Kept so the debug logger (and anything else reading the provider's model)
    # sees through this wrapper unchanged.
    @property
    def _model(self) -> Any:  # type: ignore[override]
        return getattr(self._inner, "_model", None)

    def __getattr__(self, name: str) -> Any:
        # Anything this wrapper does not define is the inner provider's.
        # `_inner` itself must never route here — that would recurse forever if
        # an attribute were read before __init__ finished.
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def acomplete(self, prompt: str, **kwargs: Any) -> str:
        return await self._recorded("acomplete", prompt, kwargs, self._inner.acomplete)

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        return await self._recorded(
            "aparse_json", prompt, kwargs, self._inner.aparse_json
        )

    async def _recorded(
        self,
        method: str,
        prompt: str,
        kwargs: dict[str, Any],
        fn: Callable[..., Awaitable[Any]],
    ) -> Any:
        _last_usage.set(None)
        started = time.monotonic()
        ok = True
        result: Any = None
        try:
            result = await fn(prompt, **kwargs)
            return result
        except Exception:
            ok = False
            raise
        finally:
            try:
                await self._record(method, prompt, kwargs, result, ok, started)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "llm_usage accounting skipped: %s: %s", type(exc).__name__, exc
                )

    async def _record(
        self,
        method: str,
        prompt: str,
        kwargs: dict[str, Any],
        result: Any,
        ok: bool,
        started: float,
    ) -> None:
        duration_ms = int((time.monotonic() - started) * 1000)
        usage = _last_usage.get()
        if usage is None:
            system = kwargs.get("system") or ""
            completion_chars = len(result) if isinstance(result, str) else (
                len(repr(result)) if result is not None else 0
            )
            usage = _estimate(len(prompt) + len(system), completion_chars)
        attribution = current_attribution()
        row = {
            "provider": _provider_family(self._inner),
            "model": str(getattr(self._inner, "_model", "") or "")[:160],
            "stage": attribution.stage[:64],
            "method": method[:16],
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "estimated": usage.estimated,
            "document_kind": attribution.document_kind[:16],
            "document_id": attribution.document_id,
            "application_id": attribution.application_id,
            "duration_ms": duration_ms,
            "ok": ok,
        }
        if not ok:
            _note_error()
        await _sink(row)


def _note_error() -> None:
    """Feed the ops layer's rolling error counter. Never raises."""
    try:
        from applire.services.ops.errors import record_error

        record_error("llm")
    except Exception:  # pragma: no cover - defensive
        pass


def _provider_family(provider: LLMProvider) -> str:
    """'OpenRouterProvider' -> 'openrouter'; the same family label ADR-085 uses.

    Pinned for all six real providers by
    ``test_llm_usage_seam.py::test_the_family_label_matches_the_provider_names``
    — the label is what the operator matches against their invoice, so it may
    not drift when a class is renamed.
    """
    name = type(provider).__name__.lstrip("_")
    for suffix in ("LLMProvider", "Provider"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return (name or "unknown").lower()[:32]


# Indirection so a test can install a memory sink without a database.
_sink: Callable[[dict[str, Any]], Awaitable[None]] = _persist


def set_sink(sink: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    """Test hook: replace the persistence sink."""
    global _sink
    _sink = sink


def restore_sink() -> None:
    """Test hook: put the database sink back."""
    global _sink
    _sink = _persist


def wrap_usage(provider: LLMProvider) -> LLMProvider:
    """Wrap a provider so every call records its token usage (ADR-086 cl. 7)."""
    if not usage_tracking_enabled():
        return provider
    return UsageRecordingProvider(provider)
