# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Who the stdio agent door acts for (ADR-091 cl. 17, ADR-092 cl. 10, S-5, MD-3).

The MCP process is started by the user's own agent client with
``APPLIRE_AGENT_TOKEN`` in its environment. ``mcp/__main__.py`` calls
:func:`establish` **before** ``mcp.run``: a missing, malformed, unknown, revoked,
wrong-scope token, or one whose owner is disabled/deleted, refuses the start
(:class:`AgentStartRefused` → stderr + exit 1). The identity is then **bound** for
the life of the process.

Every tool call re-checks it (:func:`revalidate`, MD-3) in a session of its own:
the token is resolved again (revoked → refused **on the next call**), the owner
must still be the bound user, and the call then runs inside that user's owner
context (ADR-092). Nothing caches a positive answer across calls.

Three binding kinds:

* ``token`` — the product path (S-5).
* ``harness`` — the fenced NoAuth test harness (ADR-091 cl. 3): ``AUTH_HARNESS`` is
  set, the start-up fences held (``harness.enforce_at_startup``) and **no**
  ``APPLIRE_AGENT_TOKEN`` was given. The door acts as the stub user and re-checks
  fence (b) — "no account holds a credential" — on every call, exactly like the
  HTTP harness provider. A given token always wins over the harness.
* unbound — only reachable **in-process** (unit tests, scripts importing the
  server), because the stdio entry point always binds before serving. It resolves
  to the harness stub user **only** while the harness flag is set and the
  configured database passes harness fence (c) (a test database); otherwise every
  call is refused. No database is read on this path.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.user import User

#: The stderr text for a refused start (W0-B COPY.md proposal, 4b).
START_REFUSED_MESSAGE = (
    "Applire MCP: no valid APPLIRE_AGENT_TOKEN. Create an agent token in Applire "
    "under Settings → Tokens and put it in your MCP client's env."
)

#: What a tool call answers once the token stopped being valid mid-session.
CALL_REFUSED_MESSAGE = (
    "Applire refused this call: the agent token is no longer valid (revoked, or "
    "its account was disabled). Create a new agent token in Applire under "
    "Settings → Tokens, put it in your MCP client's env and restart the server."
)

#: The harness lost fence (b) after the start (someone claimed the instance).
HARNESS_CLAIMED_MESSAGE = (
    "Applire refused this call: harness disabled: instance claimed. Start the MCP "
    "server with an agent token (APPLIRE_AGENT_TOKEN)."
)

#: In-process use without a binding and without the harness.
UNBOUND_MESSAGE = (
    "Applire refused this call: the MCP server has no agent identity. Start it "
    "with `python -m applire.mcp` and APPLIRE_AGENT_TOKEN set."
)

ACTIVITY_INTERVAL = timedelta(hours=1)  # ADR-091 cl. 21: last_active_at ≤ 1/h


class AgentStartRefused(Exception):
    """The process must not serve: print :data:`START_REFUSED_MESSAGE`, exit 1."""


class AgentCallRefused(Exception):
    """This tool call must not run; ``str(exc)`` is the agent-facing message."""


@dataclass(frozen=True)
class AgentIdentity:
    user_id: uuid.UUID
    via: Literal["token", "harness"]
    # The raw token is kept in process memory only, for the per-call re-check; it
    # is never logged and never part of repr().
    _raw: str | None = field(default=None, repr=False)


_bound: AgentIdentity | None = None

#: The live ``User`` row the current tool call acts for (set by :func:`acting_as`).
_call_user: ContextVar[User | None] = ContextVar("applire_mcp_call_user", default=None)


def bound() -> AgentIdentity | None:
    return _bound


def bind(identity: AgentIdentity | None) -> None:
    """Install (or clear, with ``None``) the process identity."""
    global _bound
    _bound = identity


async def establish(db: AsyncSession, raw: str | None) -> AgentIdentity:
    """Resolve the start-up credential and bind it; :class:`AgentStartRefused` otherwise.

    The caller has already run ``harness.enforce_at_startup(db)`` (which raises
    when ``AUTH_HARNESS`` is set and a fence fails).
    """
    from applire.auth.tokens import InvalidToken, resolve_agent_token
    from applire.config import settings

    raw = (raw or "").strip()
    if raw:
        try:
            user = await resolve_agent_token(db, raw)
        except InvalidToken:
            raise AgentStartRefused(START_REFUSED_MESSAGE) from None
        identity = AgentIdentity(user_id=user.id, via="token", _raw=raw)
    elif settings.auth_harness:
        from applire.auth.harness import STUB_USER_ID

        identity = AgentIdentity(user_id=STUB_USER_ID, via="harness")
    else:
        raise AgentStartRefused(START_REFUSED_MESSAGE)
    bind(identity)
    return identity


def _unbound_harness_allowed() -> bool:
    from applire.auth.harness import database_fence_reason
    from applire.config import settings

    return bool(settings.auth_harness) and database_fence_reason(settings.database_url) is None


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def _touch_activity(db: AsyncSession, user: User) -> None:
    """``users.last_active_at`` from MCP calls, at most once an hour (cl. 21)."""
    now = datetime.now(timezone.utc)
    last = _aware(getattr(user, "last_active_at", None))
    if last is not None and now - last < ACTIVITY_INTERVAL:
        return
    await db.execute(update(User).where(User.id == user.id).values(last_active_at=now))
    await db.commit()
    user.last_active_at = now


async def revalidate(db: AsyncSession) -> User:
    """The live user this call acts for, re-checked now (MD-3); else :class:`AgentCallRefused`."""
    identity = _bound
    if identity is None:
        if not _unbound_harness_allowed():
            raise AgentCallRefused(UNBOUND_MESSAGE)
        from applire.auth.harness import stub_user

        return stub_user()
    if identity.via == "token":
        from applire.auth.tokens import InvalidToken, resolve_agent_token

        try:
            user = await resolve_agent_token(db, identity._raw or "")
        except InvalidToken:
            raise AgentCallRefused(CALL_REFUSED_MESSAGE) from None
        if user.id != identity.user_id:  # pragma: no cover — a token has one owner
            raise AgentCallRefused(CALL_REFUSED_MESSAGE)
        await _touch_activity(db, user)
        return user
    # harness
    from applire.auth.harness import STUB_USER_ID, any_credential

    if await any_credential(db):
        raise AgentCallRefused(HARNESS_CLAIMED_MESSAGE)
    user = await db.get(User, STUB_USER_ID)
    if user is None:
        from applire.auth.harness import stub_user

        return stub_user()
    return user


def current_user() -> User | None:
    """The user of the running tool call (``None`` outside one)."""
    return _call_user.get()


def set_call_user(user: User):
    return _call_user.set(user)


def reset_call_user(token) -> None:  # noqa: ANN001
    _call_user.reset(token)
