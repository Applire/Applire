# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ownership API skeleton (ADR-092; Strawberry F4) — the parts W0 freezes.

* ``owned_tables()`` is DERIVED from ``__owned__`` (never hand-written) and equals
  ADR-092 cl. 3's owned set;
* coverage: every table with a ``user_id`` column is owned or in the closed
  identity set — a new per-user table cannot escape the guard (revision-log R3-1d);
* the statement guard sits on the app engine's ``sync_engine`` (never the
  ``Engine`` class, R3-1c), is OFF in W0, and — switched on — refuses owned-table
  SQL with no owner context, including ``text()`` and core ``select(table)``;
* the regex has no false positive on any real non-owned table/column/index name;
* ``unscoped`` takes only the closed reason list (cl. 7 + ``tooling``);
* ``get_owned`` answers a foreign id exactly like a missing one (S-10).

The guard-on tests run without the autouse owner context (marker), since with it
the guard is invisible.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Engine, event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base, engine as app_engine
from applire.models.application import Application
from applire.models.job import JobAnalysis
from applire.models.user import User

ADR_092_OWNED = {
    "master_profiles",
    "profile_snapshots",
    "user_settings",
    "applications",
    "flow_sessions",
    "uploads",
    "cv_import_jobs",
    "gap_analysis_jobs",
    "generated_cvs",
    "generated_cover_letters",
    "gap_analyses",
    "interview_sessions",
}


# ---------------------------------------------------------------------------
# The owned set
# ---------------------------------------------------------------------------


def test_owned_tables_equals_adr_092_owned_set():
    assert ownership.owned_tables() == ADR_092_OWNED


def test_owned_tables_is_derived_from_the_marker(monkeypatch):
    """Add a model class carrying ``__owned__`` → it joins the set; drop the marker → it leaves."""

    class _NewOwned:
        __tablename__ = "brand_new_owned_things"
        __owned__ = True

    class _Inherits(_NewOwned):  # the marker is per class, never inherited
        __tablename__ = "inherits_marker"

    real = ownership._mapped_classes
    monkeypatch.setattr(ownership, "_mapped_classes", lambda: [*real(), _NewOwned, _Inherits])
    tables = ownership.owned_tables()
    assert "brand_new_owned_things" in tables
    assert "inherits_marker" not in tables

    monkeypatch.setattr(
        ownership,
        "_mapped_classes",
        lambda: [c for c in real() if c.__tablename__ != "uploads"],
    )
    assert "uploads" not in ownership.owned_tables()


def test_every_user_id_table_is_owned_or_identity():
    owned = ownership.owned_tables()
    escaped = sorted(
        t.name
        for t in Base.metadata.tables.values()
        if "user_id" in t.c and t.name not in owned and t.name not in ownership.IDENTITY_TABLES
    )
    assert escaped == [], (
        f"tables with user_id that are neither __owned__ nor identity: {escaped} — "
        "mark the model __owned__ = True or amend ADR-092's identity set"
    )


def test_owned_and_identity_sets_are_disjoint():
    assert not (ownership.owned_tables() & ownership.IDENTITY_TABLES)


def test_regex_has_no_false_positive_on_real_schema_names():
    """Measured re-check g1: no non-owned table, column, index or constraint name matches."""
    rx = ownership.owned_table_regex()
    owned = ownership.owned_tables()
    names = []
    for t in Base.metadata.tables.values():
        if t.name not in owned:
            names.append(t.name)
        names += [c.name for c in t.c]
        names += [i.name for i in t.indexes if i.name]
        names += [c.name for c in t.constraints if isinstance(c.name, str)]
    assert len(names) > 200  # the measurement covers the real schema
    assert [n for n in names if rx.search(n)] == []


def test_regex_prefers_the_longest_name():
    rx = ownership.owned_table_regex()
    assert rx.search("SELECT * FROM gap_analysis_jobs").group(1) == "gap_analysis_jobs"
    assert rx.search('select 1 from "GAP_ANALYSES"').group(1) == "GAP_ANALYSES"


# ---------------------------------------------------------------------------
# Context + closed reason list
# ---------------------------------------------------------------------------


@pytest.mark.no_owner_context
def test_contexts_nest_and_restore():
    assert ownership.current_owner() is None
    uid = uuid.uuid4()
    with ownership.owner_context(uid) as ctx:
        assert ctx.user_id == uid and not ctx.is_unscoped
        with ownership.unscoped("tooling") as inner:
            assert inner.is_unscoped and inner.reason == "tooling"
        assert ownership.current_owner().user_id == uid
    assert ownership.current_owner() is None


