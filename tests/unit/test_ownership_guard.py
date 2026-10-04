# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The owner guard, mutation-proven (ADR-092 cl. 1, cl. 8a/8b; MD-8; US332;
System-FMEA SF-OWN.1, SF-OWN.2, SF-OWN.9).

Each statement shape the refuted ORM-event design missed (adversarial-security §1,
experiments t2/t6/t7) is run twice on a guarded engine with NO owner context:

* guard **off** — the statement runs and (for reads) returns user B's rows: the
  baseline the guard must rescue (a guard test on a shape that cannot leak proves
  nothing — memory ``feedback_mutation_test_the_guard``);
* guard **on** — ``OwnerContextMissing``.

The module flag is ON in the product since W3 (MD-24); every test here names the
arm it runs explicitly (``guard_on`` / ``guard_off``), so the file proves the
same thing whatever the session default is. All tests run without the autouse harness owner context
(``no_owner_context``), because with it the guard is invisible.

Residuals pinned (documented, not fixed — ADR-092 cl. 8b Negative): the loader
criteria do not filter ``exists()``, ``count``, ``text()`` or an identity-map
``Session.get`` hit; the chain-owner check compares only against a LOADED profile.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, exists, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base
from applire.models.application import Application
from applire.models.cv import GeneratedCV
from applire.models.flow import FlowSession
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile, authorized_profile_write
from applire.models.uploads import UploadRecord
from applire.models.user import User

pytestmark = pytest.mark.no_owner_context

A = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a")
B = uuid.UUID("bbbbbbbb-0000-0000-0000-00000000000b")


@pytest_asyncio.fixture
async def world():
    """A guarded in-memory DB with users A and B, one application + flow each."""
    eng = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    ownership.install_guard(eng)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    ids = {}
    with ownership.unscoped("tooling"):
        async with factory() as s:
            job = JobAnalysis(
                raw_text_hash="h", raw_text="t", role_title="R",
                seniority_level="mid", language_requirement="English",
            )
            s.add_all([User(id=A, email="a@example.org"), User(id=B, email="b@example.org"), job])
            await s.flush()
            for owner in (A, B):
                flow = FlowSession(user_id=owner, job_id=job.id)
                s.add(flow)
                await s.flush()
                app = Application(
                    user_id=owner, job_analysis_id=job.id, flow_session_id=flow.id,
                    company_name=f"Co-{owner.hex[0]}", role_title="Eng",
                )
                s.add(app)
                await s.flush()
                ids[owner] = app.id
            ids["job"] = job.id
            await s.commit()
    yield factory, ids
    await eng.dispose()


@pytest.fixture
def guard_on(monkeypatch):
    monkeypatch.setattr(ownership, "GUARD_ENABLED", True)
    monkeypatch.setattr(ownership, "GUARD_REPORT_ONLY", False)


@pytest.fixture
def guard_off(monkeypatch):
    """The baseline arm: the flag the product used to ship with (W0–W2)."""
    monkeypatch.setattr(ownership, "GUARD_ENABLED", False)
    monkeypatch.setattr(ownership, "GUARD_REPORT_ONLY", False)


# ---------------------------------------------------------------------------
# (a) the statement guard — every shape, baseline first
# ---------------------------------------------------------------------------

READS = {
    "text": lambda ids: text("SELECT company_name FROM applications"),
    "core-table": lambda ids: select(Application.__table__.c.company_name),
    "orm-select": lambda ids: select(Application.company_name),
    "count": lambda ids: select(func.count()).select_from(Application),
    "exists": lambda ids: select(exists().where(Application.id == ids[B])),
    # `joinedload` (US332's list): no model declares a relationship() today, so
    # an eager load cannot be written; its SQL is a JOIN that names the owned
    # table — the shape below. A future relationship() gets its own case here.
    "join": lambda ids: select(FlowSession.id).join(
        Application, Application.flow_session_id == FlowSession.id
    ),
    "subquery": lambda ids: select(JobAnalysis.id).where(
        JobAnalysis.id.in_(select(Application.job_analysis_id))
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(READS))
async def test_read_shapes_leak_without_the_guard(world, guard_off, shape):
    """Baseline: with the guard off and no owner named, B's row is reachable."""
    factory, ids = world
    assert ownership.current_owner() is None
    async with factory() as s:
        result = (await s.execute(READS[shape](ids))).all()
    assert result, shape  # rows (or a count/True) come back — nothing refused


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(READS))
async def test_read_shapes_are_refused_with_the_guard(world, guard_on, shape):
    factory, ids = world
    async with factory() as s:
        with pytest.raises(ownership.OwnerContextMissing):
            await s.execute(READS[shape](ids))


