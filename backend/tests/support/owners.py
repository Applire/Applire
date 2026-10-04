# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Owners for tests: the harness owner context and a second user (ADR-092 cl. 8; F12).

* ``harness_owner_context`` — the **synchronous** autouse fixture both unit
  conftests import. It sets the ADR-092 owner context to the harness (stub) user
  before any other function fixture runs — in particular before ``async_db``'s
  ``Base.metadata.create_all``, whose SQLite ``PRAGMA table_info("<owned>")``
  statements name every owned table. It must be sync: a value set in an async
  fixture lives in that fixture's task and never reaches the test.
  Opt out with ``@pytest.mark.no_owner_context`` (the guard's own mutation tests
  and the cross-user isolation suite — with the context on, the guard is invisible).
* ``make_user`` / ``make_two_users`` — users A and B for isolation tests;
  ``two_users`` is the fixture form on ``async_db``.

Other packages add their resource factories in their own
``tests/support/owners_<pkg>.py`` (3a owns ``RESOURCE_FACTORIES``).
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from applire.auth.no_auth import _STUB_USER_ID
from applire.models.user import User
from applire import ownership
from applire.ownership import reset_owner, set_owner

#: The NoAuth/harness stub user every unit test acts as by default.
#: (1a collapses the stub constants into ``auth/harness.py``; this name stays.)
HARNESS_USER_ID: uuid.UUID = _STUB_USER_ID

NO_OWNER_CONTEXT_MARKER = "no_owner_context"


#: ``APPLIRE_TEST_OWNER_GUARD`` — how the unit suites run the statement guard:
#: ``on`` (default, MD-24 (1): the guard raises), ``off`` (the isolation suite's
#: second arm: a 404 must come from an explicit owner predicate), ``report``
#: (diagnostic: log + ``guard_reports()``, nothing raises).
GUARD_MODE_ENV = "APPLIRE_TEST_OWNER_GUARD"
GUARD_MODES = ("on", "off", "report")


def guard_mode_from_env() -> str:
    mode = os.environ.get(GUARD_MODE_ENV, "on").strip().lower() or "on"
    if mode not in GUARD_MODES:
        raise pytest.UsageError(f"{GUARD_MODE_ENV}={mode!r} — expected one of {GUARD_MODES}")
    return mode


def _test_engine_guard(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
    """The statement guard for every engine a test builds (test harness only).

    Production registers ``ownership._before_cursor_execute`` on the application
    engine alone (ADR-092 cl. 8a); tests build their own SQLite engines, so the
    suites hang this delegate on the ``Engine`` class. An engine that already
    carries the production listener is skipped (one check per statement).
    """
    from sqlalchemy import event

    if event.contains(conn.engine, "before_cursor_execute", ownership._before_cursor_execute):
        return
    ownership._before_cursor_execute(conn, cursor, statement, parameters, context, executemany)


def configure_test_guard() -> str:
    """Set the guard flags for this test session and guard every engine (idempotent)."""
    from sqlalchemy import Engine, event

    mode = guard_mode_from_env()
    ownership.GUARD_ENABLED = mode != "off"
    ownership.GUARD_REPORT_ONLY = mode == "report"
    if not event.contains(Engine, "before_cursor_execute", _test_engine_guard):
        event.listen(Engine, "before_cursor_execute", _test_engine_guard)
    return mode


def register_markers(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{NO_OWNER_CONTEXT_MARKER}: run without the autouse harness owner context "
        "(guard mutation tests, cross-user isolation suite — ADR-092 cl. 8)",
    )


@pytest.fixture(autouse=True)
def harness_owner_context(request: pytest.FixtureRequest):
    """Act as the harness user for the whole test, unless opted out."""
    if request.node.get_closest_marker(NO_OWNER_CONTEXT_MARKER) is not None:
        # Clear explicitly: an owner leaked into the session's base context by an
        # earlier test (a dependency's set_owner run in-task) must not make the
        # guard invisible here (W3 finding — order-dependent green).
        token = ownership._owner.set(None)
        try:
            yield None
        finally:
            ownership._owner.reset(token)
        return
    token = set_owner(HARNESS_USER_ID)
    try:
        yield HARNESS_USER_ID
    finally:
        reset_owner(token)


def act_as(user_id: uuid.UUID, *, autouse: bool = True):
    """A **sync** fixture acting for ``user_id`` for the whole test (ADR-092 cl. 8).

    For test modules whose services act for a user other than the harness user:
    a request acts for exactly one user, so the test does too — the loader
    criteria (cl. 8b) then match what production sees. Bind it at module level::

        _acting_user = act_as(UID)

    A read for a second user runs inside its own ``owner_context(other)``.
    """

    @pytest.fixture(autouse=autouse)
    def _act_as():
        token = set_owner(user_id)
        try:
            yield user_id
        finally:
            reset_owner(token)

    return _act_as


async def make_user(db, *, email: str | None = None, user_id: uuid.UUID | None = None) -> User:
    """Insert and return a user row (flushes, does not commit)."""
    uid = user_id or uuid.uuid4()
    user = User(id=uid, email=email or f"user-{uid.hex[:8]}@example.org")
    db.add(user)
    await db.flush()
    return user


async def make_two_users(db) -> tuple[User, User]:
    """Users A and B (neither is the harness user); committed."""
    a = await make_user(db, email="owner-a@example.org")
    b = await make_user(db, email="owner-b@example.org")
    await db.commit()
    return a, b


@pytest_asyncio.fixture
async def two_users(async_db) -> tuple[User, User]:
    return await make_two_users(async_db)
