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

import asyncio
import contextvars
import logging
import time
import uuid
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterator

from applire.providers.llm.base import LLMProvider
from applire.providers.llm.reasoning import extract_reasoning_tokens
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
    # How many of `completion_tokens` the provider says went to reasoning
    # rather than to the answer (founder ruling M-4). Providers that bill
    # thinking inside the completion total without splitting it (Anthropic) and
    # providers with no reasoning at all (Ollama, mock) report 0 — an unknown
    # split is never invented, because the number's only job is to tell the
    # operator what reasoning costs them.
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        # `reasoning_tokens` is a SUBSET of `completion_tokens` on every
        # provider that reports it, never an addition — adding it here would
        # double-count the thinking against the operator's invoice.
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class UsageAttribution:
    """What a call belonged to. Every field is optional by design."""

    stage: str = ""
    document_kind: str = ""
    document_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None
    #: The account the call was made for (S-8: one shared operator key,
    #: per-user visibility; ADR-092 table — ``llm_usage.user_id``).
    user_id: uuid.UUID | None = None


_last_usage: ContextVar[Usage | None] = ContextVar("llm_last_usage", default=None)
_attribution: ContextVar[UsageAttribution | None] = ContextVar(
    "llm_usage_attribution", default=None
)

# The sink is a queue with ONE writer task per process (adv/plans re-probe N2,
# N3). A model call only appends its row: it never takes a second database
# connection while its caller holds one (under a burst every caller held one
# and waited for a second — the pool ran dry), and a row that cannot be written
# now is KEPT and written later, never dropped (a dropped row was instance-paid
# work the daily cap never saw).
#
# The writer pauses after _MAX_CONSECUTIVE_SINK_FAILURES. While paused,
# admission of instance-paid work is refused (ADR-096 cl. 8 fails closed) and
# rows keep queueing. After the cool-down the writer makes ONE try: the head of
# the queue, or with an empty queue a probe (insert, flush, roll back). One
# success resumes and the backlog is written; a failed try pauses again with a
# doubled cool-down (capped). Admission (sink_available) opens only after that
# success, never on the clock alone (security review: otherwise instance-paid
# work ran uncounted while the sink was still broken and the cap did not bind).
#
# Bounded: at _QUEUE_HIGH queued rows admission is refused as well (no new
# instance-paid work while the backlog is not written); at _QUEUE_MAX the
# oldest row is dropped with an ERROR (only reachable by work admitted before
# the pause, or under ALLOWANCE_POLICY=none, which refuses nothing). Rows still
# queued when the process is killed are lost; a normal shutdown drains them
# (drain(), lifespan).
_SINK_COOLDOWN_START = 30.0
_SINK_COOLDOWN_MAX = 300.0
_QUEUE_HIGH = 1_000
_QUEUE_MAX = 20_000
_BATCH = 200
#: Pause between two tries below the pause threshold.
_RETRY_PAUSE = 0.5
_sink_failures = 0
_sink_disabled = False
_sink_paused_at = 0.0
_sink_cooldown = _SINK_COOLDOWN_START
_queue: "deque[dict[str, Any]]" = deque()
#: Rows still to be written one at a time: the length of the last batch that
#: failed, walked row by row once so a row of its own fault is found without
#: starting over at full batch size after every good row (re-probe 2, R2-2).
_single_rows = 0
_dropped = 0
#: A dropped row's tokens are counted at most at what one written row can hold
#: (``llm_usage.total_tokens`` is a 32-bit integer): an absurd figure still
#: stops the day's instance-paid work, it never breaks the daily total itself.
_MAX_ROW_TOKENS = 2**31 - 1
#: A try is in flight (one at a time, whoever asks).
_stepping = False
#: The writer task (one per process; re-created on another event loop).
_writer: "asyncio.Task[None] | None" = None
#: Tests drive the writer step by step (``_step``) with this off.
_autostart = True


def _sink_clock() -> float:
    return time.monotonic()


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _cooling() -> bool:
    return _sink_disabled and _sink_clock() - _sink_paused_at < _sink_cooldown


def sink_available() -> bool:
    """True only while the writer's last write (or probe) succeeded and the
    backlog is short — the counter fails closed otherwise. A paused sink's
    writer makes its one try after the cool-down; this stays false until that
    try has succeeded."""
    if _sink_disabled:
        _ensure_writer()
        return False
    return len(_queue) < _QUEUE_HIGH