WRITES = {
    "bulk-update": lambda ids: update(Application).values(company_name="X"),
    "bulk-delete": lambda ids: delete(Application).where(Application.id == ids[B]),
    "text-update": lambda ids: text("UPDATE applications SET company_name = 'X'"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(WRITES))
async def test_write_shapes_change_rows_without_the_guard(world, guard_off, shape):
    factory, ids = world
    async with factory() as s:
        result = await s.execute(WRITES[shape](ids))
        assert result.rowcount >= 1
        await s.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(WRITES))
async def test_write_shapes_are_refused_with_the_guard(world, guard_on, shape):
    factory, ids = world
    async with factory() as s:
        with pytest.raises(ownership.OwnerContextMissing):
            await s.execute(WRITES[shape](ids))


@pytest.mark.asyncio
async def test_flush_insert_with_a_foreign_owner_needs_a_context(world, guard_on):
    """Unit-of-work INSERT naming B's id, no context: refused at the cursor."""
    factory, ids = world
    async with factory() as s:
        s.add(UploadRecord(
            user_id=B, original_filename="x.pdf", content_hash="0" * 64,
            mime_type="application/pdf", file_path="/x", byte_size=1,
        ))
        with pytest.raises(ownership.OwnerContextMissing, match="uploads"):
            await s.flush()


@pytest.mark.asyncio
async def test_flush_update_and_delete_need_a_context(world, guard_on):
    factory, ids = world
    async with factory() as s:
        with ownership.owner_context(A):
            row = await s.get(Application, ids[A])
        row.company_name = "changed outside any context"
        with pytest.raises(ownership.OwnerContextMissing):
            await s.flush()
        await s.rollback()
    async with factory() as s:
        with ownership.owner_context(A):
            row = await s.get(Application, ids[A])
        await s.delete(row)
        with pytest.raises(ownership.OwnerContextMissing):
            await s.flush()


@pytest.mark.asyncio
async def test_named_owner_and_unscoped_pass_the_guard(world, guard_on):
    factory, ids = world
    async with factory() as s:
        with ownership.owner_context(A):
            await s.execute(text("SELECT 1 FROM applications"))
        with ownership.unscoped("retention"):
            await s.execute(select(func.count()).select_from(Application))
        # not owned: no context needed
        await s.execute(select(JobAnalysis.id))
        await s.execute(select(User.id))


@pytest.mark.asyncio
async def test_report_mode_logs_and_records_instead_of_raising(world, guard_on, monkeypatch, caplog):
    factory, ids = world
    monkeypatch.setattr(ownership, "GUARD_REPORT_ONLY", True)
    ownership.clear_guard_reports()
    async with factory() as s:
        rows = (await s.execute(text("SELECT id FROM applications"))).all()
    assert len(rows) == 2
    reports = ownership.guard_reports()
    assert len(reports) == 1 and "applications" in reports[0]
    assert "report mode" in caplog.text
    ownership.clear_guard_reports()


# ---------------------------------------------------------------------------
# (b) loader criteria — defence in depth, never credited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loader_criteria_filter_orm_select_update_delete_for_the_user(world, guard_on):
    factory, ids = world
    async with factory() as s:
        with ownership.owner_context(A):
            seen = (await s.execute(select(Application.id))).scalars().all()
            assert seen == [ids[A]]
            res = await s.execute(update(Application).values(company_name="A-only"))
            assert res.rowcount == 1
            res = await s.execute(delete(Application).where(Application.id == ids[B]))
            assert res.rowcount == 0
            await s.rollback()


