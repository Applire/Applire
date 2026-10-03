# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ownership on a real PostgreSQL (ADR-092 cl. 1–4; US331; SF-OWN.5, SF-OWN.9).

What SQLite cannot show: the full alembic chain (pgvector, JSONB), enforced
foreign keys, and the chain-owner check on the dialect production runs.

Runs only when ``APPLIRE_PG_TEST_URL`` names a **throwaway** PostgreSQL database
(``postgresql+asyncpg://…/<name>_test``, pgvector available) — the test DROPS its
``public`` schema. Example (own container, own port; never a dev/edge DB)::

    docker run -d --rm --name applire-pgproof -e POSTGRES_PASSWORD=proof \\
        -e POSTGRES_DB=ownership_test -p 55433:5432 pgvector/pgvector:pg16
    APPLIRE_PG_TEST_URL=postgresql+asyncpg://postgres:proof@127.0.0.1:55433/ownership_test \\
        LLM_PROVIDER=mistral PYTHONPATH=backend \\
        python3 -m pytest tests/integration/test_ownership_constraints.py --noconftest -q

``--noconftest``: the root ``tests/conftest.py`` would bring up the compose stack.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

PG_URL = os.environ.get("APPLIRE_PG_TEST_URL", "")
if PG_URL:  # the app's settings need a URL before any model import (no conftest here)
    os.environ.setdefault("DATABASE_URL", PG_URL)
BACKEND = Path(__file__).resolve().parents[2] / "backend"

pytestmark = pytest.mark.skipif(
    not PG_URL or not PG_URL.rsplit("/", 1)[-1].endswith("_test"),
    reason="APPLIRE_PG_TEST_URL (a throwaway …_test PostgreSQL DB) not set",
)


def _alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": PG_URL, "PYTHONPATH": str(BACKEND)}
    env.setdefault("LLM_PROVIDER", "mistral")
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"alembic {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stderr  # alembic logs to stderr


def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(PG_URL)


async def _reset_schema() -> None:
    eng = _engine()
    async with eng.begin() as conn:
        await conn.execute(sa.text("DROP SCHEMA public CASCADE"))
        await conn.execute(sa.text("CREATE SCHEMA public"))
    await eng.dispose()


async def _sync(fn):
    eng = _engine()
    try:
        async with eng.begin() as conn:
            return await conn.run_sync(fn)
    finally:
        await eng.dispose()


def test_migration_chain_on_postgres():
    """0070 → Nougat fixture → head; then head → 0073 → head again."""
    from tests.support.nougat_fixture import (
        M74,
        M75,
        STUB,
        FixtureDB,
        seed_duplicate_settings,
        seed_nougat,
    )

    asyncio.run(_reset_schema())
    _alembic("upgrade", "0073")

    # Nougat shape, two scenarios in one DB is impossible (one stub) — this one
    # has the stub present (settings need it: user_settings.user_id has an FK).
    def seed(conn):
        db = FixtureDB(conn)
        ids = seed_nougat(db, with_stub=True)
        seed_duplicate_settings(db, ids)
        return ids

    ids = asyncio.run(_sync(seed))
    log = _alembic("upgrade", "head")
    assert str(ids["p_old"]) in log, "RD-9: the retired profile id is in the migration log"

    def check(conn, *, first_pass: bool = True):
        db = FixtureDB(conn)
        live = {pid for pid, d in db.rows("master_profiles", "id", "deleted_at") if d is None}
        assert live == {ids["p_new"]}
        state = dict(db.rows("instance_state", "key", "value"))
        if first_pass:
            assert state[M74.KEY_RETIRED_PROFILES]["profile_ids"] == [str(ids["p_old"])]
        else:  # re-upgrade: nothing left to retire, so no notice is re-written
            assert M74.KEY_RETIRED_PROFILES not in state
        for table in M75.OWNED_WITH_USER_ID:
            assert {r[0] for r in db.rows(table, "user_id")} <= {STUB}, table
            assert db.columns(table)["user_id"]["nullable"] is False, table
        assert {r[0] for r in db.rows("gap_analysis_jobs", "id")} == {ids["gj_null"]}
        assert {r[0] for r in db.rows("user_settings", "id")} == {ids["us_new"]}
        assert dict(db.rows("job_analyses", "id", "raw_text_origin")) == {
            ids["j_scraped"]: "scraped",
            ids["j_pasted"]: "supplied",
        }
        insp = sa.inspect(conn)
        fks = {
            (t, tuple(fk["constrained_columns"]), fk["referred_table"])
            for t in ("master_profiles", "cv_import_jobs", "gap_analysis_jobs", *M75.CHAIN_TABLES, "llm_usage")
            for fk in insp.get_foreign_keys(t)
        }
        for t in ("master_profiles", "cv_import_jobs", "gap_analysis_jobs", *M75.CHAIN_TABLES, "llm_usage"):
            assert (t, ("user_id",), "users") in fks, t
        assert ("gap_analysis_jobs", ("job_analysis_id",), "job_analyses") in fks
        uniq = {i["name"]: i for i in insp.get_indexes("gap_analysis_jobs")}
        assert uniq["uq_gap_jobs_live_kickoff"]["column_names"] == ["user_id", "job_analysis_id"]
        # SF-OWN.7 over the REAL catalogue (not the models): no unique index or
        # constraint on an owned table is keyed by the posting without the owner.
        job_keyed = []
        for t in M75.OWNED_WITH_USER_ID:
            uniques = [i for i in insp.get_indexes(t) if i["unique"]]
            uniques += [
                {"name": c["name"], "column_names": c["column_names"]}
                for c in insp.get_unique_constraints(t)
            ]
            for i in uniques:
                cols = i["column_names"]
                if ({"job_analysis_id", "job_id"} & set(cols)) and "user_id" not in cols:
                    job_keyed.append((t, i["name"], cols))
        assert job_keyed == [], job_keyed

    asyncio.run(_sync(check))

    _alembic("downgrade", "0073")
    _alembic("upgrade", "head")
    asyncio.run(_sync(lambda conn: check(conn, first_pass=False)))


def test_stub_inserted_when_missing_on_postgres():
    from tests.support.nougat_fixture import STUB, FixtureDB, seed_nougat

    asyncio.run(_reset_schema())
    _alembic("upgrade", "0073")
    asyncio.run(_sync(lambda conn: seed_nougat(FixtureDB(conn), with_stub=False)))
    _alembic("upgrade", "head")
    users = asyncio.run(_sync(lambda conn: FixtureDB(conn).rows("users", "id")))
    assert users == [(STUB,)]


@pytest.mark.asyncio
async def test_orm_owner_rules_on_postgres():
    """SF-OWN.9 on the production dialect: the chain row copies its profile's
    owner; a mismatching owner raises; FKs and the live-unique hold."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile, authorized_profile_write
    from applire.models.user import User
    from applire.ownership import OwnerMismatch, unscoped

    await _reset_schema()
    _alembic("upgrade", "head")
    eng = _engine()
    Session = async_sessionmaker(eng, expire_on_commit=False)
    a, b = uuid.uuid4(), uuid.uuid4()
    with unscoped("tooling"):
        async with Session() as s:
            s.add_all([User(id=a, email="a@example.org"), User(id=b, email="b@example.org")])
            await s.flush()  # no relationship(): the unit of work does not order by FK
            job = JobAnalysis(raw_text_hash="h", raw_text="t", role_title="r", language_requirement="en")
            with authorized_profile_write():
                pa = MasterProfile(profile_json={}, user_id=a)
            s.add_all([job, pa])
            await s.commit()
            job_id, pa_id = job.id, pa.id  # rollback() below expires the instances

            cv = GeneratedCV(job_analysis_id=job_id, profile_id=pa_id, tailored_data={})
            s.add(cv)
            await s.commit()
            assert cv.user_id == a, "the chain row copies its profile's owner"

            s.add(GeneratedCV(job_analysis_id=job_id, profile_id=pa_id, tailored_data={}, user_id=b))
            with pytest.raises(OwnerMismatch):
                await s.flush()
            await s.rollback()

            with authorized_profile_write():
                s.add(MasterProfile(profile_json={}, user_id=a))
            with pytest.raises(sa.exc.IntegrityError):
                await s.flush()
            await s.rollback()

            s.add(GeneratedCV(job_analysis_id=job_id, profile_id=uuid.uuid4(), tailored_data={}, user_id=a))
            with pytest.raises(sa.exc.IntegrityError):
                await s.flush()
            await s.rollback()
    await eng.dispose()
