# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Migrations 0074 + 0075 on a Nougat-era fixture DB (ADR-092 cl. 2–4, RD-9, D-10;
US331; System-FMEA SF-OWN.5).

The full alembic chain cannot run on SQLite (JSONB / pgvector predate it), so the
two revisions are EXECUTED through Alembic's own ``Operations.context`` against an
in-memory SQLite DB: ``create_all`` builds today's shape, ``0075.downgrade`` +
``0074.downgrade`` take it back to the Nougat (v0.42) shape, a fixture with every
upgrade hazard is inserted, and ``0074.upgrade`` + ``0075.upgrade`` run. The same
chain on a real PostgreSQL (``alembic upgrade head`` from 0070 with this fixture)
is ``tests/integration/test_ownership_constraints.py::test_migration_chain_on_postgres``.

Hazards in the fixture: the stub user row missing; ownerless uploads / import
jobs / gap jobs; an import job naming a user that does not exist; two live master
profiles (+ a created_at tie broken by id) and a soft-deleted one; chain rows on
the retired profile; duplicate settings rows; a gap job whose posting is gone; a
scraped and a pasted posting; an unattributed usage row.
"""

from __future__ import annotations

import uuid
from datetime import timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

from applire.db.session import Base
from tests.support.nougat_fixture import (
    GHOST,  # noqa: F401
    M74,
    M75,
    STUB,
    T0,
    FixtureDB,
    run_migration as _run,
    seed_duplicate_settings as _seed_settings,
    seed_nougat as _seed,
)


@pytest.fixture
def nougat_db():
    """Today's schema, downgraded through 0075 and 0074 = the v0.42 shape."""
    engine = sa.create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        _run(conn, M75.downgrade)
        _run(conn, M74.downgrade)
    yield engine
    engine.dispose()


def _upgrade(engine) -> None:  # noqa: ANN001
    with engine.begin() as conn:
        _run(conn, M74.upgrade)
        _run(conn, M75.upgrade)


def test_revision_chain_is_0073_0074_0075():
    assert (M74.revision, M74.down_revision) == ("0074", "0073")
    assert (M75.revision, M75.down_revision) == ("0075", "0074")


def test_downgrade_reaches_the_nougat_shape(nougat_db):
    with nougat_db.connect() as conn:
        db = FixtureDB(conn)
        assert "user_id" not in db.columns("master_profiles")
        assert db.columns("uploads")["user_id"]["nullable"] is True
        assert db.columns("cv_import_jobs")["user_id"]["nullable"] is True
        assert "raw_text_origin" not in db.columns("job_analyses")
        for t in M75.CHAIN_TABLES + ("llm_usage",):
            assert "user_id" not in db.columns(t), t


def test_upgrade_owns_every_row_and_retires_duplicate_profiles(nougat_db):
    with nougat_db.begin() as conn:
        db = FixtureDB(conn)
        ids = _seed(db)  # NO stub user row: 0074 must insert it first
        # user_settings.user_id has an FK to users — the stub must exist for it;
        # SQLite does not enforce FKs here, so seed it the way an old DB holds it.
        _seed_settings(db, ids)
    _upgrade(nougat_db)

    with nougat_db.connect() as conn:
        db = FixtureDB(conn)
        assert (STUB,) in db.rows("users", "id")

        live = {pid for pid, deleted in db.rows("master_profiles", "id", "deleted_at") if deleted is None}
        assert live == {ids["p_new"]}, "RD-9: the newest by created_at stays live (not by updated_at)"
        deleted = dict(db.rows("master_profiles", "id", "deleted_at"))
        assert deleted[ids["p_old"]] is not None
        assert deleted[ids["p_del"]].replace(tzinfo=None) == (T0 + timedelta(days=8)).replace(
            tzinfo=None
        ), "an already-deleted profile keeps its own deleted_at"
        state = dict(db.rows("instance_state", "key", "value"))
        assert state[M74.KEY_RETIRED_PROFILES]["profile_ids"] == [str(ids["p_old"])]

        for table in M75.OWNED_WITH_USER_ID:
            owners = {r[0] for r in db.rows(table, "user_id")}
            assert owners <= {STUB}, (table, owners)
        # the ghost-owned import job was re-owned, not kept dangling
        assert dict(db.rows("cv_import_jobs", "id", "user_id"))[ids["imp_ghost"]] == STUB
        # chain rows on the retired profile keep that profile's owner
        assert dict(db.rows("generated_cvs", "id", "user_id"))[ids["cv_old"]] == STUB

        gap_jobs = {r[0] for r in db.rows("gap_analysis_jobs", "id")}
        assert gap_jobs == {ids["gj_null"]}, "the job whose posting is gone is deleted"

        origin = dict(db.rows("job_analyses", "id", "raw_text_origin"))
        assert origin == {ids["j_scraped"]: "scraped", ids["j_pasted"]: "supplied"}

        assert {r[0] for r in db.rows("user_settings", "id")} == {ids["us_new"]}
        assert db.rows("llm_usage", "user_id") == [(None,)], "history is not attributed"

        for table in M75.OWNED_WITH_USER_ID:
            assert db.columns(table)["user_id"]["nullable"] is False, table