@pytest.mark.asyncio
async def test_loader_criteria_follow_each_context_in_sequence(world, guard_on):
    """W2 finding: a lambda criterion with an untracked default argument was
    cached with the FIRST user's id — B then read A's rows. Each context in
    turn must see only its own row."""
    factory, ids = world
    seen = {}
    for who in (A, B, A):
        async with factory() as s:
            with ownership.owner_context(who):
                seen.setdefault(who, []).append(
                    (await s.execute(select(Application.id))).scalars().all()
                )
    assert seen[A] == [[ids[A]], [ids[A]]]
    assert seen[B] == [[ids[B]]]


@pytest.mark.asyncio
async def test_loader_criteria_are_off_with_the_guard_off(world, guard_off):
    factory, ids = world
    async with factory() as s:
        with ownership.owner_context(A):
            seen = (await s.execute(select(Application.id))).scalars().all()
    assert sorted(seen) == sorted([ids[A], ids[B]])


@pytest.mark.asyncio
async def test_residuals_the_criteria_do_not_cover(world, guard_on):
    """Pinned residuals (ADR-092 cl. 8b): exists/count/text and an identity-map
    hit are NOT filtered — correctness rests on explicit predicates (get_owned)."""
    factory, ids = world
    async with factory() as s:
        with ownership.owner_context(A):
            assert (await s.execute(select(exists().where(Application.id == ids[B])))).scalar() is True
            assert (await s.execute(text("SELECT count(*) FROM applications"))).scalar() == 2
        with ownership.owner_context(B):
            b_row = await s.get(Application, ids[B])
        with ownership.owner_context(A):
            assert (await s.get(Application, ids[B])) is b_row  # identity-map hit, no SQL


# ---------------------------------------------------------------------------
# cl. 1 — owner fill and the chain-owner check
# ---------------------------------------------------------------------------


def _profile(owner):
    with authorized_profile_write():
        return MasterProfile(profile_json={}, user_id=owner)


@pytest.mark.asyncio
async def test_owner_fill_takes_the_user_context_and_is_counted(world, caplog):
    """Main ruling W1: a context fill is counted in FILL_STATS and logged."""
    factory, ids = world
    ownership.FILL_STATS.clear()
    async with factory() as s:
        with ownership.owner_context(A):
            up = UploadRecord(
                original_filename="x.pdf", content_hash="0" * 64,
                mime_type="application/pdf", file_path="/x", byte_size=1,
            )
            s.add(up)
            await s.flush()
            assert up.user_id == A
    assert ownership.FILL_STATS["uploads"] == 1
    assert "owner-fill from context" in caplog.text


@pytest.mark.asyncio
async def test_unscoped_never_names_an_owner(world):
    factory, ids = world
    async with factory() as s:
        with ownership.unscoped("tooling"):
            s.add(UploadRecord(
                original_filename="x.pdf", content_hash="0" * 64,
                mime_type="application/pdf", file_path="/x", byte_size=1,
            ))
            with pytest.raises(IntegrityError, match="user_id"):
                await s.flush()


@pytest.mark.asyncio
async def test_chain_row_copies_its_profiles_owner_not_the_context(world):
    factory, ids = world
    async with factory() as s:
        with ownership.unscoped("tooling"):
            pb = _profile(B)
            s.add(pb)
            await s.commit()
        async with factory() as s2:  # profile NOT loaded: the owner comes by SELECT
            with ownership.owner_context(A):
                cv = GeneratedCV(job_analysis_id=ids["job"], profile_id=pb.id, tailored_data={})
                s2.add(cv)
                ownership.FILL_STATS.clear()
                await s2.flush()
                assert cv.user_id == B
                assert ownership.FILL_STATS["generated_cvs"] == 0, "profile-derived, not counted"
                await s2.rollback()


