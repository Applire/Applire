# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""E042 / US236 — POST /api/cv/generate + MCP generate_cv gain optional
target_pages (ADR-051 §1). No Docker, no LLM.
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.auth import get_auth_provider
from applire.auth.no_auth import NoAuthProvider
from applire.db.session import get_db

from tests.support.profile_factory import make_master_profile


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_cv_generate_request_target_pages_defaults_to_none():
    from applire.schemas.cv import CVGenerateRequest

    req = CVGenerateRequest(job_id=uuid.uuid4())
    assert req.target_pages is None


def test_cv_generate_request_accepts_target_pages():
    from applire.schemas.cv import CVGenerateRequest

    req = CVGenerateRequest(job_id=uuid.uuid4(), target_pages=3)
    assert req.target_pages == 3


def test_cv_generate_request_rejects_zero_target_pages():
    from applire.schemas.cv import CVGenerateRequest

    with pytest.raises(ValidationError):
        CVGenerateRequest(job_id=uuid.uuid4(), target_pages=0)


def test_cv_generate_request_rejects_negative_target_pages():
    from applire.schemas.cv import CVGenerateRequest

    with pytest.raises(ValidationError):
        CVGenerateRequest(job_id=uuid.uuid4(), target_pages=-2)


# ---------------------------------------------------------------------------
# #379 — target_pages upper bound (MAX_TARGET_PAGES). The floor (>= 1) was
# already validated; the ceiling was not — a captured real run with
# target_pages=999 produced per-role bullet budgets of "max 1002 bullet(s)",
# making every downstream bullet-budget cap inert.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [11, 999])
def test_cv_generate_request_rejects_target_pages_above_max(bad):
    from applire.schemas.cv import CVGenerateRequest

    with pytest.raises(ValidationError):
        CVGenerateRequest(job_id=uuid.uuid4(), target_pages=bad)


@pytest.mark.parametrize("ok", [1, 2, 5, 10])
def test_cv_generate_request_accepts_target_pages_up_to_max(ok):
    from applire.schemas.cv import CVGenerateRequest

    req = CVGenerateRequest(job_id=uuid.uuid4(), target_pages=ok)
    assert req.target_pages == ok


def test_cv_generate_request_accepts_none_target_pages():
    from applire.schemas.cv import CVGenerateRequest

    req = CVGenerateRequest(job_id=uuid.uuid4(), target_pages=None)
    assert req.target_pages is None


