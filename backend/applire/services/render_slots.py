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

In-process only, like the import lock: one worker. The slot tables are keyed by
event loop (weakly), because an ``asyncio.Semaphore`` belongs to the loop it
was first used on; tests run a loop per test.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import uuid
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

#: Renders of one person and one document kind at a time.
PER_PERSON = 1
#: Renders of the whole process at a time. Each holds up to two connections,
#: so 4 renders use at most 8 of the pool's 15 and leave the rest to requests.
INSTANCE = 4

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
    """Wait for the person's slot for ``kind``, then for an instance slot."""
    state = _state()
    person = contextlib.nullcontext()
    if user_id is not None:
        key = (user_id, kind)
        sem = state["people"].get(key)
        if sem is None:
            sem = state["people"].setdefault(key, asyncio.Semaphore(PER_PERSON))
        person = sem
    async with person:
        async with state["instance"]:
            yield


def bounded_render(kind: str) -> Callable[[F], F]:
    """Decorate a background render whose ``user_id`` keyword names its person."""

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            user_id = kwargs.get("user_id")
            if user_id is None:
                from applire.ownership import current_owner

                owner = current_owner()
                user_id = owner.user_id if owner is not None else None
            async with render_slot(user_id, kind):
                return await fn(*args, **kwargs)

        wrapper.__applire_render_slot__ = kind  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorate


def reset_for_tests() -> None:
    _tables.clear()
