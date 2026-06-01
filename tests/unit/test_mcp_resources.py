"""
Iteration 7 — MCP Server: resource tests (unit)

Resource handlers are plain async functions and can be called directly.
The DB and services are mocked — no Docker or real DB required.

Run:
    pytest tests/unit/test_iter7_mcp_resources.py -v
"""
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.exceptions import McpError


# ---------------------------------------------------------------------------
# Helpers
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


# ---------------------------------------------------------------------------
# profile://current
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resource_profile_happy_path():
    from applire.mcp.server import resource_profile

    cm, _ = _mock_db()
    mock_result = _mock_result(id=str(uuid.uuid4()), completeness=75)

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server.profile_svc.get_profile",
            AsyncMock(return_value=mock_result),
        ),
    ):
        raw = await resource_profile()

    data = json.loads(raw)
    assert data["completeness"] == 75


@pytest.mark.asyncio
async def test_resource_profile_not_found_raises():
    from applire.mcp.server import resource_profile

    cm, _ = _mock_db()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server.profile_svc.get_profile",
            AsyncMock(return_value=None),
        ),
    ):
        with pytest.raises(McpError) as exc_info:
            await resource_profile()

    assert exc_info.value.error.code == -32001


# ---------------------------------------------------------------------------
# job://{job_id}
# ---------------------------------------------------------------------------


def _make_job_orm_mock(job_id: uuid.UUID) -> MagicMock:
    record = MagicMock()
    record.id = job_id
    record.raw_text_hash = "abc"
    record.raw_text = "some jd text"
    record.role_title = "Software Engineer"
    record.required_skills = ["Python"]
    record.nice_to_have_skills = []
    record.keywords = ["backend"]
    record.seniority_level = "senior"
    record.company_culture_signals = []
    record.language_requirement = "German"
    record.created_at = datetime.now(timezone.utc)
    record.deleted_at = None
    return record


@pytest.mark.asyncio
async def test_resource_job_happy_path():
    from applire.mcp.server import resource_job

    job_id = uuid.uuid4()
    cm, mock_session = _mock_db()

    # Mock the db.execute → scalars chain
    mock_scalar_result = MagicMock()
    mock_scalar_result.scalar_one_or_none.return_value = _make_job_orm_mock(job_id)
    mock_session.execute = AsyncMock(return_value=mock_scalar_result)

    mock_response = _mock_result(id=str(job_id), role_title="Software Engineer")

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.JobAnalysisResponse") as mock_schema,
    ):
        mock_schema.model_validate.return_value = mock_response
        raw = await resource_job(job_id=str(job_id))

    data = json.loads(raw)
    assert data["role_title"] == "Software Engineer"


@pytest.mark.asyncio
async def test_resource_job_not_found_raises():
    from applire.mcp.server import resource_job

    job_id = uuid.uuid4()
    cm, mock_session = _mock_db()

    mock_scalar_result = MagicMock()
    mock_scalar_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_scalar_result)

    with patch("applire.mcp.server.get_db", return_value=cm):
        with pytest.raises(McpError) as exc_info:
            await resource_job(job_id=str(job_id))

    assert exc_info.value.error.code == -32001


@pytest.mark.asyncio
async def test_resource_job_invalid_uuid_raises():
    from applire.mcp.server import resource_job

    with pytest.raises(McpError) as exc_info:
        await resource_job(job_id="not-a-uuid")

    assert exc_info.value.error.code == -32602


# ---------------------------------------------------------------------------
# cv://{cv_id}
# ---------------------------------------------------------------------------


def _make_cv_orm_mock(cv_id: uuid.UUID) -> MagicMock:
    record = MagicMock()
    record.id = cv_id
    record.job_analysis_id = uuid.uuid4()
    record.profile_id = uuid.uuid4()
    record.created_at = datetime.now(timezone.utc)
    record.expires_at = datetime.now(timezone.utc)
    record.deleted_at = None
    return record


@pytest.mark.asyncio
async def test_resource_cv_happy_path():
    from applire.mcp.server import resource_cv

    cv_id = uuid.uuid4()
    cm, mock_session = _mock_db()

    mock_scalar_result = MagicMock()
    mock_scalar_result.scalar_one_or_none.return_value = _make_cv_orm_mock(cv_id)
    mock_session.execute = AsyncMock(return_value=mock_scalar_result)

    mock_response = _mock_result(id=str(cv_id), job_analysis_id=str(uuid.uuid4()))

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.GeneratedCVResponse") as mock_schema,
    ):
        mock_schema.model_validate.return_value = mock_response
        raw = await resource_cv(cv_id=str(cv_id))

    data = json.loads(raw)
    assert data["id"] == str(cv_id)


@pytest.mark.asyncio
async def test_resource_cv_not_found_raises():
    from applire.mcp.server import resource_cv

    cv_id = uuid.uuid4()
    cm, mock_session = _mock_db()

    mock_scalar_result = MagicMock()
    mock_scalar_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_scalar_result)

    with patch("applire.mcp.server.get_db", return_value=cm):
        with pytest.raises(McpError) as exc_info:
            await resource_cv(cv_id=str(cv_id))

    assert exc_info.value.error.code == -32001


@pytest.mark.asyncio
async def test_resource_cv_invalid_uuid_raises():
    from applire.mcp.server import resource_cv

    with pytest.raises(McpError) as exc_info:
        await resource_cv(cv_id="not-a-uuid")

    assert exc_info.value.error.code == -32602


# ---------------------------------------------------------------------------
# flow://{flow_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resource_flow_returns_json():
    import json
    from applire.mcp.server import resource_flow

    cm, _ = _mock_db()
    mock_result = _mock_result(flow_id=str(uuid.uuid4()), current_step="gap_analysis")
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.flow_svc.get_flow_state", AsyncMock(return_value=mock_result)),
    ):
        out = await resource_flow(flow_id=str(uuid.uuid4()))
    assert json.loads(out)["current_step"] == "gap_analysis"
