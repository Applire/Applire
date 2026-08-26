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

"""ADR-077 clause 6 — MCP fact-pin parity on update_application.

Hard ADR-058 parity: `add_fact_pin` / `remove_fact_pin` land on the SAME tool
call as the REST subresource's semantics (additive, fail-closed, idempotent),
or neither door ships.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.schemas.application import AddFactPinRequest


def _mock_db():
    session = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, session


def _app_response():
    resp = MagicMock()
    resp.model_dump.return_value = {"id": "x", "pinned_facts": []}
    return resp


@pytest.mark.asyncio
async def test_add_fact_pin_param_reaches_the_pin_service():
    from applire.mcp.server import update_application

    cm, _session = _mock_db()
    app_id = str(uuid.uuid4())
    entry_id = str(uuid.uuid4())
    uid = uuid.uuid4()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uid)),
        patch(
            "applire.mcp.server.pin_svc.add_fact_pin", AsyncMock()
        ) as add_mock,
        patch(
            "applire.mcp.server.app_svc.get_application",
            AsyncMock(return_value=_app_response()),
        ),
    ):
        result = await update_application(
            application_id=app_id,
            add_fact_pin={
                "entry_type": "skill",
                "entry_id": entry_id,
                "quote": "Kubernetes",
            },
        )

    assert add_mock.await_count == 1
    req = add_mock.await_args.args[2]
    assert isinstance(req, AddFactPinRequest)
    assert req.entry_type == "skill" and req.quote == "Kubernetes"
    assert result["pinned_facts"] == []


@pytest.mark.asyncio
async def test_remove_fact_pin_param_reaches_the_pin_service():
    from applire.mcp.server import update_application

    cm, _session = _mock_db()
    app_id = str(uuid.uuid4())
    uid = uuid.uuid4()

    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uid)),
        patch(
            "applire.mcp.server.pin_svc.remove_fact_pin", AsyncMock()
        ) as remove_mock,
        patch(
            "applire.mcp.server.app_svc.get_application",
            AsyncMock(return_value=_app_response()),
        ),
    ):
        await update_application(application_id=app_id, remove_fact_pin="pin-123")

    assert remove_mock.await_count == 1
    assert remove_mock.await_args.args[2] == "pin-123"


@pytest.mark.asyncio
async def test_a_pin_op_alone_is_a_valid_call():
    """A pin add without any patch field must not trip 'at least one field'."""
    from applire.mcp.server import update_application
    from mcp.shared.exceptions import McpError

    cm, _session = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server._current_user_id",
            AsyncMock(return_value=uuid.uuid4()),
        ),
        patch("applire.mcp.server.pin_svc.remove_fact_pin", AsyncMock()),
        patch(
            "applire.mcp.server.app_svc.get_application",
            AsyncMock(return_value=_app_response()),
        ),
    ):
        result = await update_application(
            application_id=str(uuid.uuid4()), remove_fact_pin="p1"
        )
    assert result is not None


@pytest.mark.asyncio
async def test_malformed_add_fact_pin_is_invalid_input():
    from applire.mcp.server import update_application
    from mcp.shared.exceptions import McpError

    cm, _session = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server._current_user_id",
            AsyncMock(return_value=uuid.uuid4()),
        ),
    ):
        with pytest.raises(McpError):
            await update_application(
                application_id=str(uuid.uuid4()),
                add_fact_pin={"quote": ""},  # missing entry_type/entry_id
            )


@pytest.mark.asyncio
async def test_a_cv_target_on_a_volunteer_pin_is_invalid_input_on_the_agent_door():
    """ADR-058 parity for the #580 renderability gate: the REAL service gate runs
    (nothing mocked below it) and its ValueError reaches the agent as
    invalid_input — before the database is touched."""
    from applire.mcp.server import update_application
    from mcp.shared.exceptions import McpError

    cm, _session = _mock_db()
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server._current_user_id",
            AsyncMock(return_value=uuid.uuid4()),
        ),
    ):
        with pytest.raises(McpError) as excinfo:
            await update_application(
                application_id=str(uuid.uuid4()),
                add_fact_pin={
                    "entry_type": "publication",
                    "entry_id": "p-1",
                    "quote": "q",
                    "targets": ["cv"],
                },
            )
    assert "CV has no section" in str(excinfo.value)


def test_tool_description_no_longer_says_submitted_pins():
    """Three pin vocabularies must not share one docstring (ADR-077 cl.6)."""
    import applire.mcp.server as srv
    import inspect

    source = inspect.getsource(srv)
    assert "submitted pins" not in source
