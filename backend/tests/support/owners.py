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

import uuid

import pytest
import pytest_asyncio

from applire.auth.no_auth import _STUB_USER_ID
from applire.models.user import User
from applire.ownership import reset_owner, set_owner

#: The NoAuth/harness stub user every unit test acts as by default.
#: (1a collapses the stub constants into ``auth/harness.py``; this name stays.)
HARNESS_USER_ID: uuid.UUID = _STUB_USER_ID

NO_OWNER_CONTEXT_MARKER = "no_owner_context"


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
        yield None
        return
    token = set_owner(HARNESS_USER_ID)
    try:
        yield HARNESS_USER_ID
    finally:
        reset_owner(token)


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
