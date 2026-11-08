# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial pass, Hazelnut build 7 (A-7) — ONE retry verdict on every door, read through the
protocol layer (`mcp.call_tool`), not the tool function (memory:
FastMCP drops McpError.data — prove payload keys where the agent reads them).
"""
from __future__ import annotations

import json
import pathlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401


def _mock_db():
    session = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, session


def _payload(output) -> dict:
    """Whatever shape call_tool returns, find the JSON object the agent reads."""
    if isinstance(output, tuple) and len(output) == 2 and isinstance(output[1], dict):
        structured = output[1]
        return structured.get("result", structured) if set(structured) == {"result"} else structured
    blocks = output[0] if isinstance(output, tuple) else output
    for b in blocks:
        text = getattr(b, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"no JSON content in {output!r}")



from datetime import datetime, timedelta, timezone

from applire.exceptions import (
    LLMError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMTruncatedError,
)
from applire.internal_errors import _retry_prefix
from applire.services.cv import classify_generation_error, generation_error_message
from applire.services.generation_errors import is_retryable, letter_error_code, letter_failure_message


def _five_xx():
    return LLMProviderError("openai", sdk_type="InternalServerError", status_code=503)


TRANSIENT = [
    pytest.param(lambda: LLMRateLimitError("429 after retries"), id="429"),
    pytest.param(_five_xx, id="unclassified-5xx"),
    pytest.param(lambda: LLMTimeoutError("timed out after 180s"), id="timeout"),
    pytest.param(lambda: LLMTruncatedError("cut off"), id="truncated"),
]
NOT_TRANSIENT = [
    pytest.param(lambda: LLMProviderError("openai", sdk_type="AuthenticationError", status_code=401), id="401"),
    pytest.param(lambda: LLMProviderError("openai", sdk_type="NotFoundError", status_code=404), id="404"),
    pytest.param(lambda: ValueError("bug"), id="plain-bug"),
]


@pytest.mark.parametrize("make", TRANSIENT)
def test_every_transient_error_is_retryable_on_all_three_doors(make):
    exc = make()
    assert _retry_prefix(exc) != ""
    assert is_retryable(classify_generation_error(exc))
    assert is_retryable(letter_error_code(letter_failure_message(exc)))


@pytest.mark.parametrize("make", NOT_TRANSIENT)
def test_a_configuration_error_or_bug_is_retryable_on_no_door_negative_arm(make):
    exc = make()
    assert _retry_prefix(exc) == ""
    assert not is_retryable(classify_generation_error(exc))
    assert not is_retryable(letter_error_code(letter_failure_message(exc)))


def test_the_codes_of_the_timeout_split_are_unchanged():
    assert classify_generation_error(LLMTimeoutError("t")) == "llm_timeout"
    assert classify_generation_error(LLMRateLimitError("r")) == "provider_unavailable"
    assert classify_generation_error(_five_xx()) == "provider_unavailable"
    assert classify_generation_error(ValueError("x")) == "generation_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("make", TRANSIENT[:2])
async def test_cover_letter_status_door_says_retryable_for_a_failed_429_or_5xx(make):
    from applire.mcp import server
    from applire.mcp.server import mcp
    from applire.schemas.cover_letter import CoverLetterStatusResponse

    cm, _ = _mock_db()
    status = CoverLetterStatusResponse(
        cover_letter_id=uuid.uuid4(), status="failed",
        error_message=letter_failure_message(make()),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    with (
        patch.object(server, "get_db", return_value=cm),
        patch.object(server.cover_letter_svc, "get_cover_letter_status", AsyncMock(return_value=status)),
    ):
        out = await mcp.call_tool("get_cover_letter_status", {"cover_letter_id": str(status.cover_letter_id)})
    payload = _payload(out)
    assert payload.get("error_code") == "provider_unavailable" and payload.get("retryable") is True, payload


@pytest.mark.asyncio
@pytest.mark.parametrize("make", TRANSIENT[:2])
async def test_cv_status_door_says_retryable_for_a_failed_429_or_5xx(make):
    from applire.mcp import server
    from applire.mcp.server import mcp
    from applire.schemas.cv import CVStatusResponse

    cm, _ = _mock_db()
    code = classify_generation_error(make())
    status = CVStatusResponse(
        cv_id=uuid.uuid4(), status="failed", error_code=code,
        error_message=generation_error_message(code),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    with (
        patch.object(server, "get_db", return_value=cm),
        patch.object(server, "_owned", AsyncMock(return_value=MagicMock())),
        patch.object(server.cv_svc, "get_cv_status", AsyncMock(return_value=status)),
    ):
        out = await mcp.call_tool("get_cv_status", {"cv_id": str(status.cv_id)})
    payload = _payload(out)
    assert payload.get("error_code") == "provider_unavailable" and payload.get("retryable") is True, payload


@pytest.mark.asyncio
async def test_a_generic_failure_stays_not_retryable_through_the_door_negative_arm():
    from applire.mcp import server
    from applire.mcp.server import mcp
    from applire.schemas.cv import CVStatusResponse

    cm, _ = _mock_db()
    status = CVStatusResponse(
        cv_id=uuid.uuid4(), status="failed", error_code=classify_generation_error(ValueError("x")),
        error_message="x", expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    with (
        patch.object(server, "get_db", return_value=cm),
        patch.object(server, "_owned", AsyncMock(return_value=MagicMock())),
        patch.object(server.cv_svc, "get_cv_status", AsyncMock(return_value=status)),
    ):
        out = await mcp.call_tool("get_cv_status", {"cv_id": str(status.cv_id)})
    payload = _payload(out)
    assert payload.get("error_code") == "generation_failed" and payload.get("retryable") is False, payload


def test_the_guide_names_the_unified_verdict():
    import pathlib

    guide = (pathlib.Path(server_path()) ).read_text()
    assert "rate limit" in guide and "provider_rate_limited (safe to retry" in guide


def server_path():
    from applire.mcp import server

    return pathlib.Path(server.__file__).with_name("AGENT_GUIDE.md")
