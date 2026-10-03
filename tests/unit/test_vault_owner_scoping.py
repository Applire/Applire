# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The vault is the CALLER's (ADR-092 cl. 2, 14; US333 profile half; SF-OWN.1/.2).

The routes without an id (``GET /api/profile``, ``/exists``, ``/health``,
``/changes``, ``/enrichment-history``, ``/export``, ``PATCH /{section}``,
settings, roles) read "my" vault. Before Strawberry that meant "the newest live
profile of anyone" (``_get_latest``); here users A and B each own a vault and
every such door must return the caller's — and a caller without a vault gets the
empty answer even while another user has one. Plus the service-level contract:
``create_profile_record`` races to one live row per owner, a vault read without
an owner refuses, and ranking only sees the caller's linked postings.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base
from applire.models.profile import MasterProfile
from applire.models.user import User
from applire.models.user_settings import UserSettings
from tests.support.isolation import OwnerWorld

pytestmark = pytest.mark.no_owner_context


@pytest_asyncio.fixture
async def world():
    eng = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(eng, expire_on_commit=False)
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            a = User(id=uuid.uuid4(), email="vault-a@example.org", role="user")
            b = User(id=uuid.uuid4(), email="vault-b@example.org", role="user")
            c = User(id=uuid.uuid4(), email="vault-c@example.org", role="user")  # no vault
            s.add_all([a, b, c])
            await s.flush()
            await OwnerWorld(s, a).profile()
            # B's profile is the NEWEST row — the pre-3b read would hand it to everyone.
            await OwnerWorld(s, b).profile()
            s.add(UserSettings(user_id=a.id, ui_language="de"))
            await s.commit()
    yield factory, a, b, c
    await eng.dispose()


async def _call(factory, user, method, path, json=None):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.main import app

    async def _db():
        async with factory() as s:
            yield s

    class _As:
        async def get_current_user(self, request, db=None):  # noqa: ANN001
            return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = lambda: _As()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.request(method, path, json=json)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth_provider, None)


@pytest.mark.asyncio
async def test_get_profile_returns_the_callers_vault(world):
    factory, a, b, c = world
    ra = await _call(factory, a, "GET", "/api/profile")
    rb = await _call(factory, b, "GET", "/api/profile")
    assert ra.status_code == rb.status_code == 200
    assert ra.json()["profile"]["personal_info"]["name"] == f"Owner {a.email}"
    assert rb.json()["profile"]["personal_info"]["name"] == f"Owner {b.email}"


@pytest.mark.asyncio
async def test_a_caller_without_a_vault_never_sees_another_users(world):
    factory, a, b, c = world
    assert (await _call(factory, c, "GET", "/api/profile")).status_code == 404
    assert (await _call(factory, c, "GET", "/api/profile/exists")).json()["exists"] is False
    health = (await _call(factory, c, "GET", "/api/profile/health")).json()
    assert health["completeness"]["score"] == 0.0
    assert (await _call(factory, c, "GET", "/api/profile/enrichment-history")).json() == []
    export = (await _call(factory, c, "GET", "/api/profile/export")).json()
    assert export["profile"] is None and export["user"]["email"] == c.email


@pytest.mark.asyncio
async def test_patch_section_writes_the_callers_vault_only(world):
    factory, a, b, c = world
    r = await _call(factory, b, "PATCH", "/api/profile/personal_info", json={"location": "B-Town"})
    assert r.status_code == 200, r.text
    with ownership.unscoped("tooling"):
        async with factory() as s:
            rows = {p.user_id: p.profile_json for p in (await s.execute(select(MasterProfile))).scalars()}
    assert rows[b.id]["personal_info"].get("location") == "B-Town"
    assert rows[a.id]["personal_info"].get("location") in (None, "")


@pytest.mark.asyncio
async def test_settings_are_per_user(world):
    factory, a, b, c = world
    assert (await _call(factory, b, "GET", "/api/settings")).json()["ui_language_explicit"] is False
    r = await _call(factory, b, "PATCH", "/api/settings", json={"ui_language": "en"})
    assert r.status_code == 200, r.text
    ga = (await _call(factory, a, "GET", "/api/settings")).json()
    assert ga["ui_language"] == "de" and ga["ui_language_explicit"] is True
    with ownership.unscoped("tooling"):
        async with factory() as s:
            owners = sorted(str(r.user_id) for r in (await s.execute(select(UserSettings))).scalars())
    assert owners == sorted([str(a.id), str(b.id)])  # B got its OWN row (D-10)


