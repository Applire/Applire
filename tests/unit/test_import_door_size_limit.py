# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#789 L13 — the "10 MB" the upload surfaces promise is enforced on the REST doors.

The dropzone hint and the profile-import blurb say "up to 10 MB". Nothing in the REST
path enforced it (``MAX_CV_BYTES`` guarded only the MCP door; nginx caps at 20 M, which
is a proxy setting, absent behind Caddy/Traefik). The REST import doors now refuse a
larger file with a clean 413 and a stable error code the frontend translates, before any
job row is created or any LLM call is made. MCP and REST share ONE number.

No Docker, no real LLM.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.constants import MAX_CV_UPLOAD_BYTES
from applire.db.session import Base
from applire.models.import_job import CVImportJob

UID = uuid.uuid4()
OVER = b"x" * (MAX_CV_UPLOAD_BYTES + 1)
EXACT = b"x" * MAX_CV_UPLOAD_BYTES


@pytest.fixture(autouse=True)
def _act_as_uid():
    from applire.ownership import owner_context

    with owner_context(UID):
        yield


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[CVImportJob.__table__])
        )
    f = async_sessionmaker(engine, expire_on_commit=False)
    yield f
    await engine.dispose()


@pytest_asyncio.fixture
async def client(factory):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers import profile as profile_router

    app = FastAPI()
    app.include_router(profile_router.router)

    async def _get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[profile_router._get_provider] = lambda: MagicMock()
    app.dependency_overrides[profile_router._get_storage] = lambda: MagicMock()
    app.dependency_overrides[profile_router._get_ocr] = lambda: MagicMock()
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=UID))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _job_count(factory) -> int:
    async with factory() as db:
        return (await db.execute(select(func.count()).select_from(CVImportJob))).scalar_one()


def test_rest_and_mcp_share_one_limit_of_ten_megabytes():
    """The copy says 10 MB; MCP says 10 MB; REST enforces the same constant."""
    from applire.mcp.server import MAX_CV_BYTES

    assert MAX_CV_UPLOAD_BYTES == MAX_CV_BYTES == 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_import_jobs_door_refuses_a_file_over_the_limit_with_413(factory, client):
    with patch("applire.routers.profile.run_import_job_background", new=AsyncMock()) as bg:
        r = await client.post(
            "/api/profile/import-jobs", files={"file": ("big.pdf", OVER, "application/pdf")}
        )
    assert r.status_code == 413
    detail = r.json()["detail"]
    assert detail["error"] == "file_too_large"
    assert detail["max_bytes"] == MAX_CV_UPLOAD_BYTES
    bg.assert_not_called()
    assert await _job_count(factory) == 0  # refused before a job row exists


@pytest.mark.asyncio
async def test_import_jobs_door_accepts_a_file_of_exactly_the_limit(factory, client):
    with patch("applire.routers.profile.run_import_job_background", new=AsyncMock()):
        r = await client.post(
            "/api/profile/import-jobs", files={"file": ("ok.pdf", EXACT, "application/pdf")}
        )
    assert r.status_code == 202
    assert await _job_count(factory) == 1
