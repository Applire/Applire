# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ops endpoint's contract (ADR-086 clause 2 and clause 4, US312).

Mounted on a bare FastAPI app rather than `applire.main`: `main.py` belongs to
another work package this run, so the `include_router` line ships as a report
patch and these tests exercise the router directly — which is also what a
contract test should do.

Three things are asserted here and nowhere else:

* the **verdict is in the HTTP status** (200 for ok/degraded, 503 for down), so
  an external uptime probe never has to parse the body;
* the payload is **stable and additive** — the top-level keys are pinned, so a
  rename fails a named test instead of breaking somebody's Uptime Kuma;
* the payload **discloses nothing forbidden** (``SF-OPS.5``) — this is the
  enforcement of ADR-086 clause 4, which is only prose without it.
"""

import json

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.db.session import Base, get_db
from applire.models.llm_usage import LlmUsage
from applire.models.retention_run import RetentionRun
from applire.routers import ops as ops_router
from applire.services.ops import aggregate, probes

_SQLITE_URL = "sqlite+aiosqlite:///:memory:"


class _StubProvider:
    async def acomplete(self, *_a, **_k):
        return "pong"


@pytest_asyncio.fixture
async def client(monkeypatch):
    import applire.providers.llm as llm

    probes.reset_provider_cache()
    aggregate.reset_state()
    monkeypatch.setattr(probes, "_code_head", lambda: None)
    monkeypatch.setattr(llm, "get_provider", lambda: _StubProvider())

    engine = create_async_engine(_SQLITE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[RetentionRun.__table__, LlmUsage.__table__],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with maker() as session:
            yield session

    app = FastAPI()
    app.include_router(ops_router.router)
    app.dependency_overrides[get_db] = _get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://ops") as c:
        yield c
    await engine.dispose()
    probes.reset_provider_cache()
    aggregate.reset_state()


@pytest.mark.asyncio
async def test_endpoint_answers_200_when_not_down(client):
    response = await client.get("/api/ops/health")
    assert response.status_code == 200
    assert response.json()["status"] in ("ok", "degraded")


@pytest.mark.asyncio
async def test_degraded_is_deliberately_200_not_503(client, monkeypatch):
    """A stale retention run must not page anyone at 3 a.m.

    The distinction is the whole reason the verdict has three values instead of
    two: a probe that cried wolf would be muted within a week, and then the
    layer detects nothing at all.
    """
    response = await client.get("/api/ops/health")
    body = response.json()
    assert body["status"] == "degraded"  # no retention run, no backup, no heads
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_endpoint_answers_503_when_the_verdict_is_down(client, monkeypatch):
    async def _dead(_db):
        return probes.ProbeResult("database", probes.DOWN, "database unreachable")

    monkeypatch.setattr(probes, "probe_database", _dead)
    response = await client.get("/api/ops/health")
    assert response.status_code == 503
    assert response.json()["status"] == "down"


@pytest.mark.asyncio
async def test_the_payloads_top_level_keys_are_a_contract(client):
    """Additive-only: adding a key is fine, renaming one is a breaking change."""
    body = (await client.get("/api/ops/health")).json()
    assert {
        "status",
        "edition",
        "version",
        "llm_provider",
        "checked_at",
        "components",
        "usage",
    } <= set(body)


@pytest.mark.asyncio
async def test_every_probe_appears_in_the_payload(client):
    body = (await client.get("/api/ops/health")).json()
    assert set(body["components"]) == set(probes.PROBE_NAMES)
    for component in body["components"].values():
        assert set(component) == {"status", "message", "detail"}


@pytest.mark.asyncio
async def test_the_payload_discloses_nothing_forbidden(client, monkeypatch):
    """ADR-086 clause 4 / SF-OPS.5 — the enforcement, not the prose.

    Every forbidden value is set to a recognisable sentinel and the whole
    serialised response is searched for it. A probe added in a later flavour that
    returns its diagnostic verbatim — an exception carrying a DSN, a
    PermissionError carrying a path, a provider error echoing a URL — fails HERE.
    """
    import applire.config as cfg

    sentinels = {
        "database_url": "postgresql+asyncpg://SENTINELUSER:SENTINELPW@sentinelhost:5432/db",
        "mistral_api_key": "SENTINELKEYMISTRAL",
        "openai_api_key": "SENTINELKEYOPENAI",
        "openrouter_api_key": "SENTINELKEYOPENROUTER",
        "requesty_api_key": "SENTINELKEYREQUESTY",
        "anthropic_api_key": "SENTINELKEYANTHROPIC",
        "upload_dir": "/sentinel/upload/path",
        "openrouter_base_url": "https://sentinel-base-url.example",
        "ollama_base_url": "http://sentinelhost:11434",
        "mistral_model": "SENTINELMODELNAME",
        "applire_base_url": "http://sentinelhost:8001",
    }
    for field, value in sentinels.items():
        monkeypatch.setattr(cfg.settings, field, value)

    payload = (await client.get("/api/ops/health")).json()
    body = json.dumps(payload)
    leaked = [value for value in sentinels.values() if value in body]
    assert leaked == [], f"the unauthenticated ops payload leaked: {leaked}"

    # The sentinel sweep above only catches a CONFIGURED value echoed verbatim.
    # A derived one slips through it — the disk probe's path resolves to an
    # existing ancestor of `upload_dir`, so leaking it would leak "/" and not
    # the sentinel. Found by mutating the probe to include its path (kill #4,
    # 2026-09-08): the sentinel assertion stayed green. Hence the shape check.
    shaped = list(_shaped_like_a_path_or_url(payload))
    assert shaped == [], f"the ops payload carries a path or URL: {shaped}"


def _shaped_like_a_path_or_url(node, trail: str = "$"):
    """Yield (json path, value) for anything that reads as a filesystem path or URL."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _shaped_like_a_path_or_url(value, f"{trail}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _shaped_like_a_path_or_url(value, f"{trail}[{index}]")
    elif isinstance(node, str):
        if "://" in node or node.startswith("/") or node.startswith("\\\\"):
            yield (trail, node)


