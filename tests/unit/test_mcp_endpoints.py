"""Sprint 7 — Iteration 21: MCP tool unit tests (Tasks 21.14)

Tests the two new MCP tools added in sprint 7:
  - list_applications  — list pipeline, optional status_filter
  - get_application    — single application by ID

Each tool handler is a plain async function and can be called directly
without going through the MCP protocol. Services and DB are mocked.

Run:
    pytest tests/unit/test_iter21_sprint7_mcp.py -v
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.shared.exceptions import McpError


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_iter7_mcp_tools.py)
# ---------------------------------------------------------------------------


def _mock_db():
    """Return a context-manager mock for applire.mcp.deps.get_db."""
    mock_session = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, mock_session


def _mock_result(**kwargs) -> MagicMock:
    """Build a Pydantic-like result mock whose model_dump(mode='json') returns kwargs."""
    m = MagicMock()
    m.model_dump.return_value = kwargs
    return m


def _mock_app(**kwargs) -> MagicMock:
    """Build a mock ApplicationResponse whose model_dump returns kwargs (caller overrides defaults)."""
    defaults = {
        "id": str(uuid.uuid4()),
        "role_title": "Backend Engineer",
        "company_name": "Acme GmbH",
        "workflow_status": "none",
        "user_status": "tracking",
    }
    return _mock_result(**{**defaults, **kwargs})


# ---------------------------------------------------------------------------
# list_applications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_applications_happy_path():
    """Returns a list of dicts when user exists and applications are found."""
    from applire.mcp.server import list_applications

    app1 = _mock_app(role_title="Backend Engineer")
    app2 = _mock_app(role_title="Frontend Engineer")

    mock_list_result = MagicMock()
    mock_list_result.items = [app1, app2]

    cm, mock_session = _mock_db()

    # Mock the User query — scalar_one_or_none returns a user with an id
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = mock_user
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server.app_svc.list_applications",
            AsyncMock(return_value=mock_list_result),
        ),
    ):
        result = await list_applications()

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["role_title"] == "Backend Engineer"
    assert result[1]["role_title"] == "Frontend Engineer"


@pytest.mark.asyncio
async def test_list_applications_passes_status_filter_to_service():
    """A valid status_filter is converted to UserStatus and forwarded to the service."""
    from applire.mcp.server import list_applications
    from applire.models.application import UserStatus

    mock_list_result = MagicMock()
    mock_list_result.items = []

    cm, mock_session = _mock_db()
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = mock_user
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return mock_list_result

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.app_svc.list_applications", side_effect=_capture),
    ):
        await list_applications(status_filter="applied")

    assert captured.get("user_status") == UserStatus("applied")


@pytest.mark.asyncio
async def test_list_applications_invalid_status_filter_raises():
    """An unrecognised status_filter raises McpError with invalid-input code (-32602)."""
    from applire.mcp.server import list_applications

    with pytest.raises(McpError) as exc_info:
        await list_applications(status_filter="nonexistent_status")

    assert exc_info.value.error.code == -32602


@pytest.mark.asyncio
async def test_list_applications_no_user_raises_not_found():
    """When no User row exists in the DB, McpError with not-found code (-32001) is raised."""
    from applire.mcp.server import list_applications

    cm, mock_session = _mock_db()
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none.return_value = None  # no user
    mock_session.execute = AsyncMock(return_value=mock_execute_result)

    with patch("applire.mcp.server.get_db", return_value=cm):
        with pytest.raises(McpError) as exc_info:
            await list_applications()

    assert exc_info.value.error.code == -32001


# ---------------------------------------------------------------------------
# get_application
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_application_happy_path():
    """Returns a dict with application fields when the ID is valid and found."""
    from applire.mcp.server import get_application

    app_id = uuid.uuid4()
    mock_app = _mock_app(id=str(app_id), role_title="Data Engineer")
    cm, _ = _mock_db()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch(
            "applire.mcp.server.app_svc.get_application",
            AsyncMock(return_value=mock_app),
        ),
    ):
        result = await get_application(application_id=str(app_id))

    assert result["role_title"] == "Data Engineer"
    assert result["id"] == str(app_id)


@pytest.mark.asyncio
async def test_get_application_invalid_uuid_raises():
    """A non-UUID application_id raises McpError with invalid-input code (-32602)."""
    from applire.mcp.server import get_application

    with pytest.raises(McpError) as exc_info:
        await get_application(application_id="not-a-uuid")

    assert exc_info.value.error.code == -32602


@pytest.mark.asyncio
async def test_get_application_not_found_raises():
    """LookupError from the service layer raises McpError with not-found code (-32001)."""
    from applire.mcp.server import get_application

    cm, _ = _mock_db()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch(
            "applire.mcp.server.app_svc.get_application",
            AsyncMock(side_effect=LookupError("Application not found")),
        ),
    ):
        with pytest.raises(McpError) as exc_info:
            await get_application(application_id=str(uuid.uuid4()))

    assert exc_info.value.error.code == -32001
