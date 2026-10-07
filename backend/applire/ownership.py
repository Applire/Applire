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

**State (Strawberry build 1, W3 / package 3e, MD-24):** the guard is **on** —
``GUARD_ENABLED`` defaults to ``True`` and an owned-table statement with no owner
context raises ``OwnerContextMissing``. ``GUARD_REPORT_ONLY`` (default ``False``)
is a test/diagnostic switch only: it turns the enabled guard into a logger (the
violation is logged and kept in ``guard_reports()``, nothing raises). Neither is
an operator setting; the unit suites drive them through
``tests/support/owners.configure_test_guard`` (``APPLIRE_TEST_OWNER_GUARD``).

Three more mechanisms live here, installed on the declarative ``Base`` by
``install_orm_hooks`` (called from ``applire/db/session.py``):

* **Owner fill + chain consistency (cl. 1, SF-OWN.9)** — a ``before_insert``
  mapper listener on every ``__owned__`` model with a ``user_id`` column. A chain
  row (``profile_id`` + ``user_id``) whose ``user_id`` differs from its profile's
  owner raises ``OwnerMismatch`` (checked when the profile is loaded in the
  session); a row inserted with ``user_id`` unset takes the profile's owner (chain
  tables) or else the *user* owner context — the latter counted in ``FILL_STATS``
  and logged (WARNING), so W2/3e can see which constructors still rely on it. With neither,
  the column stays NULL and the database's NOT NULL refuses the row — an
  ``unscoped`` context never names an owner by itself. Always on (it is a model
  rule, not the guard).
* **Loader criteria (cl. 8b, defence in depth, never credited)** — a
  ``do_orm_execute`` hook adds ``with_loader_criteria(Model, Model.user_id == uid)``
  for every owned model on SELECT/UPDATE/DELETE while a *user* context is set and
  the guard is enabled.
"""

from __future__ import annotations

import collections
import contextlib
import logging
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

#: The statement guard is ON (ADR-092 cl. 8a; MD-24 (1), W3). Not an operator
#: setting — a test/diagnostic switch only.
GUARD_ENABLED: bool = True

#: Diagnostic report mode: with ``GUARD_ENABLED`` the guard logs + records instead
#: of raising. Tests/diagnostics only (MD-24 (1)); never set in the running app.
GUARD_REPORT_ONLY: bool = False

#: Owner fills taken from the owner CONTEXT (not a profile), per table — W2/3e read
#: these to decide strict mode (main ruling, W1). Reset with ``FILL_STATS.clear()``.
FILL_STATS: "collections.Counter[str]" = collections.Counter()

_log = logging.getLogger(__name__)

#: Report-mode findings (newest last, bounded) — the W2 isolation run reads these.
_REPORTS_MAX = 500
_reports: list[str] = []


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


class OwnerMismatch(RuntimeError):
    """A chain row names a different owner than its profile (ADR-092 cl. 1, SF-OWN.9)."""


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


def guard_reports() -> list[str]:
    """Report-mode findings so far (a copy)."""
    return list(_reports)


def clear_guard_reports() -> None:
    _reports.clear()


def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    if not GUARD_ENABLED:
        return
    if not GUARD_REPORT_ONLY:
        check_statement(statement)
        return
    try:
        check_statement(statement)
    except OwnerContextMissing as exc:
        _log.warning("ownership guard (report mode): %s", exc)
        _reports.append(f"{exc} :: {statement[:200]}")
        del _reports[:-_REPORTS_MAX]


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


# ---------------------------------------------------------------------------
# ORM hooks — owner fill / chain consistency (cl. 1) and loader criteria (cl. 8b)
# ---------------------------------------------------------------------------


def _context_user_id() -> uuid.UUID | None:
    ctx = _owner.get()
    return ctx.user_id if ctx is not None and not ctx.is_unscoped else None


def _loaded_profile_owner(target: Any) -> uuid.UUID | None:
    """The owner of ``target.profile_id``'s profile if that profile is in the session."""
    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy.orm.util import identity_key

    from applire.models.profile import MasterProfile

    pid = getattr(target, "profile_id", None)
    session = sa_inspect(target).session
    if pid is None or session is None:
        return None
    loaded = session.identity_map.get(identity_key(MasterProfile, pid))
    if loaded is not None:
        return loaded.user_id
    # A profile added in the SAME flush is pending, not yet in the identity map,
    # and — with no FK ordering the two inserts — may not be stored yet either.
    # Without this the fill fell through to the CONTEXT owner and gave the chain
    # row an owner its profile does not have (W3 finding, SF-OWN.9).
    for obj in session.new:
        if isinstance(obj, MasterProfile) and obj.id == pid:
            return obj.user_id
    return None


