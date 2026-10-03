# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The unit-test owner context bootstrap (ADR-092 cl. 8; Strawberry F12).

The autouse fixture is SYNC so its value is in the context every async test and
async fixture task is copied from; ``async_db`` depends on it so the owner is set
before ``create_all`` (whose SQLite PRAGMA statements name the owned tables).
"""

import pytest
from sqlalchemy import event

from applire import ownership
from applire.models.profile import MasterProfile
from tests.support.owners import HARNESS_USER_ID, make_two_users
from tests.support.profile_factory import make_master_profile


def test_sync_test_runs_as_the_harness_user():
    assert ownership.current_owner().user_id == HARNESS_USER_ID


@pytest.mark.asyncio
async def test_async_test_runs_as_the_harness_user():
    assert ownership.current_owner().user_id == HARNESS_USER_ID


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_marker_opts_out():
    assert ownership.current_owner() is None


@pytest.fixture
def owned_sql_spy():
    """Class-level spy (every engine) installed BEFORE ``async_db`` builds its schema."""
    from sqlalchemy import Engine

    seen = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        if ownership.owned_table_regex().search(statement):
            seen.append(ownership.current_owner())

    event.listen(Engine, "before_cursor_execute", spy)
    yield seen
    event.remove(Engine, "before_cursor_execute", spy)


@pytest.mark.asyncio
async def test_async_db_create_all_runs_with_the_owner_set(owned_sql_spy, async_db):
    assert len(owned_sql_spy) >= 12, "create_all issued no statements naming owned tables"
    assert all(c is not None and c.user_id == HARNESS_USER_ID for c in owned_sql_spy)


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_without_the_fixture_create_all_would_be_ownerless(owned_sql_spy, async_db):
    """The opposite arm: the same spy sees no owner once the fixture is opted out —
    so the guard, when on, would refuse the schema build (why the fixture exists)."""
    assert owned_sql_spy and all(c is None for c in owned_sql_spy)


@pytest.mark.asyncio
async def test_two_users_are_distinct_and_not_the_harness_user(async_db):
    a, b = await make_two_users(async_db)
    assert len({a.id, b.id, HARNESS_USER_ID}) == 3


def test_profile_factory_defaults_the_owner_once_the_column_exists(monkeypatch):
    if hasattr(MasterProfile, "user_id"):
        assert make_master_profile(profile_json={}).user_id == HARNESS_USER_ID
    else:  # W0: the column lands with migration 0074 (3a); nothing is passed yet
        assert "user_id" not in make_master_profile(profile_json={}).__dict__