def pending_instance_tokens() -> int:
    """Tokens of queued rows that the instance or the system paid (the daily
    cap adds them to the written total, so a backlog never under-counts)."""
    return sum(int(r.get("total_tokens") or 0) for r in list(_queue) if r.get("payer") != "user")


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
        return Usage(
            prompt or 0,
            completion or 0,
            reasoning_tokens=extract_reasoning_tokens(response) or 0,
        )

    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    prompt = _as_int(getattr(usage, "prompt_tokens", None))
    completion = _as_int(getattr(usage, "completion_tokens", None))
    if prompt is None and completion is None:
        # Anthropic names them differently, and its `input_tokens` is only the
        # UNCACHED remainder: the cache write and the cache read are reported
        # beside it. Summed, the row keeps meaning "prompt tokens processed"
        # once the adapter caches the system prompt (ADR-009 amended 2026-11-06).
        prompt = _as_int(getattr(usage, "input_tokens", None))
        completion = _as_int(getattr(usage, "output_tokens", None))
        if prompt is not None:
            for field in ("cache_creation_input_tokens", "cache_read_input_tokens"):
                prompt += _as_int(getattr(usage, field, None)) or 0
    if prompt is None and completion is None:
        return None
    return Usage(
        prompt or 0,
        completion or 0,
        reasoning_tokens=extract_reasoning_tokens(response) or 0,
    )


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
    user_id: uuid.UUID | str | None = None,
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
            user_id=_uuid(user_id),
        )
    )
    try:
        yield
    finally:
        _attribution.reset(token)


def _context_user() -> uuid.UUID | None:
    """The USER owner context of this call (ADR-092 cl. 8) — the auth dependency,
    the MCP identity and every background task set it — else ``None``
    (unscoped / tooling calls stay unattributed, never guessed)."""
    try:
        from applire import ownership

        ctx = ownership.current_owner()
    except Exception:  # pragma: no cover - defensive: accounting never raises
        return None
    return ctx.user_id if ctx is not None and not ctx.is_unscoped else None


def current_attribution() -> UsageAttribution:
    """The open attribution, falling back to the debug log's stage label.

    ``user_id``: an explicit ``llm_usage_context(user_id=…)`` wins, else the
    user owner context of the call (S-8 per-user visibility).
    """
    attribution = _attribution.get()
    if attribution is not None and attribution.stage:
        if attribution.user_id is None:
            uid = _context_user()
            if uid is not None:
                attribution = replace(attribution, user_id=uid)
        return attribution
    fallback_stage = ""
    try:
        from applire.providers.llm.debug_log import current_call_site

        fallback_stage = current_call_site()[0] or ""
    except Exception:  # pragma: no cover - defensive
        fallback_stage = ""
    if attribution is None:
        return UsageAttribution(stage=fallback_stage, user_id=_context_user())
    return UsageAttribution(
        stage=fallback_stage,
        document_kind=attribution.document_kind,
        document_id=attribution.document_id,
        application_id=attribution.application_id,
        user_id=attribution.user_id or _context_user(),
    )


# ── the sink ──────────────────────────────────────────────────────────────────


async def _persist(row: dict[str, Any]) -> None:
    """Queue one row for the writer. Never touches the database, never raises."""
    global _dropped
    row = dict(row)
    row.setdefault("created_at", datetime.now(timezone.utc))  # the call's time, not the write's
    if len(_queue) >= _QUEUE_MAX:
        _queue.popleft()
        _dropped += 1
        if _dropped == 1 or _dropped % 1000 == 0:
            logger.error(
                "llm_usage backlog full (%d rows): %d oldest row(s) dropped uncounted so far",
                _QUEUE_MAX, _dropped,
            )
    _queue.append(row)
    _ensure_writer()


def _ensure_writer() -> None:
    global _writer
    if not _autostart:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no loop (a sync caller): the next async call starts it
        return
    if _writer is not None and not _writer.done() and _writer.get_loop() is loop:
        return
    # An empty context: the writer outlives the request that started it and
    # writes identity-table rows only (llm_usage, the instance's daily total).
    _writer = loop.create_task(_run_writer(), context=contextvars.Context())


async def _run_writer() -> None:
    """Write the queue until it is empty and the sink is open."""
    while _queue or _sink_disabled:
        if _cooling():
            await _sleep(min(_sink_cooldown - (_sink_clock() - _sink_paused_at), 1.0))
            continue
        if not await _step() and not _sink_disabled:
            await _sleep(_RETRY_PAUSE)