# ---------------------------------------------------------------------------
# Router pass-through: POST /api/cv/generate
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def router_db():
    from applire.db.session import Base
    import applire.models.user
    import applire.models.job
    import applire.models.profile
    import applire.models.gap
    import applire.models.cv
    import applire.models.cover_letter
    import applire.models.session
    import applire.models.application
    import applire.models.flow
    import applire.models.uploads
    import applire.models.color_profile
    import applire.models.company
    import applire.models.user_settings

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def cv_client(router_db):
    """FastAPI test client for the cv router with mocked LLM provider dependency."""
    from applire.routers.cv import router as cv_router, _get_provider

    mock_provider = AsyncMock()

    _app = FastAPI()
    _app.include_router(cv_router)
    _app.dependency_overrides[get_db] = lambda: router_db
    _app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    _app.dependency_overrides[_get_provider] = lambda: mock_provider

    transport = ASGITransport(app=_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, router_db


async def _seed_job_and_profile(db):
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile

    job_id = uuid.uuid4()
    db.add(JobAnalysis(
        id=job_id,
        raw_text_hash=f"hash-{job_id}",
        raw_text="Sample job description",
        role_title="Backend Engineer",
        company_name="DataCraft GmbH",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
    ))
    db.add(make_master_profile(
        id=uuid.uuid4(),
        profile_json={"personal_info": {"full_name": "Emma Beispiel"}},
    ))
    await db.commit()
    return job_id


@pytest.mark.asyncio
async def test_post_generate_persists_target_pages_override(cv_client):
    from applire.models.cv import GeneratedCV

    client, db = cv_client
    job_id = await _seed_job_and_profile(db)

    with patch("applire.services.cv._render_cv_background", AsyncMock()):
        resp = await client.post(
            "/api/cv/generate", json={"job_id": str(job_id), "target_pages": 3}
        )
    assert resp.status_code == 201
    cv_id = uuid.UUID(resp.json()["cv_id"])

    record = await db.get(GeneratedCV, cv_id)
    assert record.target_pages == 3


@pytest.mark.asyncio
async def test_post_generate_without_target_pages_uses_dach_standard(cv_client):
    from applire.models.cv import GeneratedCV

    client, db = cv_client
    job_id = await _seed_job_and_profile(db)

    with patch("applire.services.cv._render_cv_background", AsyncMock()):
        resp = await client.post("/api/cv/generate", json={"job_id": str(job_id)})
    assert resp.status_code == 201
    cv_id = uuid.UUID(resp.json()["cv_id"])

    record = await db.get(GeneratedCV, cv_id)
    assert record.target_pages == 2


@pytest.mark.asyncio
async def test_post_generate_rejects_zero_target_pages(cv_client):
    client, db = cv_client
    job_id = await _seed_job_and_profile(db)

    resp = await client.post(
        "/api/cv/generate", json={"job_id": str(job_id), "target_pages": 0}
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# MCP tool pass-through: generate_cv(job_id, target_pages=...)
# ---------------------------------------------------------------------------


def _mock_db():
    mock_session = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, mock_session


def _mock_result(**kwargs) -> MagicMock:
    m = MagicMock()
    m.model_dump.return_value = kwargs
    return m


@pytest.mark.asyncio
async def test_mcp_generate_cv_forwards_target_pages():
    from applire.mcp.server import generate_cv

    job_id = str(uuid.uuid4())
    cv_id = uuid.uuid4()
    cm, _ = _mock_db()
    mock_result = _mock_result(
        cv_id=str(cv_id),
        html_url=f"http://localhost:8001/api/cv/{cv_id}/html",
        pdf_url=f"http://localhost:8001/api/cv/{cv_id}/pdf",
    )
    mock_generate = AsyncMock(return_value=mock_result)

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.cv_svc.generate_cv", mock_generate),
    ):
        result = await generate_cv(job_id=job_id, target_pages=3)

    assert "cv_id" in result
    _, kwargs = mock_generate.call_args
    assert kwargs.get("target_pages") == 3


@pytest.mark.asyncio
async def test_mcp_generate_cv_defaults_target_pages_to_none():
    from applire.mcp.server import generate_cv

    job_id = str(uuid.uuid4())
    cv_id = uuid.uuid4()
    cm, _ = _mock_db()
    mock_result = _mock_result(
        cv_id=str(cv_id),
        html_url=f"http://localhost:8001/api/cv/{cv_id}/html",
        pdf_url=f"http://localhost:8001/api/cv/{cv_id}/pdf",
    )
    mock_generate = AsyncMock(return_value=mock_result)

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.cv_svc.generate_cv", mock_generate),
    ):
        await generate_cv(job_id=job_id)

    _, kwargs = mock_generate.call_args
    assert kwargs.get("target_pages") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_target", [0, -5])
async def test_mcp_generate_cv_rejects_target_pages_below_one(bad_target):
    """MCP mirrors the REST Field(ge=1) validation (Task 1.1 review Important)."""
    from mcp.shared.exceptions import McpError

    from applire.mcp.server import generate_cv

    job_id = str(uuid.uuid4())
    mock_generate = AsyncMock()

    with (
        patch("applire.mcp.server.get_db"),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.cv_svc.generate_cv", mock_generate),
    ):
        with pytest.raises(McpError, match="target_pages must be between 1 and"):
            await generate_cv(job_id=job_id, target_pages=bad_target)

    mock_generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_target", [11, 999])
async def test_mcp_generate_cv_rejects_target_pages_above_max(bad_target):
    """#379: the floor was validated (>= 1), the ceiling was not — an unbounded
    override fed straight into the per-role bullet-budget math and produced inert
    "max 1002 bullet(s)" ceilings on a captured target_pages=999 run."""
    from mcp.shared.exceptions import McpError

    from applire.constants import MAX_TARGET_PAGES
    from applire.mcp.server import generate_cv

    job_id = str(uuid.uuid4())
    mock_generate = AsyncMock()

    with (
        patch("applire.mcp.server.get_db"),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.cv_svc.generate_cv", mock_generate),
    ):
        with pytest.raises(McpError, match=f"target_pages must be between 1 and {MAX_TARGET_PAGES}"):
            await generate_cv(job_id=job_id, target_pages=bad_target)

    mock_generate.assert_not_awaited()
