# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The consent at the moment a line is written (ADR-091/096 amended 2026-11-18).

The auth door reads a person's consent once per request or tool call. Work that
outlives that moment — an import job, a generation, a background re-audit —
asks here before each content line and each model call, so a withdrawal takes
effect while the work is running:

* in the process that records the withdrawal, at once (:func:`note_withdrawn`);
* in every other process (a second web worker, an agent-channel process),
  within :data:`TTL` seconds: a model call re-reads the stored state when the
  cached one is older than that, and :func:`run_refresher` re-reads it for the
  people this process is working for, so a content line between two model
  calls is held back too.

After a withdrawal no further content line or model-call line is written for
the person, and no further model call is started for them: running work ends
at its next model call with ``log_consent_required``; what it stored before
stays. A database that cannot be read keeps the last known state.

A request or tool call that IS model work (its door admitted it strictly) is
*watched* (:func:`watching`, :func:`arm`): a refusal at the call seam is noted
there, so the request boundary answers it as the refusal whatever the code in
between did with the exception (a broad ``except``, a fallback). A write that
only layers model work on top is not armed: its write stands, its model step
is left out.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from contextvars import ContextVar
from typing import Iterable, Iterator

logger = logging.getLogger(__name__)

#: Seconds a stored consent state is trusted before it is read again.
TTL = 2.0
#: A person this process worked for within this many seconds is refreshed.
ACTIVE_WINDOW = 3600.0

#: user id → (consent ok, monotonic time the state was read)
_state: dict[uuid.UUID, tuple[bool, float]] = {}
#: user id → monotonic time a door last admitted them with a consent
_active: dict[uuid.UUID, float] = {}


class Watch:
    """One request's or tool call's record of a refusal at the call seam."""

    __slots__ = ("strict", "refusal")

    def __init__(self) -> None:
        self.strict = False
        self.refusal = None

    @property
    def refused(self):
        """The refusal to answer with, when the work was strict model work."""
        return self.refusal if self.strict else None


_watch: ContextVar[Watch | None] = ContextVar("applire_consent_watch", default=None)


@contextlib.contextmanager
def watching() -> Iterator[Watch]:
    watch = Watch()
    token = _watch.set(watch)
    try:
        yield watch
    finally:
        _watch.reset(token)


def arm() -> None:
    """The current request or tool call is model work (a strict door admitted it)."""
    watch = _watch.get()
    if watch is not None:
        watch.strict = True


def note_refused(exc) -> None:
    """The call seam refused a model call after a withdrawal."""
    watch = _watch.get()
    if watch is not None and watch.refusal is None:
        watch.refusal = exc


def _now() -> float:
    return time.monotonic()


def note_door(user_id: uuid.UUID, consent_ok: bool) -> None:
    """The auth door read the stored consent (``allowance.load_facts``)."""
    now = _now()
    _state[user_id] = (consent_ok, now)
    if consent_ok:
        _active[user_id] = now


def note_withdrawn(user_id: uuid.UUID) -> None:
    """The withdrawal (or the account's deletion) was stored in this process."""
    _state[user_id] = (False, _now())


def note_consented(user_id: uuid.UUID) -> None:
    _state[user_id] = (True, _now())
    _active[user_id] = _now()


def revoked(user_id: uuid.UUID | None) -> bool:
    """True when the last known state says the person's consent is gone."""
    if user_id is None:
        return False
    entry = _state.get(user_id)
    return entry is not None and not entry[0]


async def refresh(user_ids: Iterable[uuid.UUID], *, session_factory=None) -> None:
    """Re-read the stored consent of ``user_ids``; a deleted account reads as gone."""
    ids = list(dict.fromkeys(user_ids))
    if not ids:
        return
    from sqlalchemy import select

    from applire.models.user import User
    from applire.services.log_consent import consent_ok

    if session_factory is None:
        from applire.db.session import AsyncSessionLocal as session_factory
    try:
        async with session_factory() as db:
            rows = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    except Exception as exc:  # noqa: BLE001 — keep the last known state
        logger.warning("consent refresh could not read the database (%s)", type(exc).__name__)
        return
    now = _now()
    found = {row.id: consent_ok(row) and getattr(row, "disabled_at", None) is None for row in rows}
    for uid in ids:
        _state[uid] = (found.get(uid, False), now)


async def allowed_to_call(user_id: uuid.UUID | None, *, session_factory=None) -> bool:
    """Before a model call: the stored consent, at most :data:`TTL` seconds old.

    A person this process has no state for (no door ran for them here) is
    decided by the payer facts alone, as before."""
    if user_id is None:
        return True
    entry = _state.get(user_id)
    if entry is None:
        return True
    if _now() - entry[1] > TTL:
        await refresh([user_id], session_factory=session_factory)
    return not revoked(user_id)


async def run_refresher(stop: asyncio.Event | None = None, *, session_factory=None) -> None:
    """Every :data:`TTL` seconds, re-read the consent of the people this process
    worked for recently (content logging on only)."""
    from applire.content_log import content_logging_on

    while stop is None or not stop.is_set():
        try:
            if content_logging_on():
                cutoff = _now() - ACTIVE_WINDOW
                for uid in [u for u, t in _active.items() if t < cutoff]:
                    _active.pop(uid, None)
                await refresh(list(_active), session_factory=session_factory)
        except Exception as exc:  # noqa: BLE001 — the refresher must keep running
            logger.warning("consent refresher iteration failed (%s)", type(exc).__name__)
        try:
            if stop is None:
                await asyncio.sleep(TTL)
            else:
                await asyncio.wait_for(stop.wait(), timeout=TTL)
        except asyncio.TimeoutError:
            pass


def reset_for_tests() -> None:
    _state.clear()
    _active.clear()


class RefusalBoundary:
    """Pure ASGI middleware: an HTTP request that is model work answers a
    refusal at the call seam as the refusal (``409 log_consent_required``),
    whatever the code in between made of it: an error answer, a fallback that
    went on without the model, or an exception. Pure ASGI so the watch set here
    is the context the route and its dependencies run in."""

    def __init__(self, app) -> None:  # noqa: ANN001 — ASGI app
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        with watching() as watch:
            state = {"started": False, "replaced": False}

            async def guarded(message) -> None:  # noqa: ANN001
                if state["replaced"]:
                    return  # the original answer's body
                if message["type"] == "http.response.start":
                    state["started"] = True
                    refusal = watch.refused
                    if refusal is not None and message.get("status") != refusal.status_code:
                        state["replaced"] = True
                        await _answer_refusal(send, refusal, scope)
                        return
                await send(message)

            try:
                await self.app(scope, receive, guarded)
            except Exception:
                refusal = watch.refused
                if refusal is None or state["started"]:
                    raise
                await _answer_refusal(send, refusal, scope)


async def _answer_refusal(send, refusal, scope) -> None:  # noqa: ANN001
    import json

    route = getattr(scope.get("route"), "path_format", None) or "[no route]"
    logger.info(
        "model work refused after a consent withdrawal [route=%s %s] [code=%s]",
        scope.get("method", "?"), route, refusal.code,
    )
    body = json.dumps({"detail": refusal.detail}).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": refusal.status_code,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    })
    await send({"type": "http.response.body", "body": body})