async def _write_rows(rows: list[dict[str, Any]]) -> None:
    """One transaction: the rows and the instance's daily totals they add to."""
    from applire.db.session import AsyncSessionLocal
    from applire.models.llm_usage import LlmUsage

    async with AsyncSessionLocal() as db:
        for row in rows:
            db.add(LlmUsage(**row))
        # ADR-096 cl. 8 (phase 2): the instance's daily total, same transaction.
        await _add_daily(db, rows)
        await db.commit()


async def _add_daily(db: Any, rows: list[dict[str, Any]]) -> None:
    """Add the rows' tokens to the instance's daily totals: per payer and per
    the LOCAL day each call ran on — the day function the cap reads
    (``allowance._today_local``), so a row queued before midnight and written
    after it counts for the day it ran on."""
    from applire.services.allowance import _today_local, add_daily_tokens

    daily: dict[tuple[str, Any], tuple[int, Any]] = {}
    for row in rows:
        at = row.get("created_at") if isinstance(row.get("created_at"), datetime) else None
        key = (row.get("payer", ""), _today_local(at))
        tokens, first = daily.get(key, (0, at))
        daily[key] = (tokens + int(row.get("total_tokens") or 0), first)
    for (payer, _day), (tokens, at) in daily.items():
        await add_daily_tokens(db, payer=payer, tokens=tokens, now=at)


async def _probe() -> None:
    """Insert a row, flush, roll back — the same connection, table and insert
    path as a write, nothing counted."""
    from applire.db.session import AsyncSessionLocal
    from applire.models.llm_usage import LlmUsage

    async with AsyncSessionLocal() as db:
        db.add(LlmUsage(provider="sink_probe", stage="sink_probe"))
        await db.flush()
        await db.rollback()


async def _step() -> bool:
    """One try of the writer: the head of the queue (a batch; one row after a
    failure), or a probe while paused with an empty queue. True on success."""
    global _stepping
    if _cooling() or _stepping:
        return False
    _stepping = True
    try:
        return await _try_head()
    finally:
        _stepping = False


async def _try_head() -> bool:
    global _single_rows
    retrying = _sink_disabled
    size = 1 if _single_rows > 0 else _BATCH
    batch = [_queue[i] for i in range(min(size, len(_queue)))]
    if not batch and not retrying:
        return False
    try:
        if batch:
            await _write_rows(batch)
        else:
            await _probe()
    except Exception as exc:
        if len(batch) == 1 and await _the_row_is_the_problem(batch[0], exc):
            _single_rows = max(0, _single_rows - 1)
            _succeeded("a per-row check")  # the sink itself works
            return True
        if len(batch) > 1:  # walk this batch row by row, once, to find the row
            _single_rows = len(batch)
        _failed(exc, retrying)
        return False
    for _ in batch:
        _queue.popleft()
    _single_rows = max(0, _single_rows - len(batch))
    _succeeded("a write" if batch else "a probe")
    return True


#: SQLSTATE classes that name the row, not the database: 22 data exception
#: (value too long, out of range, invalid byte sequence), 23 integrity
#: constraint violation. Everything else — 08 connection, 40 serialization /
#: deadlock, 53 resources, 57 cancelled / starting up — is the sink's.
_ROW_SQLSTATE_CLASSES = ("22", "23")


def _sqlstate(exc: Exception) -> str | None:
    orig = getattr(exc, "orig", None)
    state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return state if isinstance(state, str) and state else None


def _is_row_error(exc: Exception) -> bool:
    """A definite per-row error: the database rejected a value of the row.
    With asyncpg a data error arrives as a plain ``DBAPIError`` whose
    ``orig.sqlstate`` names it; a dialect without a SQLSTATE raises
    ``IntegrityError`` / ``DataError``. A connection that went away meanwhile
    is never the row's fault."""
    from sqlalchemy.exc import DataError, DBAPIError, IntegrityError

    if not isinstance(exc, DBAPIError) or exc.connection_invalidated:
        return False
    state = _sqlstate(exc)
    if state is not None:
        return state[:2] in _ROW_SQLSTATE_CLASSES
    return isinstance(exc, (IntegrityError, DataError))


