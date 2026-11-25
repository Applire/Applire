# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A request's database session gives its connection back when the endpoint
has answered, before the response's background tasks run (adv/plans re-probe
N3).

FastAPI closes a request-scoped ``yield`` dependency only after the response
has been sent, and a response's background tasks run inside that send. A
request that read something after its last commit therefore kept its
connection "idle in transaction" for the whole background task: twelve
regenerations of one person held twelve of the pool's fifteen connections for
the minutes their renders took (and while those renders waited for a render
slot), and other people's requests timed out at the pool with a 500.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, Depends, FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def engine(tmp_path, monkeypatch):
    from sqlalchemy.pool import AsyncAdaptedQueuePool

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}", poolclass=AsyncAdaptedQueuePool)
    monkeypatch.setattr("applire.db.session.AsyncSessionLocal", async_sessionmaker(eng, expire_on_commit=False))
    yield eng
    await eng.dispose()


def _app(engine, seen):
    from applire.db.session import get_db

    app = FastAPI()

    async def background():
        seen.append(("background", engine.pool.checkedout()))

    @app.post("/work")
    async def work(tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
        await db.execute(text("select 1"))  # a read after the last commit: a transaction is open
        seen.append(("endpoint", engine.pool.checkedout()))
        tasks.add_task(background)
        return {"ok": True}

    @app.post("/commit-then-read")
    async def commit_then_read(tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
        await db.execute(text("create table if not exists t (n int)"))
        await db.execute(text("insert into t values (1)"))
        await db.commit()
        rows = (await db.execute(text("select count(*) from t"))).scalar_one()
        tasks.add_task(background)
        return {"rows": rows}

    return app


@pytest.mark.asyncio
async def test_the_request_connection_is_back_before_the_background_task_runs(engine):
    seen: list = []
    async with AsyncClient(transport=ASGITransport(app=_app(engine, seen)), base_url="http://t") as client:
        r = await client.post("/work")
    assert r.status_code == 200
    assert seen == [("endpoint", 1), ("background", 0)]


@pytest.mark.asyncio
async def test_a_committed_write_and_the_answer_are_unchanged(engine):
    seen: list = []
    async with AsyncClient(transport=ASGITransport(app=_app(engine, seen)), base_url="http://t") as client:
        r = await client.post("/commit-then-read")
        assert r.json() == {"rows": 1}
        r = await client.post("/commit-then-read")
        assert r.json() == {"rows": 2}  # the first request's write was committed
    assert seen == [("background", 0), ("background", 0)]


# ── the framework seam this relies on (adv/plans re-probe 2, S1) ──────────────


@pytest.mark.asyncio
async def test_the_installed_fastapi_gives_each_request_its_endpoint_exit_stack():
    """The release above hangs on FastAPI's ``fastapi_function_astack`` scope
    key. If an upgrade renames or drops it, this test is the one that fails."""
    app = FastAPI()
    seen: list = []

    @app.get("/scope")
    async def scope(request: Request):
        seen.append("fastapi_function_astack" in request.scope)
        return {}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        assert (await client.get("/scope")).status_code == 200
    assert seen == [True]


@pytest.mark.asyncio
async def test_a_request_without_the_endpoint_exit_stack_is_logged_once_per_process(engine, monkeypatch, caplog):
    """Without the key the session is still served (closed at the request's
    end, after background tasks, as before): the operator sees one WARNING
    naming it, not one per request and not silence."""
    import logging

    from applire.db import session as db_session

    monkeypatch.setattr(db_session, "_missing_stack_warned", False)
    caplog.set_level(logging.WARNING)
    for _ in range(3):
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        gen = db_session.get_db(request)
        session = await gen.__anext__()
        assert session is not None
        await gen.aclose()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "fastapi_function_astack" in r.getMessage()]
    assert len(warnings) == 1
    gen = db_session.get_db(None)  # a caller outside a request: nothing to warn about
    await gen.__anext__()
    await gen.aclose()
    assert len([r for r in caplog.records if "fastapi_function_astack" in r.getMessage()]) == 1