def test_unscoped_reasons_are_the_closed_adr_list():
    assert ownership.UNSCOPED_REASONS == {
        "admin-metadata",
        "retention",
        "ops-aggregate",
        "orphan-scan",
        "startup-backfill",
        "job-refcount",
        "migration",
        "tooling",
    }
    with pytest.raises(ValueError, match="closed list"):
        with ownership.unscoped("because-i-said-so"):  # type: ignore[arg-type]
            pass


def test_set_owner_rejects_a_string_id():
    with pytest.raises(TypeError):
        ownership.set_owner("00000000-0000-0000-0000-000000000001")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The statement guard
# ---------------------------------------------------------------------------


def test_guard_is_registered_on_the_app_engine_only():
    assert ownership.guard_installed(app_engine)
    assert not event.contains(Engine, "before_cursor_execute", ownership._before_cursor_execute)


def test_guard_is_off_in_w0():
    assert ownership.GUARD_ENABLED is False


@pytest_asyncio.fixture
async def guarded_db():
    eng = create_async_engine("sqlite+aiosqlite://")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    ownership.install_guard(eng)
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        yield s
    await eng.dispose()


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_guard_off_lets_ownerless_owned_sql_through(guarded_db: AsyncSession):
    assert ownership.current_owner() is None
    await guarded_db.execute(text("SELECT count(*) FROM applications"))


@pytest.mark.no_owner_context
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stmt",
    [
        lambda: text("SELECT count(*) FROM applications"),
        lambda: select(Application.__table__),
        lambda: select(Application).where(Application.user_id == uuid.uuid4()),
    ],
    ids=["text", "core-table", "orm"],
)
async def test_guard_on_refuses_ownerless_owned_sql(guarded_db, monkeypatch, stmt):
    monkeypatch.setattr(ownership, "GUARD_ENABLED", True)
    with pytest.raises(ownership.OwnerContextMissing, match="applications"):
        await guarded_db.execute(stmt())


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_guard_on_passes_named_owner_unscoped_and_non_owned_tables(guarded_db, monkeypatch):
    monkeypatch.setattr(ownership, "GUARD_ENABLED", True)
    with ownership.owner_context(uuid.uuid4()):
        await guarded_db.execute(text("SELECT count(*) FROM applications"))
    with ownership.unscoped("tooling"):
        await guarded_db.execute(text("SELECT count(*) FROM applications"))
    # job_analyses / users are not owned: no context needed.
    await guarded_db.execute(select(JobAnalysis.id))
    await guarded_db.execute(select(User.id))


# ---------------------------------------------------------------------------
# get_owned / OwnedNotFound
# ---------------------------------------------------------------------------


async def _seed(db, owner):
    db.add(User(id=owner, email=f"{owner.hex[:8]}@example.org"))
    job = JobAnalysis(raw_text_hash=uuid.uuid4().hex, raw_text="x", role_title="R", seniority_level="mid", language_requirement="English")
    db.add(job)
    await db.flush()
    app_row = Application(user_id=owner, job_analysis_id=job.id, company_name="C", role_title="R")
    db.add(app_row)
    await db.commit()
    return app_row


@pytest.mark.asyncio
async def test_get_owned_returns_own_row_and_hides_foreign_and_missing(guarded_db):
    a, b = uuid.uuid4(), uuid.uuid4()
    row = await _seed(guarded_db, a)
    assert (await ownership.get_owned(guarded_db, Application, row.id, a)).id == row.id

    with pytest.raises(ownership.OwnedNotFound) as foreign:
        await ownership.get_owned(guarded_db, Application, row.id, b, kind="application")
    with pytest.raises(ownership.OwnedNotFound) as missing:
        await ownership.get_owned(guarded_db, Application, uuid.uuid4(), b, kind="application")
    assert (foreign.value.status_code, foreign.value.detail) == (missing.value.status_code, missing.value.detail)
    assert foreign.value.detail == "application not found"


@pytest.mark.asyncio
async def test_get_owned_refuses_a_non_owned_model(guarded_db):
    with pytest.raises(TypeError, match="not an owned model"):
        await ownership.get_owned(guarded_db, JobAnalysis, uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_owned_not_found_renders_the_plain_404_body():
    app = FastAPI()

    @app.get("/x")
    async def x():
        raise ownership.OwnedNotFound("cv")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/x")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "cv not found"}


def test_models_package_imports_every_model_module():
    """``owned_tables()`` imports ``applire.models`` to see every mapper; a module the
    package forgets is a table the guard never learns about (W0 found
    ``cover_letter`` missing)."""
    import pkgutil

    import applire.models as pkg

    src = open(pkg.__file__).read()
    modules = {m.name for m in pkgutil.iter_modules(pkg.__path__)}
    missing = sorted(m for m in modules if f"    {m},\n" not in src)
    assert missing == []
