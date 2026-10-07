# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial ownership probes on the shared posting cache ``job_analyses`` (w4-adv-ownership).

Each ``test_adv_ownership_<n>_*`` asserts the ISOLATED behaviour and fails on a leak.
A and B share one posting (A scraped it from a personal URL); B reaches it by text hash.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.auth import get_auth_provider
from applire.db.session import Base, get_db
from applire.main import app
from applire.models.application import Application
from applire.models.job import JobAnalysis
from applire.models.user import User
from applire.routers import job as job_router
from applire.services.job import _hash_text
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse

pytestmark = pytest.mark.no_owner_context

POSTING_TEXT = "Senior Engineer (m/w/d) at Acme. Python, FastAPI, 5 years."
A_PRIVATE_URL = "https://boards.example/job/42?recruiter_token=alice-private-7f3a"


class _AsUser:
    def __init__(self, user: User) -> None:
        self.user = user

    async def get_current_user(self, request, db=None):  # noqa: ANN001
        return self.user


@pytest_asyncio.fixture
async def world():
    eng = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    with ownership.unscoped("tooling"):
        async with factory() as s:
            a = User(id=uuid.uuid4(), email="adv-a@example.org", role="user")
            b = User(id=uuid.uuid4(), email="adv-b@example.org", role="user")
            s.add_all([a, b])
            await s.flush()
            job = JobAnalysis(
                raw_text_hash=_hash_text(POSTING_TEXT),
                raw_text=POSTING_TEXT,
                source_url=A_PRIVATE_URL,
                raw_text_origin="scraped",
                role_title="Senior Engineer",
                company_name="Acme",
                required_skills=["Python"],
                language_requirement="English",
            )
            s.add(job)
            await s.flush()
            s.add(Application(user_id=a.id, job_analysis_id=job.id, company_name="Acme",
                              role_title="Senior Engineer", source_url=A_PRIVATE_URL))
            await s.commit()
            ids = {"job": job.id}
    yield factory, a, b, ids
    await eng.dispose()


async def _call_as(factory, user, method, url, json=None):
    async def _db():
        async with factory() as s:
            yield s

    def _no_provider():
        return object()  # a cache hit never touches the provider

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = lambda: _AsUser(user)
    app.dependency_overrides[job_router._get_provider] = _no_provider
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await asyncio.wait_for(c.request(method, url, json=json), timeout=20)
    finally:
        for k in (get_db, get_auth_provider, job_router._get_provider):
            app.dependency_overrides.pop(k, None)


@pytest.mark.asyncio
async def test_adv_ownership_101_cache_hit_by_text_does_not_return_other_users_source_url(world):
    """B pastes the exact posting text A scraped from A's personal URL: the response
    must not echo A's URL (it may carry a recruiter/tracking token; MD-10, cl. 5)."""
    factory, a, b, ids = world
    resp = await _call_as(factory, b, "POST", "/api/job/analyze", {"text": POSTING_TEXT})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == str(ids["job"])  # it IS the shared row (precondition)
    assert resp.json().get("source_url") != A_PRIVATE_URL, (
        f"B's analyze response leaks A's source_url: {resp.json().get('source_url')!r}"
    )


@pytest.mark.asyncio
async def test_adv_ownership_102_get_job_does_not_return_other_users_source_url(world):
    """After linking by text, B's GET /api/job/{id} must not carry A's URL."""
    factory, a, b, ids = world
    r1 = await _call_as(factory, b, "POST", "/api/job/analyze", {"text": POSTING_TEXT})
    assert r1.status_code == 200, r1.text
    r2 = await _call_as(factory, b, "GET", f"/api/job/{ids['job']}")
    assert r2.status_code == 200, r2.text
    assert r2.json().get("source_url") != A_PRIVATE_URL, (
        f"GET /api/job/{{id}} as B leaks A's source_url: {r2.json().get('source_url')!r}"
    )
