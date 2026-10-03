# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ownership API — every row has an owner (ADR-092; frozen interface F4).

Who may read or write an *owned* table is named per execution context, never per
query by habit:

* ``owner_context(user_id)`` / ``set_owner(user_id)`` — the request (or tool call,
  or background task) acts for one user. The five auth dependencies in
  ``applire.auth.deps`` set it (they are ``async def``: a sync dependency would set
  it in a threadpool copy of the context, ADR-091 cl. 4).
* ``unscoped(reason)`` — a declared cross-user access; ``reason`` is a closed
  ``Literal`` (ADR-092 cl. 7). Adding a reason is an ADR amendment.
* ``get_owned(db, Model, id, user_id)`` — one row by id for its owner; a foreign
  id raises ``OwnedNotFound`` exactly like a missing one (S-10).

The **statement guard** (ADR-092 cl. 8a, MD-8) is a ``before_cursor_execute``
listener on the application engine's ``sync_engine`` (never the ``Engine`` class —
``alembic/env.py`` builds its own engine and stays unguarded). Its table regex is
built from the models carrying ``__owned__ = True`` — never hand-written.

**W0 state (Strawberry build 1):** the listener is registered but **disabled** by
the module flag ``GUARD_ENABLED``. Package 3a adds the ORM loader criteria
(defence in depth, cl. 8b) and package 3e flips the flag.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Iterator, Literal, TypeVar, get_args

from fastapi import HTTPException, status

#: The closed list of cross-user access reasons (ADR-092 cl. 7 + cl. 8 `tooling`).
UnscopedReason = Literal[
    "admin-metadata",
    "retention",
    "ops-aggregate",
    "orphan-scan",
    "startup-backfill",
    "job-refcount",
    "migration",
    "tooling",
]
UNSCOPED_REASONS: frozenset[str] = frozenset(get_args(UnscopedReason))

#: Tables that carry ``user_id`` but are NOT owned: read before a user context
#: exists (identity) or instance data attributed to a user (ADR-092 cl. 3, cl. 8a).
IDENTITY_TABLES: frozenset[str] = frozenset(
    {
        "users",
        "auth_sessions",
        "personal_tokens",
        "auth_links",
        "reauth_grants",
        "llm_usage",
        "audit_events",
    }
)

#: W0: the guard is registered but OFF. 3e flips this (ADR-092 cl. 8a).
GUARD_ENABLED: bool = False


@dataclass(frozen=True)
class OwnerContext:
    """Whose rows the current execution context may touch.

    Exactly one of ``user_id`` / ``reason`` is set.
    """

    user_id: uuid.UUID | None = None
    reason: str | None = None

    @property
    def is_unscoped(self) -> bool:
        return self.reason is not None


_owner: ContextVar[OwnerContext | None] = ContextVar("applire_owner", default=None)


class OwnerContextMissing(RuntimeError):
    """Owned-table SQL ran with no owner context (the fail-closed guard, cl. 8a)."""