@pytest.mark.asyncio
async def test_a_vault_read_without_an_owner_refuses(world):
    from applire.services.profile import get_profile

    factory, *_ = world
    async with factory() as s:
        with pytest.raises(ownership.OwnerContextMissing):
            await get_profile(s)
        with ownership.unscoped("retention"):  # an unscoped context is not an owner
            with pytest.raises(ownership.OwnerContextMissing):
                await get_profile(s)


@pytest.mark.asyncio
async def test_create_profile_record_race_ends_on_one_live_row(world):
    """Two check-then-insert creators for one owner (cl. 2): the loser's INSERT
    hits ``uq_master_profiles_user_live`` inside a savepoint and re-reads the
    winner instead of failing the request."""
    from applire.services.profile.commit import create_profile_record

    factory, a, b, c = world
    async with factory() as s1:
        first = await create_profile_record(s1, c.id)
        await s1.commit()
    async with factory() as s2:
        second = await create_profile_record(s2, c.id)
        await s2.commit()
    assert second.id == first.id
    with ownership.unscoped("tooling"):
        async with factory() as s:
            live = (await s.execute(
                select(MasterProfile).where(MasterProfile.user_id == c.id, MasterProfile.deleted_at.is_(None))
            )).scalars().all()
    assert len(live) == 1


@pytest.mark.asyncio
async def test_rank_jobs_sees_only_the_callers_linked_postings(world):
    from applire.services.matching import rank_jobs

    factory, a, b, c = world
    with ownership.unscoped("tooling"):
        async with factory() as s:
            wa, wb = OwnerWorld(s, a), OwnerWorld(s, b)
            app_a = await wa.application()
            app_a.role_title = "A's own title"
            await wb.application()
            pa = (await s.execute(select(MasterProfile).where(MasterProfile.user_id == a.id))).scalar_one()
            pb = (await s.execute(select(MasterProfile).where(MasterProfile.user_id == b.id))).scalar_one()
            await s.commit()
    async with factory() as s:
        res = await rank_jobs(pa.id, s, user_id=a.id)
        assert [r.job_id for r in res] == [app_a.job_analysis_id]
        assert res[0].role_title == "A's own title"  # the caller's override (cl. 5f)
        with pytest.raises(LookupError):
            await rank_jobs(pb.id, s, user_id=a.id)  # B's profile id is not A's


@pytest.mark.asyncio
async def test_signature_settings_row_is_the_callers(world):
    """ADR-088 signature lives on the CALLER's settings row (D-10)."""
    from applire.services import signature

    factory, a, b, c = world
    with ownership.unscoped("tooling"):
        async with factory() as s:
            row = (await s.execute(select(UserSettings).where(UserSettings.user_id == a.id))).scalar_one()
            row.signature_path = "/sig/a.png"
            await s.commit()
    async with factory() as s:
        assert await signature.resolve_signature_available(s, user_id=a.id) is True
        assert await signature.resolve_signature_available(s, user_id=b.id) is False
        with ownership.owner_context(b.id):  # fallback: the context, never "the" row
            assert await signature.resolve_signature_available(s) is False


@pytest.mark.asyncio
async def test_a_vault_write_resweeps_only_the_owners_fact_pins(world):
    """``_sweep_fact_pins`` (ADR-077 cl. 7) re-verifies pins against the vault
    just written — only the vault OWNER's applications; B's pin, which quotes
    B's vault, must not be marked stale by A's write."""
    from applire.models.application import Application
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import SetProfileMeta

    factory, a, b, c = world
    pin = {"pin_id": "p1", "entry_type": "skill", "entry_id": "s-b", "quote": "Kotlin",
           "targets": ["cv", "letter"], "stale": False}
    with ownership.unscoped("tooling"):
        async with factory() as s:
            await OwnerWorld(s, a).application()
            app_b = await OwnerWorld(s, b).application()
            app_b.pinned_facts = [pin]
            await s.commit()
    async with factory() as s:
        with ownership.owner_context(a.id):
            record = (await s.execute(select(MasterProfile).where(MasterProfile.user_id == a.id))).scalar_one()
            await commit_ops(
                s, [SetProfileMeta(key="na_fields", value="summary")],
                CommitProvenance(source="manual_edit", intake="gap_na", actor="candidate"),
                record=record, grounding=None, snapshot=None,
            )
            await s.commit()
    with ownership.unscoped("tooling"):
        async with factory() as s:
            stored = (await s.execute(select(Application.pinned_facts).where(Application.user_id == b.id))).scalar_one()
    assert stored[0].get("stale") in (False, None), "A's write re-swept B's pin against A's vault"
