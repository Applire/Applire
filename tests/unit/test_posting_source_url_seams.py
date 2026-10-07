# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-31 seams — a posting response carries the CALLER's source_url (w4-fix-own).

``job_analyses`` is a shared cache; its ``source_url`` is the first analyser's.
The REST doors are pinned by ``test_adv_ownership_posting_cache.py``; this file
pins the remaining call sites of the one builder (``services.job.posting_response``):
the MCP ``analyze_jd`` tool, the ``job://`` resource, the caller's OWN URL still
coming back, and ``create_application`` no longer copying the shared URL.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base
from applire.models.application import Application
from applire.models.job import JobAnalysis
from applire.models.user import User
from applire.services.job import _hash_text
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse

pytestmark = pytest.mark.no_owner_context

POSTING_TEXT = "Data Engineer (m/w/d) at Beta GmbH. SQL, Airflow, 3 years."
A_PRIVATE_URL = "https://boards.example/job/7?ref=alice-private-91c2"


@pytest_asyncio.fixture
async def world():
    eng = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(eng, expire_on_commit=False)
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            a = User(id=uuid.uuid4(), email="src-a@example.org", role="user")
            b = User(id=uuid.uuid4(), email="src-b@example.org", role="user")
            s.add_all([a, b])
            await s.flush()
            job = JobAnalysis(
                raw_text_hash=_hash_text(POSTING_TEXT), raw_text=POSTING_TEXT,
                source_url=A_PRIVATE_URL, raw_text_origin="scraped",
                role_title="Data Engineer", company_name="Beta GmbH",
                required_skills=["SQL"], language_requirement="German",
            )
            s.add(job)
            await s.flush()
            s.add(Application(user_id=a.id, job_analysis_id=job.id, company_name="Beta GmbH",
                              role_title="Data Engineer", source_url=A_PRIVATE_URL))
            await s.commit()
            job_id = job.id
    yield factory, a, b, job_id
    await eng.dispose()


async def _bind(factory, user):
    from applire.auth.tokens import create_token
    from applire.mcp import identity

    previous = identity.bound()
    async with factory() as s:
        _row, raw = await create_token(s, user_id=user.id, scope="agent", name="src")
        await s.commit()
        await identity.establish(s, raw)
    return previous


@pytest.fixture
def mcp_on(world, monkeypatch):
    import applire.mcp.server as server

    factory = world[0]

    @contextlib.asynccontextmanager
    async def _db():
        async with factory() as s:
            yield s

    monkeypatch.setattr(server, "get_db", _db)
    monkeypatch.setattr(server, "get_provider", lambda *a, **k: object())  # cache hit only
    return server


@pytest.mark.asyncio
async def test_mcp_analyze_jd_cache_hit_does_not_return_other_users_source_url(world, mcp_on):
    from applire.mcp import identity

    factory, _a, b, job_id = world
    previous = await _bind(factory, b)
    try:
        out = await mcp_on.analyze_jd(text=POSTING_TEXT)
    finally:
        identity.bind(previous)
    assert out["id"] == str(job_id)
    assert out.get("source_url") != A_PRIVATE_URL


@pytest.mark.asyncio
async def test_mcp_job_resource_does_not_return_other_users_source_url(world, mcp_on):
    from applire.mcp import identity

    factory, _a, b, job_id = world
    previous = await _bind(factory, b)
    try:
        await mcp_on.analyze_jd(text=POSTING_TEXT)  # B's link, by text
        raw = await mcp_on.resource_job(job_id=str(job_id))
    finally:
        identity.bind(previous)
    assert json.loads(raw).get("source_url") != A_PRIVATE_URL


@pytest.mark.asyncio
async def test_mcp_job_resource_returns_the_callers_own_source_url(world, mcp_on):
    """The fix is per caller, not a blanket strip: A still sees A's own URL."""
    from applire.mcp import identity

    factory, a, _b, job_id = world
    previous = await _bind(factory, a)
    try:
        raw = await mcp_on.resource_job(job_id=str(job_id))
    finally:
        identity.bind(previous)
    assert json.loads(raw).get("source_url") == A_PRIVATE_URL


@pytest.mark.asyncio
async def test_analyze_by_url_fills_the_callers_own_empty_link_and_returns_it(world):
    """B already has a link without a URL; B analyses again from B's own URL —
    the response carries B's URL (from B's row), not A's."""
    from applire.services.job import _link_and_respond

    factory, _a, b, job_id = world
    b_url = "https://boards.example/job/7?ref=bob"
    with ownership.owner_context(b.id):
        async with factory() as s:
            job = await s.get(JobAnalysis, job_id)
            s.add(Application(user_id=b.id, job_analysis_id=job_id, company_name="Beta GmbH",
                              role_title="Data Engineer", source_url=None))
            await s.commit()
            resp = await _link_and_respond(s, job, b.id, POSTING_TEXT, b_url, None, None)
    assert resp.source_url == b_url


@pytest.mark.asyncio
async def test_create_application_does_not_copy_the_shared_source_url(world, monkeypatch):
    """MD-31: the `request.source_url or job.source_url` fallback is gone.

    Today every caller already holds a link row when it creates (analyze links
    first), so the new-row branch is reached only if a link were ever created
    lazily — the access check is stubbed to model exactly that path."""
    import applire.services.job as job_svc
    from applire.schemas.application import CreateApplicationRequest
    from applire.services.application import create_application

    factory, _a, b, job_id = world

    async def _granted(db, jid, uid):
        return await db.get(JobAnalysis, jid)

    monkeypatch.setattr(job_svc, "get_job_for_user", _granted)
    with ownership.owner_context(b.id):
        async with factory() as s:
            resp = await create_application(
                b.id, CreateApplicationRequest(job_analysis_id=job_id), s
            )
            row = (await s.execute(
                select(Application).where(Application.user_id == b.id)
            )).scalar_one()
    assert resp.source_url != A_PRIVATE_URL
    assert row.source_url is None
