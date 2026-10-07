# backend/tests/unit/test_iter24_assist_routes.py
"""Unit tests for POST/PATCH assist routes (task 24.1, 24.2)."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from applire.auth import get_auth_provider

from applire.auth.no_auth import NoAuthProvider
from applire.db.session import get_db
from applire.routers.cv import router

_CV_ID = str(uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"))


async def _stub_db():
    yield None


@pytest.fixture()
def client():
    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    app.dependency_overrides[get_db] = _stub_db
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


def test_post_assist_returns_200_with_session_and_question(client):
    from applire.schemas.cv_sections import AssistStartResponse

    mock_response = AssistStartResponse(
        session_id="sess-123",
        question="Wie lange nutzen Sie Python?",
    )
    with patch(
        "applire.routers.cv.start_assist_session",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = client.post(
            f"/api/cv/{_CV_ID}/sections/introduction/assist",
            json={"gap_id": "Python"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == "sess-123"
    assert "Python" in data["question"]


def test_post_assist_returns_422_on_invalid_gap_id(client):
    with patch(
        "applire.routers.cv.start_assist_session",
        new_callable=AsyncMock,
        side_effect=ValueError("gap_id not found"),
    ):
        response = client.post(
            f"/api/cv/{_CV_ID}/sections/introduction/assist",
            json={"gap_id": "InvalidGap"},
        )

    assert response.status_code == 422


def test_post_assist_returns_404_on_missing_cv(client):
    with patch(
        "applire.routers.cv.start_assist_session",
        new_callable=AsyncMock,
        side_effect=LookupError("CV not found"),
    ):
        response = client.post(
            f"/api/cv/{_CV_ID}/sections/introduction/assist",
            json={"gap_id": "Python"},
        )

    assert response.status_code == 404


def test_patch_assist_returns_200_with_suggestion(client):
    from applire.schemas.cv_sections import AssistAnswerResponse

    mock_response = AssistAnswerResponse(
        suggestion="Erfahrener Python-Entwickler mit 5 Jahren Erfahrung."
    )
    with patch(
        "applire.routers.cv.submit_assist_answer",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = client.patch(
            f"/api/cv/{_CV_ID}/sections/introduction/assist",
            json={"session_id": "sess-123", "answer": "5 Jahre"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "Python" in data["suggestion"]


def test_patch_assist_returns_422_on_invalid_session_id(client):
    with patch(
        "applire.routers.cv.submit_assist_answer",
        new_callable=AsyncMock,
        side_effect=ValueError("Invalid session_id"),
    ):
        response = client.patch(
            f"/api/cv/{_CV_ID}/sections/introduction/assist",
            json={"session_id": "bad-id", "answer": "5 Jahre"},
        )

    assert response.status_code == 422
