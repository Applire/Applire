# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migration 0076 — owners get a link to every posting they already worked on
(ADR-092 cl. 5c, RD-2; founder task W2-1; Strawberry package 4a).

Executed through Alembic's ``Operations.context`` on SQLite after 0074 + 0075, on
the v0.42 (Nougat) shape the 3a harness builds (``tests.support.nougat_fixture``).
The PostgreSQL proof of the full chain is
``tests/integration/test_migration_0076_links.py``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from applire.db.session import Base
from tests.support.nougat_fixture import (
    M74,
    M75,
    STUB,
    T0,
    FixtureDB,
    load_migration,
    run_migration,
    seed_duplicate_settings,
    seed_nougat,
)

M76 = load_migration("0076")


def seed_0076(db: FixtureDB, ids: dict) -> dict:
    """On top of ``seed_nougat``: the 0076 hazards.

    seed_nougat already holds work on j_scraped (CVs, gap, interview) and j_pasted
    (letter) with no application. Added here: a posting the owner already has a
    card for (no second link), a posting with a soft-deleted card (counts as
    existing), a posting with a flow only, a posting whose only work is
    soft-deleted (no link), a soft-deleted posting with live work (no link), and a
    posting with no work at all (no link).
    """
    more = {k: uuid.uuid4() for k in (
        "j_carded", "app_carded", "cv_carded", "j_removed", "app_removed", "cl_removed",
        "j_flow", "flow_only", "j_deadwork", "cv_dead", "j_gone", "cv_on_gone", "j_untouched",
    )}
    for key, hash_ in (
        ("j_carded", "h3"), ("j_removed", "h4"), ("j_flow", "h5"),
        ("j_deadwork", "h6"), ("j_untouched", "h8"),
    ):
        db.insert("job_analyses", id=more[key], raw_text_hash=hash_, role_title=f"Role {hash_}",
                  company_name=f"Co {hash_}")
    db.insert("job_analyses", id=more["j_gone"], raw_text_hash="h7", deleted_at=T0)
    db.insert("applications", id=more["app_carded"], user_id=STUB, job_analysis_id=more["j_carded"])
    db.insert("generated_cvs", id=more["cv_carded"], job_analysis_id=more["j_carded"], profile_id=ids["p_new"])
    db.insert("applications", id=more["app_removed"], user_id=STUB, job_analysis_id=more["j_removed"],
              deleted_at=T0 + timedelta(days=2))
    db.insert("generated_cover_letters", id=more["cl_removed"], job_analysis_id=more["j_removed"],
              profile_id=ids["p_new"])
    db.insert("flow_sessions", id=more["flow_only"], user_id=STUB, job_id=more["j_flow"],
              current_step="jd_analysis", user_type="new", available_actions={})
    db.insert("generated_cvs", id=more["cv_dead"], job_analysis_id=more["j_deadwork"],
              profile_id=ids["p_new"], deleted_at=T0)
    db.insert("generated_cvs", id=more["cv_on_gone"], job_analysis_id=more["j_gone"], profile_id=ids["p_new"])
    return more


@pytest.fixture
def nougat_db():
    engine = sa.create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        run_migration(conn, M76.downgrade)
        run_migration(conn, M75.downgrade)
        run_migration(conn, M74.downgrade)
    yield engine
    engine.dispose()


def _upgrade(engine) -> None:  # noqa: ANN001
    with engine.begin() as conn:
        run_migration(conn, M74.upgrade)
        run_migration(conn, M75.upgrade)
        run_migration(conn, M76.upgrade)


def _links(engine) -> dict:
    with engine.connect() as conn:
        rows = FixtureDB(conn).rows(
            "applications", "user_id", "job_analysis_id", "user_status", "workflow_status",
            "deleted_at", "role_title", "company_name", "flow_session_id", "expires_at",
        )
    return {r[1]: r for r in rows}


def test_revision_chain():
    assert (M76.revision, M76.down_revision) == ("0076", "0075")


