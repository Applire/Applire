# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-user erasure (ADR-092 cl. 11, RD-3, US336; System-FMEA SF-OWN / SF-RET).

Users A and B each own one of every owned kind; they SHARE one posting (S-17)
and A also has a posting only A references. A's erasure must:

* delete every owned row of A (both scopes) and nothing of B's — compared
  column by column before/after (``_snapshot``);
* keep the shared posting (B still references it) and delete A's private one;
* vault: clear A's signature path, keep A's settings row and user row;
* account: delete A's settings row and identity rows, detach A's ``llm_usage``;
* run through the router door (``DELETE /api/profile``) as an adapter and
  through 1b's account deletion (disable + revoke → erase → tombstone).

Each referencing table alone keeps a posting alive (the NOT EXISTS covers all
seven — parametrised), and the set of seven equals the FK catalogue.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base
from applire.models.auth import AuthLink, AuthSession, PersonalToken, ReauthGrant
from applire.models.job import JobAnalysis
from applire.models.llm_usage import LlmUsage
from applire.models.user import User
from applire.models.user_settings import UserSettings
from applire.services import erasure
from tests.support.isolation import OwnerWorld
from tests.support.profile_factory import set_profile_json

pytestmark = pytest.mark.no_owner_context


class _Storage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, path: str) -> None:
        self.deleted.append(path)


async def _build(s, user: User, *, shared_job: JobAnalysis | None) -> dict:
    w = OwnerWorld(s, user)
    if shared_job is not None:
        w._cache["job"] = shared_job  # B links to A's posting (S-17)
    await w.cv()
    await w.cover_letter()
    await w.gap_analysis()
    await w.interview()
    await w.enrich_session()
    await w.gap_job()
    await w.import_job()
    await w.staged_upload()
    await w.flow()
    profile = await w.profile()
    set_profile_json(profile, {
        "personal_info": {"name": f"Owner {user.email}", "photo_url": f"/photos/{user.id}.jpg"}
    })
    s.add(UserSettings(user_id=user.id, signature_path=f"/sig/{user.id}.png"))
    sess = AuthSession(user_id=user.id, token_hash=uuid.uuid4().hex)
    s.add(sess)
    await s.flush()
    now = datetime.now(timezone.utc)
    s.add_all([
        ReauthGrant(user_id=user.id, session_id=sess.id, action="account.delete",
                    target_id=user.id, expires_at=now + timedelta(minutes=5)),
        PersonalToken(user_id=user.id, scope="agent", name="t", prefix=uuid.uuid4().hex[:8],
                      token_hash=uuid.uuid4().hex),
        AuthLink(user_id=user.id, purpose="reset", token_hash=uuid.uuid4().hex,
                 expires_at=now + timedelta(hours=1)),
        LlmUsage(user_id=user.id, provider="mistral", total_tokens=10),
    ])
    await s.flush()
    return {"world": w, "job": await w.job()}


def _fk_col(table):
    return next(c for c in table.columns if any(fk.column.table.name == "job_analyses" for fk in c.foreign_keys))


@pytest_asyncio.fixture
async def two_worlds():
    eng = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(eng, expire_on_commit=False)
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            a = User(id=uuid.uuid4(), email="erase-a@example.org", role="user")
            b = User(id=uuid.uuid4(), email="erase-b@example.org", role="user")
            s.add_all([a, b])
            await s.flush()
            wa = await _build(s, a, shared_job=None)
            wb = await _build(s, b, shared_job=wa["job"])
            # A's private posting: only A references it (an extra application).
            private = JobAnalysis(raw_text_hash=uuid.uuid4().hex, raw_text="Private.",
                                  role_title="Solo", seniority_level="mid",
                                  language_requirement="English")
            s.add(private)
            await s.flush()
            from applire.models.application import Application

            s.add(Application(user_id=a.id, job_analysis_id=private.id,
                              company_name="Solo", role_title="Solo"))
            # A posting NOBODY references yet (another user's analysis whose link
            # is still being created) — not A's to purge; retention's age-floored
            # orphan rule owns it (cl. 12c).
            stray = JobAnalysis(raw_text_hash=uuid.uuid4().hex, raw_text="Stray.",
                                role_title="Stray", seniority_level="mid",
                                language_requirement="English")
            s.add(stray)
            await s.commit()
            factory.stray_id = stray.id
    yield factory, a, b, wa["job"].id, private.id
    await eng.dispose()


