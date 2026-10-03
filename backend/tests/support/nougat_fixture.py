# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Nougat-era (v0.42, alembic 0073) fixture DB for the ownership migrations
0074 + 0075 (ADR-092, US331) — shared by the SQLite unit test
(``tests/unit/test_ownership_migrations.py``) and the PostgreSQL proof
(``tests/integration/test_ownership_constraints.py``).

``FixtureDB`` inserts through a *sync* connection with exactly the columns the
database has at its current revision, filling every NOT NULL column without a
default by type, so the fixture names only what matters to the migration.
"""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlalchemy as sa

import applire.models  # noqa: F401 — every mapper
from applire.db.session import Base

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
STUB = uuid.UUID("00000000-0000-0000-0000-000000000001")
GHOST = uuid.UUID("99999999-9999-9999-9999-999999999999")
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load_migration(name: str):
    path = next(VERSIONS.glob(f"{name}_*.py"))
    spec = importlib.util.spec_from_file_location(f"_mig_{name}_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M74, M75 = load_migration("0074"), load_migration("0075")


def run_migration(conn, fn) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        fn()


def _filler(col_type) -> object:  # noqa: ANN001
    py = None
    try:
        py = col_type.python_type
    except NotImplementedError:
        pass
    if isinstance(col_type, sa.JSON) or py in (dict, list):
        return {}
    if py is uuid.UUID:
        return uuid.uuid4()
    if py is bool:
        return False
    if py in (int, float):
        return 0
    if py is datetime:
        return T0
    return "x"


class FixtureDB:
    """Inserts rows with exactly the columns the CURRENT (downgraded) DB has."""

    def __init__(self, conn) -> None:  # noqa: ANN001
        self.conn = conn

    def insert(self, table: str, **values) -> None:
        meta_table = Base.metadata.tables[table]
        cols = sa.inspect(self.conn).get_columns(table)
        row = {}
        for c in cols:
            name = c["name"]
            if name in values:
                row[name] = values[name]
            elif not c["nullable"] and c.get("default") is None:
                row[name] = _filler(meta_table.c[name].type)
        unknown = set(values) - {c["name"] for c in cols}
        assert not unknown, f"fixture names columns {table} lacks at this revision: {unknown}"
        t = sa.table(table, *[sa.column(k, meta_table.c[k].type) for k in row])
        self.conn.execute(t.insert().values(**row))

    def rows(self, table: str, *cols: str) -> list[tuple]:
        meta_table = Base.metadata.tables[table]
        t = sa.table(table, *[sa.column(c, meta_table.c[c].type) for c in cols])
        return [tuple(r) for r in self.conn.execute(sa.select(*t.c))]

    def columns(self, table: str) -> dict[str, dict]:
        return {c["name"]: c for c in sa.inspect(self.conn).get_columns(table)}


def seed_nougat(db: FixtureDB, *, with_stub: bool = False) -> dict:
    ids = {k: uuid.uuid4() for k in (
        "p_old", "p_new", "p_del", "j_scraped", "j_pasted", "cv_old", "cv_new",
        "cl_new", "gap_old", "sess_new", "up_a", "up_b", "imp_null",
        "imp_ghost", "gj_null", "gj_orphan", "us_old", "us_new", "usage",
    )}
    if with_stub:
        db.insert("users", id=STUB, email="local@applire.community", created_at=T0)
    db.insert("job_analyses", id=ids["j_scraped"], raw_text_hash="h1", source_url="https://jobs.example.org/1")
    db.insert("job_analyses", id=ids["j_pasted"], raw_text_hash="h2", source_url=None)
    db.insert("master_profiles", id=ids["p_old"], created_at=T0, updated_at=T0 + timedelta(days=9))
    db.insert("master_profiles", id=ids["p_new"], created_at=T0 + timedelta(days=5), updated_at=T0 + timedelta(days=5))
    db.insert("master_profiles", id=ids["p_del"], created_at=T0 + timedelta(days=7), deleted_at=T0 + timedelta(days=8))
    db.insert("generated_cvs", id=ids["cv_old"], job_analysis_id=ids["j_scraped"], profile_id=ids["p_old"])
    db.insert("generated_cvs", id=ids["cv_new"], job_analysis_id=ids["j_scraped"], profile_id=ids["p_new"])
    db.insert("generated_cover_letters", id=ids["cl_new"], job_analysis_id=ids["j_pasted"], profile_id=ids["p_new"])
    db.insert("gap_analyses", id=ids["gap_old"], job_analysis_id=ids["j_scraped"], profile_id=ids["p_old"], input_fingerprint="fp")
    db.insert("interview_sessions", id=ids["sess_new"], job_analysis_id=ids["j_scraped"], profile_id=ids["p_new"], status="active")
    db.insert("uploads", id=ids["up_a"], user_id=None)
    db.insert("uploads", id=ids["up_b"], user_id=None)
    db.insert("cv_import_jobs", id=ids["imp_null"], user_id=None)
    db.insert("cv_import_jobs", id=ids["imp_ghost"], user_id=GHOST)
    db.insert("gap_analysis_jobs", id=ids["gj_null"], job_analysis_id=ids["j_scraped"], user_id=None, status="pending")
    db.insert("gap_analysis_jobs", id=ids["gj_orphan"], job_analysis_id=uuid.uuid4(), user_id=None, status="ready")
    db.insert("llm_usage", id=ids["usage"], provider="mock")
    return ids


def seed_duplicate_settings(db: FixtureDB, ids: dict) -> None:
    db.insert("user_settings", id=ids["us_old"], user_id=STUB, created_at=T0)
    db.insert("user_settings", id=ids["us_new"], user_id=STUB, created_at=T0 + timedelta(days=1))