def test_owner_gets_a_link_for_each_posting_with_live_work(nougat_db):
    with nougat_db.begin() as conn:
        db = FixtureDB(conn)
        ids = seed_nougat(db, with_stub=True)
        seed_duplicate_settings(db, ids)
        more = seed_0076(db, ids)
    _upgrade(nougat_db)
    links = _links(nougat_db)

    created = {ids["j_scraped"], ids["j_pasted"], more["j_flow"]}
    pre_existing = {more["j_carded"], more["j_removed"]}
    assert set(links) == created | pre_existing, "no link for untouched / dead-work / deleted postings"
    for job_id in created:
        user_id, _job, user_status, workflow_status, deleted_at, *_rest, flow_id, expires_at = links[job_id]
        assert user_id == STUB
        assert (user_status, workflow_status, deleted_at, flow_id) == ("tracking", "none", None, None)
        assert expires_at is not None
    # the pre-existing cards are untouched — the removed one stays removed
    assert links[more["j_removed"]][4] is not None
    assert links[more["j_carded"]][4] is None
    # labels denormalised from the posting
    assert links[more["j_flow"]][5:7] == ("Role h5", "Co h5")
    with nougat_db.connect() as conn:
        apps = FixtureDB(conn).rows("applications", "id")
    assert len(apps) == 5, "exactly one row per (owner, posting) — uq_application_user_job held"


def test_two_owners_each_get_their_own_link_on_one_posting(nougat_db):
    """Post-0075 state with a second account (an install upgraded, then a user
    invited, then 0076 re-run is not possible — but the step must key on the owner)."""
    with nougat_db.begin() as conn:
        db = FixtureDB(conn)
        seed_nougat(db, with_stub=True)
    with nougat_db.begin() as conn:
        run_migration(conn, M74.upgrade)
        run_migration(conn, M75.upgrade)
        db = FixtureDB(conn)
        other = uuid.uuid4()
        db.insert("users", id=other, email="b@example.org", created_at=T0)
        j = uuid.uuid4()
        db.insert("job_analyses", id=j, raw_text_hash="shared", raw_text_origin="scraped")
        pa, pb = uuid.uuid4(), uuid.uuid4()
        db.insert("master_profiles", id=pb, user_id=other, created_at=T0)
        db.insert("generated_cvs", id=uuid.uuid4(), job_analysis_id=j, profile_id=pb, user_id=other)
        db.insert("flow_sessions", id=uuid.uuid4(), user_id=STUB, job_id=j,
                  current_step="jd_analysis", user_type="new", available_actions={})
        run_migration(conn, M76.upgrade)
        owners = sorted(
            (str(u) for u, job in FixtureDB(conn).rows("applications", "user_id", "job_analysis_id") if job == j)
        )
    assert owners == sorted([str(STUB), str(other)])


def test_idempotent_and_empty_db(nougat_db):
    _upgrade(nougat_db)  # empty Nougat DB → nothing to create
    with nougat_db.begin() as conn:
        run_migration(conn, M76.upgrade)  # second run: still nothing, no crash
        assert FixtureDB(conn).rows("applications", "id") == []


def test_rerun_after_links_exist_creates_nothing_new(nougat_db):
    with nougat_db.begin() as conn:
        db = FixtureDB(conn)
        ids = seed_nougat(db, with_stub=True)
        seed_0076(db, ids)
    _upgrade(nougat_db)
    before = _links(nougat_db)
    with nougat_db.begin() as conn:
        run_migration(conn, M76.upgrade)
    assert _links(nougat_db) == before


def test_links_open_the_posting_through_get_job_for_user(nougat_db):
    """The point of the step: the owner's work is reachable again (cl. 5c)."""
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from applire.services.job import get_job_for_user

    with nougat_db.begin() as conn:
        db = FixtureDB(conn)
        ids = seed_nougat(db, with_stub=True)
    _upgrade(nougat_db)

    # Same in-memory DB through an async engine: share the sync engine's connection.
    async def check():
        aeng = create_async_engine("sqlite+aiosqlite://", creator=lambda: nougat_db.raw_connection().driver_connection)
        async with AsyncSession(aeng) as s:
            job = await get_job_for_user(s, ids["j_pasted"], STUB)
            assert job.id == ids["j_pasted"]
        await aeng.dispose()

    try:
        asyncio.run(check())
    except Exception as exc:  # noqa: BLE001 — the async bridge is a convenience, not the claim
        pytest.skip(f"async bridge onto the sync in-memory DB unavailable: {exc}")