def test_created_at_tie_keeps_the_larger_id(nougat_db):
    with nougat_db.begin() as conn:
        db = FixtureDB(conn)
        lo, hi = sorted([uuid.uuid4(), uuid.uuid4()])
        db.insert("master_profiles", id=lo, created_at=T0)
        db.insert("master_profiles", id=hi, created_at=T0)
    _upgrade(nougat_db)
    with nougat_db.connect() as conn:
        live = [pid for pid, d in FixtureDB(conn).rows("master_profiles", "id", "deleted_at") if d is None]
    # SQLite orders the Uuid column by its stored 32-hex text = the UUID's byte order,
    # the same order PostgreSQL's uuid comparison gives.
    assert live == [hi]


def test_upgrade_keeps_an_existing_stub_and_runs_on_an_empty_db(nougat_db):
    with nougat_db.begin() as conn:
        FixtureDB(conn).insert("users", id=STUB, email="local@applire.community", created_at=T0)
    _upgrade(nougat_db)
    with nougat_db.connect() as conn:
        db = FixtureDB(conn)
        assert db.rows("users", "id") == [(STUB,)]
        assert M74.KEY_RETIRED_PROFILES not in dict(db.rows("instance_state", "key", "value"))


def test_constraints_after_upgrade(nougat_db):
    _upgrade(nougat_db)
    with nougat_db.connect() as conn:
        db = FixtureDB(conn)
        a, b = uuid.uuid4(), uuid.uuid4()
        job = uuid.uuid4()
        db.insert("users", id=a, email="a@example.org", created_at=T0)
        db.insert("job_analyses", id=job, raw_text_hash="h", source_url=None)
        db.insert("master_profiles", id=uuid.uuid4(), user_id=a)
        # a second live profile for the same owner is refused …
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                db.insert("master_profiles", id=uuid.uuid4(), user_id=a)
        # … a soft-deleted one and another owner's are fine
        db.insert("master_profiles", id=uuid.uuid4(), user_id=a, deleted_at=T0)
        db.insert("master_profiles", id=uuid.uuid4(), user_id=b)
        # settings: one row per user
        db.insert("user_settings", id=uuid.uuid4(), user_id=a)
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                db.insert("user_settings", id=uuid.uuid4(), user_id=a)
        # NOT NULL owners
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                db.insert("uploads", id=uuid.uuid4(), user_id=None)
        # re-keyed live kickoff: two users on one posting may both kick off …
        db.insert("gap_analysis_jobs", id=uuid.uuid4(), job_analysis_id=job, user_id=a, status="pending")
        db.insert("gap_analysis_jobs", id=uuid.uuid4(), job_analysis_id=job, user_id=b, status="pending")
        # … one user twice may not (SF-OWN.7)
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                db.insert("gap_analysis_jobs", id=uuid.uuid4(), job_analysis_id=job, user_id=a, status="pending")
        # posting origin is a closed set
        with pytest.raises(sa.exc.IntegrityError):
            with conn.begin_nested():
                db.insert("job_analyses", id=uuid.uuid4(), raw_text_hash="h3", raw_text_origin="guessed")
        conn.rollback()


def test_the_end_assertion_fails_the_upgrade_on_an_ownerless_row(nougat_db):
    """The cl. 4 assertion is a control of its own: shown firing on a missed backfill."""
    with nougat_db.begin() as conn:
        db = FixtureDB(conn)
        db.insert("users", id=STUB, email="local@applire.community", created_at=T0)
        db.insert("job_analyses", id=(j := uuid.uuid4()), raw_text_hash="h", source_url=None)
        db.insert("master_profiles", id=(p := uuid.uuid4()))
        db.insert("generated_cvs", id=uuid.uuid4(), job_analysis_id=j, profile_id=p)
    with nougat_db.begin() as conn:
        _run(conn, M74.upgrade)
        # Simulate a missed backfill: the chain columns exist but stayed NULL.
        for table in M75.CHAIN_TABLES:
            conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN user_id CHAR(32)"))
        with pytest.raises(RuntimeError, match=r"ownerless rows survived.*generated_cvs"):
            M75._assert_no_ownerless_rows(conn)
        # and the same state with the owner copied passes
        conn.execute(sa.text("UPDATE generated_cvs SET user_id = 'x'"))
        M75._assert_no_ownerless_rows(conn)


def test_upgrade_then_downgrade_then_upgrade_again(nougat_db):
    with nougat_db.begin() as conn:
        db = FixtureDB(conn)
        ids = _seed(db, with_stub=True)
        _seed_settings(db, ids)
    _upgrade(nougat_db)
    with nougat_db.begin() as conn:
        _run(conn, M75.downgrade)
        _run(conn, M74.downgrade)
    _upgrade(nougat_db)
    with nougat_db.connect() as conn:
        db = FixtureDB(conn)
        live = {pid for pid, d in db.rows("master_profiles", "id", "deleted_at") if d is None}
        assert live == {ids["p_new"]}
