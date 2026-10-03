# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/health`` is liveness only; the instance facts moved behind ``admin_or_probe``
(US329; ADR-091 cl. 19; ADR-086/087 amended 2026-10-03, rulings S-16 + RD-1).

Takes over the intent of ``test_instance_state_and_upgrade_notice.py`` §11
(NEEDS-EDIT 1c-3): the frozen-field contract is now three fields, and the
additive fields (``upgrade_notice``, ``debug_log_on``, ``topology``, ``ops``) are
asserted on ``GET /api/ops/health``.
"""

import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from applire.auth import get_auth_provider
from applire.models.user import User

LIVENESS_FIELDS = {"status", "edition", "version"}
MOVED_FIELDS = {"llm_provider", "upgrade_notice", "debug_log_on", "topology", "ops"}


@pytest.fixture(autouse=True)
def clean_upgrade_notice():
    from applire.routers.health import set_upgrade_notice

    set_upgrade_notice(None)
    yield
    set_upgrade_notice(None)


@pytest_asyncio.fixture
async def health_client():
    from applire.routers.health import router

    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_health_returns_exactly_the_three_frozen_fields(health_client):
    from applire._version import __version__
    from applire.config import HAS_CLOUD

    resp = await health_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == LIVENESS_FIELDS
    assert body["status"] == "ok"
    assert body["edition"] == ("cloud" if HAS_CLOUD else "community")
    assert body["version"] == __version__


@pytest.mark.asyncio
async def test_health_discloses_none_of_the_moved_fields_even_when_set(health_client, monkeypatch):
    """A published notice, debug log on, dev topology — none of it reaches /health."""
    import applire.config as cfg
    from applire.routers.health import set_upgrade_notice

    set_upgrade_notice({"from": "0.42.0", "to": "0.43.0", "unset": [], "re_meant": ["AUTH_PROVIDER"]})
    monkeypatch.setattr(cfg.settings, "llm_debug_log", True)
    monkeypatch.setattr(cfg.settings, "applire_topology", "dev")
    body = (await health_client.get("/health")).json()
    assert not (MOVED_FIELDS & set(body)), body
    assert "AUTH_PROVIDER" not in str(body)


@pytest.mark.asyncio
async def test_health_needs_no_credential_and_no_database(health_client):
    """The compose healthcheck calls it bare; nothing here may depend on Postgres."""
    resp = await health_client.get("/health", headers={})
    assert resp.status_code == 200


class _AdminProvider:
    async def get_current_user(self, request, db):
        user = User(id=uuid.uuid4(), email="admin@example.org")
        user.role = "admin"
        return user


@pytest_asyncio.fixture
async def ops_client(monkeypatch):
    """The ops router with an admin provider and a throwaway SQLite DB."""
    import applire.providers.llm as llm
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.db.session import Base, get_db
    from applire.models.llm_usage import LlmUsage
    from applire.models.retention_run import RetentionRun
    from applire.routers import ops as ops_router
    from applire.services.ops import aggregate, probes

    class _Stub:
        async def acomplete(self, *_a, **_k):
            return "pong"

    probes.reset_provider_cache()
    aggregate.reset_state()
    monkeypatch.setattr(probes, "_code_head", lambda: None)
    monkeypatch.setattr(llm, "get_provider", lambda: _Stub())
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all, tables=[RetentionRun.__table__, LlmUsage.__table__]
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with maker() as s:
            yield s

    async def _provider():
        return _AdminProvider()

    app = FastAPI()
    app.include_router(ops_router.router)
    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_auth_provider] = _provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ops") as c:
        yield c
    await engine.dispose()
    probes.reset_provider_cache()
    aggregate.reset_state()


@pytest.mark.asyncio
async def test_moved_fields_are_served_on_the_ops_endpoint(ops_client, monkeypatch):
    import applire.config as cfg

    monkeypatch.setattr(cfg.settings, "llm_debug_log", False)
    body = (await ops_client.get("/api/ops/health")).json()
    assert MOVED_FIELDS <= set(body)
    assert body["upgrade_notice"] is None
    assert body["debug_log_on"] is False
    assert body["topology"] == cfg.settings.applire_topology
    assert body["llm_provider"] == cfg.settings.llm_provider
    assert set(body["ops"]) == {"status", "since"}


@pytest.mark.asyncio
async def test_ops_endpoint_reflects_the_published_upgrade_notice(ops_client):
    from applire.routers.health import set_upgrade_notice

    notice = {"from": "0.30.0", "to": "0.41.0", "unset": [], "re_meant": []}
    set_upgrade_notice(notice)
    body = (await ops_client.get("/api/ops/health")).json()
    assert body["upgrade_notice"] == notice


@pytest.mark.asyncio
async def test_ops_endpoint_reports_debug_log_and_topology(ops_client, monkeypatch):
    import applire.config as cfg

    monkeypatch.setattr(cfg.settings, "llm_debug_log", True)
    monkeypatch.setattr(cfg.settings, "applire_topology", "dev")
    body = (await ops_client.get("/api/ops/health")).json()
    assert body["debug_log_on"] is True
    assert body["topology"] == "dev"