async def _the_row_is_the_problem(row: dict[str, Any], exc: Exception) -> bool:
    """A single row failed. Only a definite per-row error (an integrity or
    data error) can be the row's own; anything else — a timeout, a lost
    connection, a serialization failure — is the sink's, and the row stays.

    * an integrity error and the row's account is really gone (erased while
      the row was queued): the row is written unattributed next, as the foreign
      key's ON DELETE SET NULL would have stored it;
    * otherwise the row can never be written: it is dropped with an ERROR, and
      its tokens are still added to the daily total (in their own transaction,
      which also proves the sink works).

    False when the sink is the problem (the caller counts a failure)."""
    from sqlalchemy.exc import IntegrityError

    if not _is_row_error(exc):
        return False
    from applire.db.session import AsyncSessionLocal

    try:
        uid = row.get("user_id")
        state = _sqlstate(exc)
        if (state == "23503" or (state is None and isinstance(exc, IntegrityError))) and uid is not None:
            from applire.models.user import User

            async with AsyncSessionLocal() as db:
                if await db.get(User, uid) is None:
                    row["user_id"] = None  # the next step writes it unattributed
                    return True
        counted = dict(row, total_tokens=min(max(int(row.get("total_tokens") or 0), 0), _MAX_ROW_TOKENS))
        async with AsyncSessionLocal() as db:
            await _add_daily(db, [counted])
            await db.commit()
    except Exception:
        return False
    _queue.popleft()
    logger.error(
        "llm_usage row could not be written and is dropped (its %d tokens are counted): %s",
        int(row.get("total_tokens") or 0),
        type(exc).__name__,
    )
    return True


def _failed(exc: Exception, retrying: bool) -> None:
    global _sink_failures, _sink_disabled, _sink_paused_at, _sink_cooldown
    _sink_failures += 1
    if retrying or _sink_failures >= _MAX_CONSECUTIVE_SINK_FAILURES:
        _sink_cooldown = min(_sink_cooldown * 2, _SINK_COOLDOWN_MAX) if retrying else _SINK_COOLDOWN_START
        _sink_disabled = True
        _sink_paused_at = _sink_clock()
        logger.warning(
            "llm_usage accounting paused after %d consecutive write failures "
            "(last: %s: %s); %d row(s) are kept and written when a write "
            "succeeds again, tried after %.0f s. Under ALLOWANCE_POLICY=allowance "
            "instance-paid AI work is refused until then.",
            _sink_failures,
            type(exc).__name__,
            exc,
            len(_queue),
            _sink_cooldown,
        )
    else:
        logger.debug("llm_usage write failed: %s: %s", type(exc).__name__, exc)


def _succeeded(what: str) -> None:
    global _sink_failures, _sink_disabled, _sink_cooldown
    if _sink_disabled:
        logger.warning(
            "llm_usage accounting resumed after %d failed writes (%s succeeded); "
            "%d queued row(s) follow",
            _sink_failures,
            what,
            len(_queue),
        )
    _sink_failures = 0
    _sink_disabled = False
    _sink_cooldown = _SINK_COOLDOWN_START


async def drain(timeout: float = 10.0) -> int:
    """Write what is queued, for at most ``timeout`` seconds (shutdown, tests).
    Returns the number of rows still queued."""
    loop = asyncio.get_running_loop()
    if _writer is not None and not _writer.done() and _writer.get_loop() is loop:
        try:  # the writer is at work: wait for it (never a second writer)
            await asyncio.wait_for(asyncio.shield(_writer), timeout)
        except asyncio.TimeoutError:
            pass
    else:
        deadline = loop.time() + timeout
        while _queue and loop.time() < deadline and not _cooling():
            if not await _step():
                await _sleep(min(_RETRY_PAUSE, max(0.0, deadline - loop.time())))
    if _queue:
        logger.warning("llm_usage: %d queued row(s) not written at shutdown", len(_queue))
    return len(_queue)


def reset_sink_state() -> None:
    """Test hook: forget the failure counter, the backlog and re-enable the sink."""
    global _sink_failures, _sink_disabled, _sink_paused_at, _sink_cooldown, _single_rows, _dropped, _writer, _stepping
    _stepping = False
    _sink_failures = 0
    _sink_disabled = False
    _sink_paused_at = 0.0
    _sink_cooldown = _SINK_COOLDOWN_START
    _single_rows = 0
    _dropped = 0
    _queue.clear()
    _writer = None


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
            "reasoning_tokens": usage.reasoning_tokens,
            "total_tokens": usage.total_tokens,
            "estimated": usage.estimated,
            "document_kind": attribution.document_kind[:16],
            "document_id": attribution.document_id,
            "application_id": attribution.application_id,
            "user_id": attribution.user_id,
            "duration_ms": duration_ms,
            "ok": ok,
            # ADR-096 cl. 11: whose key paid (stamped on the provider by the factory).
            "payer": str(getattr(self._inner, "_payer", "") or "")[:16],
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