async def _owned_snapshot(factory, owner_id) -> dict[str, list[tuple]]:
    out = {}
    with ownership.unscoped("tooling"):
        async with factory() as s:
            for name in sorted(ownership.owned_tables() - {"profile_snapshots"}):
                t = Base.metadata.tables[name]
                rows = (await s.execute(select(t).where(t.c.user_id == owner_id))).all()
                out[name] = sorted(tuple(map(repr, r)) for r in rows)
            for name in ("auth_sessions", "personal_tokens", "auth_links", "reauth_grants", "llm_usage"):
                t = Base.metadata.tables[name]
                rows = (await s.execute(select(t).where(t.c.user_id == owner_id))).all()
                out[name] = sorted(tuple(map(repr, r)) for r in rows)
    return out


async def _job_exists(factory, job_id) -> bool:
    with ownership.unscoped("tooling"):
        async with factory() as s:
            return (await s.get(JobAnalysis, job_id)) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["vault", "account"])
async def test_erase_deletes_all_of_a_and_nothing_of_b(two_worlds, scope):
    factory, a, b, shared, private = two_worlds
    b_before = await _owned_snapshot(factory, b.id)
    assert all(b_before[t] for t in ("master_profiles", "applications", "uploads", "user_settings"))
    storage = _Storage()

    async with factory() as s:
        counts = await erasure.erase(s, a.id, scope, storage=storage)

    a_after = await _owned_snapshot(factory, a.id)
    owned_left = {t: rows for t, rows in a_after.items()
                  if rows and t in ownership.owned_tables() and t != "user_settings"}
    assert owned_left == {}, f"A's owned rows survived: {sorted(owned_left)}"
    assert await _owned_snapshot(factory, b.id) == b_before, "B's rows changed under A's erasure"
    # Shared posting kept (B references it), A's private posting purged.
    assert await _job_exists(factory, shared)
    assert not await _job_exists(factory, private)
    assert await _job_exists(factory, factory.stray_id), "erasure purged a posting A never referenced"
    assert counts["job_analyses"] == 1
    # Files after the commit: A's upload, photo and signature — never B's.
    assert any(str(a.id) in p for p in storage.deleted)
    assert not any(str(b.id) in p for p in storage.deleted)

    with ownership.unscoped("tooling"):
        async with factory() as s:
            settings_row = (
                await s.execute(select(UserSettings).where(UserSettings.user_id == a.id))
            ).scalar_one_or_none()
            usage = (await s.execute(select(LlmUsage))).scalars().all()
            user_a = await s.get(User, a.id)
    assert user_a is not None and user_a.deleted_at is None  # tombstone = 1b's step 3
    assert user_a.photo_consent is False
    if scope == "vault":
        assert settings_row is not None and settings_row.signature_path is None
        assert a_after["auth_sessions"] and a_after["personal_tokens"] and a_after["llm_usage"]
    else:
        assert settings_row is None
        for t in ("auth_sessions", "personal_tokens", "auth_links", "reauth_grants", "llm_usage"):
            assert a_after[t] == [], t
        # A's usage history stays as instance data, detached (ON DELETE SET NULL intent).
        assert sum(1 for u in usage if u.user_id is None) == 1
        assert counts["llm_usage_detached"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("table", erasure.POSTING_REFERENCES)
async def test_a_posting_referenced_by_any_one_table_survives_the_purge(table):
    """Each of the seven referencing tables ALONE keeps a posting alive."""
    eng = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(eng, expire_on_commit=False)
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            b = User(id=uuid.uuid4(), email="ref-b@example.org", role="user")
            s.add(b)
            await s.flush()
            w = OwnerWorld(s, b)
            builders = {
                "applications": w.application, "generated_cvs": w.cv,
                "generated_cover_letters": w.cover_letter, "gap_analyses": w.gap_analysis,
                "gap_analysis_jobs": w.gap_job, "interview_sessions": w.interview,
                "flow_sessions": w.flow,
            }
            await builders[table]()
            job_id = (await w.job()).id
            await s.commit()
            # Strip every OTHER reference so only `table` still points at the posting.
            for other in erasure.POSTING_REFERENCES:
                if other == table:
                    continue
                t = Base.metadata.tables[other]
                if other == "applications":
                    await s.execute(t.update().values(flow_session_id=None))
                await s.execute(t.delete().where(_fk_col(t) == job_id))
            await s.commit()
        async with factory() as s:
            n = await erasure.purge_unreferenced_postings(s, [job_id])
            await s.commit()
    assert n == 0
    assert await _job_exists(factory, job_id)
    # And with that last reference gone, the same call purges it.
    with ownership.unscoped("tooling"):
        async with factory() as s:
            t = Base.metadata.tables[table]
            if table == "flow_sessions":
                apps = Base.metadata.tables["applications"]
                await s.execute(apps.update().values(flow_session_id=None))
            await s.execute(t.delete().where(_fk_col(t) == job_id))
            await s.commit()
            assert await erasure.purge_unreferenced_postings(s, [job_id]) == 1
            await s.commit()
    await eng.dispose()


def test_posting_references_equal_the_fk_catalogue():
    """A new table with an FK into job_analyses must join the refcount (cl. 11)."""
    referencing = sorted(
        t.name
        for t in Base.metadata.sorted_tables
        for fk in t.foreign_keys
        if fk.column.table.name == "job_analyses"
    )
    assert referencing == sorted(erasure.POSTING_REFERENCES)
    assert sorted(m.__tablename__ for m in erasure._posting_reference_models()) == referencing


@pytest.mark.asyncio
async def test_router_delete_profile_is_an_adapter_scoped_to_the_caller(two_worlds, monkeypatch):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.main import app
    from applire.routers import profile as profile_router

    factory, a, b, shared, private = two_worlds
    b_before = await _owned_snapshot(factory, b.id)

    async def _db():
        async with factory() as s:
            yield s

    class _AsA:
        async def get_current_user(self, request, db=None):  # noqa: ANN001
            return a

    storage = _Storage()
    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = lambda: _AsA()
    app.dependency_overrides[profile_router._get_storage] = lambda: storage
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.delete("/api/profile")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["records_deleted"]["master_profiles"] == 1
    assert body["records_deleted"]["cv_import_jobs"] == 1  # newly covered (cl. 11)
    assert await _owned_snapshot(factory, b.id) == b_before
    assert await _job_exists(factory, shared) and not await _job_exists(factory, private)


@pytest.mark.asyncio
async def test_account_deletion_real_path_disable_erase_tombstone(two_worlds, monkeypatch):
    """1b's three-step order on the REAL erase (W2 brief: the stubbed-erase test's
    real-path twin): disabled + revoked, data erased, user tombstoned, audited."""
    from applire.models.audit import AuditEvent
    from applire.services.admin import users as accounts

    factory, a, b, shared, private = two_worlds
    b_before = await _owned_snapshot(factory, b.id)
    monkeypatch.setattr("applire.storage.get_storage", lambda: _Storage())
    with ownership.unscoped("tooling"):
        async with factory() as s:
            me = await s.get(User, a.id)
            await accounts.delete_account(s, actor=me, user_id=a.id, by="self")
        async with factory() as s:
            user_a = await s.get(User, a.id)
            audit = (await s.execute(select(AuditEvent))).scalars().all()
    assert user_a.deleted_at is not None and user_a.disabled_at is not None
    assert user_a.email == f"deleted+{a.id}@invalid"
    a_after = await _owned_snapshot(factory, a.id)
    assert {t: r for t, r in a_after.items() if r} == {}
    assert await _owned_snapshot(factory, b.id) == b_before
    deleted = [e for e in audit if e.action == "user.deleted"]
    assert len(deleted) == 1 and deleted[0].detail["erased_rows"] > 0


@pytest.mark.asyncio
async def test_a_failed_erasure_rolls_back_everything(two_worlds, monkeypatch):
    factory, a, b, shared, private = two_worlds
    a_before = await _owned_snapshot(factory, a.id)

    async def _boom(*_a, **_k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(erasure, "purge_unreferenced_postings", _boom)
    async with factory() as s:
        with pytest.raises(erasure.ErasureFailed):
            await erasure.erase(s, a.id, "account", storage=_Storage())
    assert await _owned_snapshot(factory, a.id) == a_before
    assert await _job_exists(factory, private)


@pytest.mark.asyncio
async def test_erase_retries_once_on_integrity_error(two_worlds, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    factory, a, b, shared, private = two_worlds
    real = erasure.purge_unreferenced_postings
    calls = {"n": 0}

    async def _flaky(db, candidates=None, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise IntegrityError("DELETE", {}, Exception("fk: a link was created"))
        return await real(db, candidates, **kw)

    monkeypatch.setattr(erasure, "purge_unreferenced_postings", _flaky)
    async with factory() as s:
        counts = await erasure.erase(s, a.id, "vault", storage=_Storage())
    assert calls["n"] == 2
    assert counts["master_profiles"] == 1
    assert not await _job_exists(factory, private)
