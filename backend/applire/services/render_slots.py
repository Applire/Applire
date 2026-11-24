# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""How many document generations run at once (adv/plans F2, ADR-096 cl. 8).

A generation's background render holds one database connection for its whole
run (minutes of model work), and every model call's usage row takes a second
one for a moment. One process has SQLAlchemy's default pool (5 + 10 overflow;
the image runs a single uvicorn worker). A person who starts many generations
at once — regenerations are free — used to take the whole pool: pool timeouts,
renders that died without a status, and the usage counter paused for everyone.

So a render waits for two slots BEFORE it opens its session (the precedent is
the per-user import lock, ``services/profile/import_jobs.py``): one per person
and document kind, and one of the process's few render slots. A waiting render
holds no connection and its record stays ``pending``, which is the truth. No
request is refused and nothing new is shown: the generations of one person run
one after another; other people's renders and every request keep their
connections.

What the person sees while a render waits: the record is ``pending``, so the
generation view keeps its progress state, exactly as for a render that has
just started. The wait is bounded: a render that has no slot after
``MAX_WAIT_SECONDS`` (8 min) gives up — it never runs, its record turns
``failed`` (the view offers the retry) — so a waiting render is never shown as
failed by ``get_cv_status``'s 10-minute check while it still waits, and never
runs after the reaper (31 min) has failed it. The debit of a generation is
committed in the request before the render is scheduled, so a waiting render
holds neither a connection nor the subject's advisory lock.

In-process only, like the import lock: one worker. The slot tables are keyed by
event loop (weakly), because an ``asyncio.Semaphore`` belongs to the loop it
was first used on; tests run a loop per test.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

#: Renders of one person and one document kind at a time.
PER_PERSON = 1
#: Renders of the whole process at a time. Each holds up to two connections,
#: so 4 renders use at most 8 of the pool's 15 and leave the rest to requests.
INSTANCE = 4
#: The longest a render waits for its slots; below the 10-minute inline stale
#: check of ``get_cv_status`` (and the reaper's 31 minutes).
MAX_WAIT_SECONDS = 8 * 60

logger = logging.getLogger(__name__)


class SlotWaitTimeout(Exception):
    """No slot within ``MAX_WAIT_SECONDS``: the render gives up and never runs."""

_State = dict[str, Any]
_tables: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _State]" = weakref.WeakKeyDictionary()

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def _state() -> _State:
    loop = asyncio.get_running_loop()
    state = _tables.get(loop)
    if state is None:
        state = _tables.setdefault(loop, {"instance": asyncio.Semaphore(INSTANCE), "people": {}})
    return state


@contextlib.asynccontextmanager
async def render_slot(user_id: uuid.UUID | None, kind: str) -> AsyncIterator[None]:
    """Wait for the person's slot for ``kind``, then for an instance slot —
    together at most ``MAX_WAIT_SECONDS``, else :class:`SlotWaitTimeout`."""
    state = _state()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + MAX_WAIT_SECONDS
    sems: list[asyncio.Semaphore] = []
    if user_id is not None:
        key = (user_id, kind)
        sem = state["people"].get(key)
        if sem is None:
            sem = state["people"].setdefault(key, asyncio.Semaphore(PER_PERSON))
        sems.append(sem)
    sems.append(state["instance"])
    held: list[asyncio.Semaphore] = []
    try:
        for sem in sems:
            try:
                await asyncio.wait_for(sem.acquire(), max(0.0, deadline - loop.time()))
            except asyncio.TimeoutError:
                raise SlotWaitTimeout(kind) from None
            held.append(sem)
        yield
    finally:
        for sem in reversed(held):
            sem.release()


async def _give_up(kind: str, record_id: Any, user_id: uuid.UUID | None) -> None:
    """Mark a render that never got its slot ``failed`` (only while ``pending``:
    the person may have deleted it, a reaper may have ended it)."""
    await _mark_failed(
        kind, record_id, user_id, ("pending",), "Generation gave up waiting for a render slot"
    )


async def _settle(kind: str, record_id: Any, user_id: uuid.UUID | None) -> None:
    """After the render body has exited — returned or raised — a record that is
    still ``pending`` or ``generating`` belongs to a render that died before (or
    while) marking its own failure, e.g. a pool timeout while loading the
    record. It is marked ``failed`` (the view offers the retry); a ``ready`` or
    already ``failed`` record is left as it is."""
    await _mark_failed(
        kind, record_id, user_id, ("pending", "generating"),
        "The generation stopped before it finished", error_code="generation_failed",
    )


#: How long a failure mark is retried while the database is busy (a pool
#: timeout is the usual reason a render died), and the pause between tries.
SETTLE_SECONDS = 60.0
SETTLE_RETRY_SECONDS = 2.0


def _transient(exc: BaseException) -> bool:
    """The database is busy or unreachable for now (worth another try): a pool
    timeout, a lost or refused connection. A schema or data error is not."""
    from sqlalchemy.exc import DBAPIError, InterfaceError
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    if isinstance(exc, (PoolTimeout, InterfaceError, TimeoutError, OSError)):
        return True
    return isinstance(exc, DBAPIError) and bool(exc.connection_invalidated)


async def _mark_failed(
    kind: str,
    record_id: Any,
    user_id: uuid.UUID | None,
    statuses: tuple[str, ...],
    message: str,
    *,
    error_code: str | None = None,
) -> None:
    if not isinstance(record_id, uuid.UUID) or user_id is None:
        return  # the stale-job reaper still ends it
    from sqlalchemy import update

    from applire.ownership import owner_context

    if kind == "cv":
        from applire.models.cv import GeneratedCV as Model
    else:
        from applire.models.cover_letter import GeneratedCoverLetter as Model
    values: dict[str, Any] = {"status": "failed", "error_message": message}
    if error_code is not None and hasattr(Model, "error_code"):
        values["error_code"] = error_code
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SETTLE_SECONDS
    while True:
        try:
            from applire.db import session as db_session

            with owner_context(user_id):
                async with db_session.AsyncSessionLocal() as db:
                    await db.execute(
                        update(Model).where(Model.id == record_id, Model.status.in_(statuses)).values(**values)
                    )
                    await db.commit()
            return
        except Exception as exc:  # noqa: BLE001 — the reaper is the second line
            if not _transient(exc) or loop.time() + SETTLE_RETRY_SECONDS > deadline:
                logger.warning(
                    "could not mark the %s render %s failed (%s)", kind, record_id, type(exc).__name__,
                    exc_info=True,
                )
                return
        await asyncio.sleep(SETTLE_RETRY_SECONDS)


def bounded_render(kind: str) -> Callable[[F], F]:
    """Decorate a background render whose first argument is its record id and
    whose ``user_id`` keyword names its person."""

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            user_id = kwargs.get("user_id")
            if user_id is None:
                from applire.ownership import current_owner

                owner = current_owner()
                user_id = owner.user_id if owner is not None else None
            entered = False
            record_id = args[0] if args else None
            try:
                async with render_slot(user_id, kind):
                    entered = True
                    return await fn(*args, **kwargs)
            except SlotWaitTimeout:
                if entered:
                    raise
            finally:
                if entered:  # every exit after the slot ends an unfinished record
                    await _settle(kind, record_id, user_id)
            logger.warning("%s render %s gave up: no render slot within %d s", kind, record_id, MAX_WAIT_SECONDS)
            await _give_up(kind, record_id, user_id)
            return None

        wrapper.__applire_render_slot__ = kind  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorate


def reset_for_tests() -> None:
    _tables.clear()