@pytest.mark.asyncio
async def test_the_payload_does_name_the_provider_family_and_version(client, monkeypatch):
    """The other half of clause 4: what it MAY say, so the boundary is a line."""
    import applire.config as cfg

    monkeypatch.setattr(cfg.settings, "llm_provider", "openrouter")
    body = (await client.get("/api/ops/health")).json()
    assert body["llm_provider"] == "openrouter"
    assert body["version"]
    assert body["edition"] in ("community", "cloud")


@pytest.mark.asyncio
async def test_the_endpoint_never_triggers_a_provider_call(client, monkeypatch):
    """SF-OPS.6 — an unauthenticated caller cannot spend the operator's credit."""
    import applire.providers.llm as llm

    calls = {"n": 0}

    class _Counting:
        async def acomplete(self, *_a, **_k):
            calls["n"] += 1
            return "pong"

    monkeypatch.setattr(llm, "get_provider", lambda: _Counting())
    probes.reset_provider_cache()

    for _ in range(4):
        await client.get("/api/ops/health")
    # The first request fills the cold cache; every later one is free. In
    # production the background refresher has already filled it before anyone
    # can call, so the steady state is zero.
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_health_summary_is_cached_and_never_computes(client):
    """/health may not depend on Postgres — it is the container healthcheck."""
    aggregate.reset_state()
    assert aggregate.cached_summary() is None  # cold cache -> null field
    await client.get("/api/ops/health")
    summary = aggregate.cached_summary()
    assert set(summary) == {"status", "since"}
    assert summary["status"] in ("ok", "degraded", "down")


@pytest.mark.asyncio
async def test_a_verdict_change_logs_a_warning(client, monkeypatch, caplog):
    """ADR-086 clause 9 — the push half, for an operator who reads the log."""
    import logging

    await client.get("/api/ops/health")  # first verdict: degraded, no transition

    async def _dead(_db):
        return probes.ProbeResult("database", probes.DOWN, "database unreachable")

    monkeypatch.setattr(probes, "probe_database", _dead)
    with caplog.at_level(logging.WARNING, logger="applire.services.ops.aggregate"):
        await client.get("/api/ops/health")
    assert any(
        "instance health" in record.message or "instance health" in record.getMessage()
        for record in caplog.records
    )