def _stored_profile_owner(connection: Any, target: Any) -> uuid.UUID | None:
    from sqlalchemy import select

    from applire.models.profile import MasterProfile

    pid = getattr(target, "profile_id", None)
    if pid is None:
        return None
    table = MasterProfile.__table__
    return connection.execute(
        select(table.c.user_id).where(table.c.id == pid)
    ).scalar_one_or_none()


def _before_insert_owner(mapper: Any, connection: Any, target: Any) -> None:
    """Owner fill + chain-owner check (ADR-092 cl. 1).

    * explicit ``user_id`` on a chain row: compared with the profile's owner **when
      the profile is loaded** in the session (the ADR's wording — no extra query);
      a difference raises ``OwnerMismatch``;
    * ``user_id`` unset: the profile's owner (loaded, else one SELECT), else the
      *user* owner context; with neither it stays NULL and NOT NULL refuses it.
    """
    cls = type(target)
    if cls.__dict__.get("__owned__") is not True or "user_id" not in mapper.columns:
        return
    is_chain = "profile_id" in mapper.columns and cls.__tablename__ != "master_profiles"
    if target.user_id is not None:
        if is_chain:
            owner = _loaded_profile_owner(target)
            if owner is not None and owner != target.user_id:
                raise OwnerMismatch(
                    f"{cls.__tablename__} row names owner {target.user_id} but its "
                    f"profile belongs to {owner} (ADR-092 cl. 1)"
                )
        return
    owner = None
    if is_chain:
        owner = _loaded_profile_owner(target) or _stored_profile_owner(connection, target)
    if owner is None:
        owner = _context_user_id()
        if owner is not None:
            # RULING (main, W1): a fill from the CONTEXT is counted + logged so the
            # W2 report-mode run shows which constructors still rely on it; 3e
            # decides strict mode in W3 from FILL_STATS.
            FILL_STATS[cls.__tablename__] += 1
            _log.warning(
                "ownership owner-fill from context",
                extra={"ownership_fill": {"table": cls.__tablename__, "source": "context"}},
            )
    target.user_id = owner


def _owned_models_with_user_id() -> list[type]:
    return [
        cls
        for cls in _mapped_classes()
        if cls.__dict__.get("__owned__") is True and hasattr(cls, "user_id")
    ]


def _do_orm_execute_criteria(orm_execute_state: Any) -> None:
    if not GUARD_ENABLED:
        return
    uid = _context_user_id()
    if uid is None:
        return
    if not (
        orm_execute_state.is_select
        or orm_execute_state.is_update
        or orm_execute_state.is_delete
    ):
        return
    from sqlalchemy.orm import with_loader_criteria

    options = [
        with_loader_criteria(
            Model,
            # A plain expression, never a lambda: SQLAlchemy caches lambda
            # criteria by code, and an untracked default argument baked the
            # FIRST user's id into every later statement (W2 finding).
            Model.user_id == uid,
            include_aliases=True,
            propagate_to_loaders=True,
        )
        for Model in _owned_models_with_user_id()
    ]
    orm_execute_state.statement = orm_execute_state.statement.options(*options)


def install_orm_hooks(base: Any) -> None:
    """Register the owner-fill listener on ``base`` (propagating) and the criteria hook (idempotent)."""
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    if not event.contains(base, "before_insert", _before_insert_owner):
        event.listen(base, "before_insert", _before_insert_owner, propagate=True)
    if not event.contains(Session, "do_orm_execute", _do_orm_execute_criteria):
        event.listen(Session, "do_orm_execute", _do_orm_execute_criteria)
