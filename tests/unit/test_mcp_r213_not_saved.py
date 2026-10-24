# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""R2-13 (P-1) on the agent channel: a failed reconcile says "not saved".

`send_message`, `resolve_gap` and `import_cv` each map `ReconcileFailedError`
(the engine failed after its retry, nothing merged) to an internal error that
tells the agent the answer / import was NOT saved and to retry — never a
follow-up question as if the answer held nothing. `LLMResponseParseError` is
also a `ValueError` (it subclasses `json.JSONDecodeError`), so each door gets
an arm for it ahead of the `ValueError → invalid_input` arm: an unreadable
model reply is not the agent's bad input.

Driven through `mcp.call_tool` — FastMCP drops `McpError.data`, so the message
text is what reaches the agent.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.exceptions import LLMResponseParseError, ReconcileFailedError
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse

_ANSWER_TEXT = "Your answer was not saved (the profile update failed) — resend the same message to retry."
_IMPORT_TEXT = "The import was not saved — nothing was stored; retry the import."
_UNREADABLE_TEXT = "The AI provider's reply could not be read — retry the same call."


def _db_cm():
    session = AsyncMock()
    user = MagicMock()
    user.id = uuid.uuid4()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    session.execute = AsyncMock(return_value=result)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _call_error_text(tool: str, args: dict) -> str:
    from applire.mcp.server import mcp

    with pytest.raises(Exception) as info:
        await mcp.call_tool(tool, args)
    exc = info.value
    error = getattr(exc, "error", None)
    return getattr(error, "message", None) or str(exc)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raised, expected",
    [(ReconcileFailedError("reconcile failed after retry"), _ANSWER_TEXT),
     (LLMResponseParseError("Requesty reply holds a second JSON value", "{}{}", 2), _UNREADABLE_TEXT)],
    ids=["reconcile-failed", "reply-unreadable"],
)
async def test_seam_send_message(raised, expected):
    with (
        patch("applire.mcp.server.get_db", return_value=_db_cm()),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server._owned", AsyncMock()),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch("applire.mcp.server.session_svc.send_message", AsyncMock(side_effect=raised)),
    ):
        text = await _call_error_text("send_message", {"session_id": str(uuid.uuid4()), "message": "I led the Kafka migration"})
    assert expected in text
    assert "invalid" not in text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raised, expected",
    [(ReconcileFailedError("reconcile failed after retry"), _ANSWER_TEXT),
     (LLMResponseParseError("Requesty reply holds a second JSON value", "{}{}", 2), _UNREADABLE_TEXT)],
    ids=["reconcile-failed", "reply-unreadable"],
)
async def test_seam_resolve_gap(raised, expected):
    from applire.mcp import server

    created = MagicMock()
    created.session_id = uuid.uuid4()
    with (
        patch.object(server, "get_db", return_value=_db_cm()),
        patch.object(server, "get_provider"),
        patch.object(server, "_owned_job", AsyncMock()),
        patch.object(server, "_current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch.object(server.session_svc, "gap_cluster_ids", AsyncMock(return_value=["cluster-k8s"])),
        patch.object(server.session_svc, "active_full_interview_exists", AsyncMock(return_value=False)),
        patch.object(server.session_svc, "last_recorded_answer", AsyncMock(return_value=None)),
        patch.object(server.session_svc, "create_session", AsyncMock(return_value=created)),
        patch.object(server.session_svc, "send_message", AsyncMock(side_effect=raised)),
    ):
        text = await _call_error_text(
            "resolve_gap",
            {"job_id": str(uuid.uuid4()), "gap_id": "cluster-k8s", "answer": "I ran our EKS clusters for two years"},
        )
    assert expected in text
    assert "invalid" not in text.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["text", "pdf"])
@pytest.mark.parametrize(
    "raised, expected",
    [(ReconcileFailedError("reconcile failed after retry"), _IMPORT_TEXT),
     (LLMResponseParseError("Requesty reply holds a second JSON value", "{}{}", 2), _UNREADABLE_TEXT)],
    ids=["reconcile-failed", "reply-unreadable"],
)
async def test_seam_import_cv(door, raised, expected):
    import base64

    from applire.mcp import server

    target = "import_from_text" if door == "text" else "import_from_pdf"
    args = (
        {"text": "Felix Example — Backend engineer, Kafka, EKS"}
        if door == "text"
        else {"file_base64": base64.b64encode(b"%PDF-1.4 synthetic").decode(), "filename": "cv.pdf"}
    )
    with (
        patch.object(server, "get_db", return_value=_db_cm()),
        patch.object(server, "get_provider"),
        patch.object(server, "get_storage"),
        patch.object(server, "_current_user_id", AsyncMock(return_value=uuid.uuid4())),
        patch.object(server.profile_svc, target, AsyncMock(side_effect=raised)),
    ):
        text = await _call_error_text("import_cv", args)
    assert expected in text
    assert "invalid" not in text.lower()
