# backend/tests/unit/test_cv_assist_service.py
"""Unit tests for the cv_assist service (Sprint 10, task 24.1 / 24.2)."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.support.owners import HARNESS_USER_ID

_CV_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_SECTION_ID = "introduction"
_GAP_ID = "Python"


@pytest.fixture()
def mock_db():
    return MagicMock()


@pytest.fixture()
def mock_provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="Wie lange nutzen Sie Python?")
    return provider


@pytest.fixture()
def mock_cv_record():
    record = MagicMock()
    record.content_snapshot = {
        "introduction": "Erfahrener Entwickler",
        "positions": [],
        "skills": ["Java"],
    }
    return record


@pytest.mark.asyncio
async def test_start_assist_session_returns_session_id_and_question(
    mock_db, mock_provider, mock_cv_record
):
    from applire.services.cv_assist import start_assist_session, _sessions
    _sessions.clear()

    with patch(
        "applire.services.cv_assist._load_cv_and_section",
        new_callable=AsyncMock,
        return_value=("Introduction", "Erfahrener Entwickler"),
    ), patch(
        "applire.services.cv_assist._gap_exists",
        new_callable=AsyncMock,
        return_value=True,
    ):
        result = await start_assist_session(
            _CV_ID, _SECTION_ID, _GAP_ID, mock_provider, mock_db
        )

    assert result.question == "Wie lange nutzen Sie Python?"
    assert result.session_id in _sessions
    _sessions.clear()


@pytest.mark.asyncio
async def test_start_assist_session_raises_value_error_on_unknown_gap(
    mock_db, mock_provider, mock_cv_record
):
    from applire.services.cv_assist import start_assist_session, _sessions
    _sessions.clear()

    with patch(
        "applire.services.cv_assist._load_cv_and_section",
        new_callable=AsyncMock,
        return_value=("Introduction", "Erfahrener Entwickler"),
    ), patch(
        "applire.services.cv_assist._gap_exists",
        new_callable=AsyncMock,
        return_value=False,
    ):
        with pytest.raises(ValueError, match="gap_id"):
            await start_assist_session(
                _CV_ID, _SECTION_ID, _GAP_ID, mock_provider, mock_db
            )
    _sessions.clear()


@pytest.mark.asyncio
async def test_submit_assist_answer_returns_suggestion(mock_db, mock_provider):
    from applire.services.cv_assist import _sessions, submit_assist_answer
    _sessions.clear()

    session_id = "test-session-abc"
    _sessions[session_id] = {
        "cv_id": str(_CV_ID),
        "section_id": _SECTION_ID,
        "gap_id": _GAP_ID,
        "section_label": "Introduction",
        "section_content": "Erfahrener Entwickler",
        "question": "Wie lange?",
        "user_id": str(HARNESS_USER_ID),  # ADR-092: the session carries its owner
    }
    mock_provider.acomplete = AsyncMock(
        return_value="Erfahrener Python-Entwickler mit 5 Jahren Erfahrung."
    )

    # ADR-092: the CV is looked up owner-keyed first; the (mock) row is owned.
    with patch("applire.services.cv_assist.owned_cv", new_callable=AsyncMock):
        result = await submit_assist_answer(
            _CV_ID, _SECTION_ID, session_id, "5 Jahre", mock_provider, mock_db
        )

    assert "Python" in result.suggestion
    _sessions.clear()


@pytest.mark.asyncio
async def test_submit_assist_answer_raises_on_invalid_session(mock_db, mock_provider):
    from applire.services.cv_assist import _sessions, submit_assist_answer
    _sessions.clear()

    with patch("applire.services.cv_assist.owned_cv", new_callable=AsyncMock), \
            pytest.raises(ValueError, match="session_id"):
        await submit_assist_answer(
            _CV_ID, _SECTION_ID, "nonexistent-id", "answer", mock_provider, mock_db
        )