@pytest.mark.asyncio
async def test_chain_row_added_in_the_same_flush_as_its_profile_takes_the_profiles_owner(world):
    """W3 finding (SF-OWN.9): profile and CV added together, in another user's
    context. The profile is pending (not in the identity map) and, with no FK
    ordering the two inserts, may not be stored when the CV's ``before_insert``
    runs — the fill used to fall through to the CONTEXT owner (A) and gave the
    CV an owner its profile does not have."""
    factory, ids = world
    async with factory() as s:
        with ownership.owner_context(A):
            pb = _profile(B)
            pb.id = uuid.uuid4()
            cv = GeneratedCV(job_analysis_id=ids["job"], profile_id=pb.id, tailored_data={})
            s.add_all([cv, pb])
            await s.flush()
            assert cv.user_id == B
            await s.rollback()


@pytest.mark.asyncio
async def test_chain_row_naming_another_owner_than_its_pending_profile_raises(world):
    factory, ids = world
    async with factory() as s:
        with ownership.unscoped("tooling"):
            pb = _profile(B)
            pb.id = uuid.uuid4()
            cv = GeneratedCV(
                job_analysis_id=ids["job"], profile_id=pb.id, tailored_data={}, user_id=A
            )
            s.add_all([cv, pb])
            with pytest.raises(ownership.OwnerMismatch):
                await s.flush()
            await s.rollback()


@pytest.mark.asyncio
async def test_chain_row_naming_another_owner_than_its_loaded_profile_raises(world):
    factory, ids = world
    async with factory() as s:
        with ownership.unscoped("tooling"):
            pa = _profile(A)
            s.add(pa)
            await s.flush()
            s.add(GeneratedCV(job_analysis_id=ids["job"], profile_id=pa.id, tailored_data={}, user_id=B))
            with pytest.raises(ownership.OwnerMismatch):
                await s.flush()


@pytest.mark.asyncio
async def test_residual_mismatch_with_an_unloaded_profile_is_not_checked(world):
    """Pinned residual: the check compares against a LOADED profile only (ADR wording)."""
    factory, ids = world
    with ownership.unscoped("tooling"):
        async with factory() as s:
            pa = _profile(A)
            s.add(pa)
            await s.commit()
        async with factory() as s:
            cv = GeneratedCV(job_analysis_id=ids["job"], profile_id=pa.id, tailored_data={}, user_id=B)
            s.add(cv)
            await s.flush()
            assert cv.user_id == B
            await s.rollback()


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_every_owned_table_but_snapshots_has_a_not_null_owner():
    for name in ownership.owned_tables() - {"profile_snapshots"}:
        col = Base.metadata.tables[name].c.user_id
        assert col.nullable is False, name
        assert [fk.column.table.name for fk in col.foreign_keys] == ["users"], name


def test_profile_snapshots_stay_chain_owned_by_cascade():
    """MD-4: no user_id; CASCADE with the profile."""
    t = Base.metadata.tables["profile_snapshots"]
    assert "user_id" not in t.c
    (fk,) = t.c.profile_id.foreign_keys
    assert fk.ondelete == "CASCADE"


def test_job_keyed_uniques_are_re_keyed_with_the_owner():
    """SF-OWN.7: no live-unique on a per-user table is keyed by the posting alone."""
    expected = {
        "uq_gap_jobs_live_kickoff": ["user_id", "job_analysis_id"],
        "uq_gap_analyses_live_fingerprint": ["user_id", "job_analysis_id", "input_fingerprint"],
        "uq_interview_sessions_active_per_job": ["user_id", "job_analysis_id"],
        "uq_master_profiles_user_live": ["user_id"],
    }
    found = {
        i.name: [c.name for c in i.columns]
        for t in Base.metadata.tables.values()
        for i in t.indexes
        if i.name in expected
    }
    assert found == expected
    for name in ownership.owned_tables():
        for i in Base.metadata.tables[name].indexes:
            cols = [c.name for c in i.columns]
            if i.unique and "job_analysis_id" in cols:
                assert "user_id" in cols, (name, i.name)


def test_the_posting_cache_has_no_owner():
    t = Base.metadata.tables["job_analyses"]
    assert "user_id" not in t.c
    assert "job_analyses" not in ownership.owned_tables()
    assert t.c.raw_text_origin.nullable is False