class OwnedNotFound(HTTPException):
    """A missing **or foreign** id (S-10) — REST 404 ``{"detail": "<kind> not found"}``.

    Subclasses ``HTTPException`` so FastAPI renders it without a handler; the MCP
    door maps it to its ``not_found`` error (ADR-092 cl. 6).
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=f"{kind} not found")


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


def current_owner() -> OwnerContext | None:
    """The owner context of this execution context, or ``None``."""
    return _owner.get()


def set_owner(user_id: uuid.UUID) -> Token:
    """Set the owner for the rest of this context (used by the auth dependencies).

    Returns the ``ContextVar`` token; callers that need to restore use ``reset_owner``.
    """
    if not isinstance(user_id, uuid.UUID):
        raise TypeError(f"owner must be a uuid.UUID, got {type(user_id).__name__}")
    return _owner.set(OwnerContext(user_id=user_id))


def reset_owner(token: Token) -> None:
    _owner.reset(token)


@contextlib.contextmanager
def owner_context(user_id: uuid.UUID) -> Iterator[OwnerContext]:
    """Act for ``user_id`` inside the block (background tasks, MCP tool calls)."""
    token = set_owner(user_id)
    try:
        yield _owner.get()  # type: ignore[misc]
    finally:
        _owner.reset(token)


@contextlib.contextmanager
def unscoped(reason: UnscopedReason) -> Iterator[OwnerContext]:
    """Declared cross-user access for one of the closed reasons (cl. 7)."""
    if reason not in UNSCOPED_REASONS:
        raise ValueError(
            f"unscoped reason {reason!r} is not in the closed list (ADR-092 cl. 7): "
            f"{sorted(UNSCOPED_REASONS)}"
        )
    token = _owner.set(OwnerContext(reason=reason))
    try:
        yield _owner.get()  # type: ignore[misc]
    finally:
        _owner.reset(token)


# ---------------------------------------------------------------------------
# The owned set — derived, never hand-written
# ---------------------------------------------------------------------------


def _mapped_classes() -> list[type]:
    import applire.models  # noqa: F401 — registers every mapper
    from applire.db.session import Base

    return [m.class_ for m in Base.registry.mappers]


def owned_tables() -> frozenset[str]:
    """Table names of every model declaring ``__owned__ = True`` (cl. 3, cl. 8a)."""
    return frozenset(
        cls.__tablename__
        for cls in _mapped_classes()
        if cls.__dict__.get("__owned__") is True
    )


_guard_regex: re.Pattern[str] | None = None


def owned_table_regex() -> re.Pattern[str]:
    """Word-boundary, case-insensitive, longest name first; built from ``owned_tables()``."""
    global _guard_regex
    if _guard_regex is None:
        names = sorted(owned_tables(), key=len, reverse=True)
        if not names:
            raise RuntimeError("no model declares __owned__ = True")
        _guard_regex = re.compile(
            r"\b(" + "|".join(re.escape(n) for n in names) + r")\b", re.IGNORECASE
        )
    return _guard_regex


def check_statement(statement: str) -> None:
    """Raise ``OwnerContextMissing`` if ``statement`` names an owned table with no context."""
    if _owner.get() is not None:
        return
    match = owned_table_regex().search(statement)
    if match:
        raise OwnerContextMissing(
            f"SQL touches owned table {match.group(1)!r} with no owner context — "
            "run it inside owner_context(user_id) or unscoped(<reason>) (ADR-092 cl. 8)"
        )


def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    if not GUARD_ENABLED:
        return
    check_statement(statement)


def install_guard(async_engine: Any) -> None:
    """Register the statement guard on an ``AsyncEngine``'s ``sync_engine`` (idempotent)."""
    from sqlalchemy import event

    target = async_engine.sync_engine
    if not event.contains(target, "before_cursor_execute", _before_cursor_execute):
        event.listen(target, "before_cursor_execute", _before_cursor_execute)


def guard_installed(async_engine: Any) -> bool:
    from sqlalchemy import event

    return event.contains(async_engine.sync_engine, "before_cursor_execute", _before_cursor_execute)


# ---------------------------------------------------------------------------
# Owned reads
# ---------------------------------------------------------------------------

M = TypeVar("M")


async def get_owned(db: Any, Model: type[M], id: Any, user_id: uuid.UUID, *, kind: str | None = None) -> M:
    """The ``Model`` row ``id`` owned by ``user_id``, else ``OwnedNotFound``.

    Missing and foreign ids are indistinguishable (S-10). Soft-deleted rows are
    returned — the caller decides what ``deleted_at`` means for its resource.
    ``kind`` defaults to the table name with ``_`` → space, singular-ish
    (callers pass the user-facing noun, e.g. ``"application"``).
    """
    from sqlalchemy import select

    if Model.__dict__.get("__owned__") is not True:
        raise TypeError(f"{Model.__name__} is not an owned model (__owned__ is not True)")
    if not hasattr(Model, "user_id"):
        raise TypeError(
            f"{Model.__name__} has no user_id column yet (added by migration 0074/0075)"
        )
    label = kind or Model.__tablename__.rstrip("s").replace("_", " ")  # type: ignore[attr-defined]
    row = (
        await db.execute(
            select(Model).where(Model.id == id, Model.user_id == user_id)  # type: ignore[attr-defined]
        )
    ).scalar_one_or_none()
    if row is None:
        raise OwnedNotFound(label)
    return row
