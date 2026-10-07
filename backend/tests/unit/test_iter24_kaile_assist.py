"""Consolidated Sprint 10 backend tests (task 24.9).

Covers:
  (a) POST /assist — question generated; 422 on invalid gap_id
  (b) PATCH /assist — suggestion returned; 422 on invalid session_id
  (c) Gap auto-resolve — resolved_gaps returned when keyword present; not returned when absent
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from applire.auth import get_auth_provider

from applire.auth.no_auth import NoAuthProvider
from applire.db.session import get_db
from applire.routers.cv import router

_CV_ID = str(uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"))


async def _stub_db():
    yield None


@pytest.fixture()
def client():
    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    app.dependency_overrides[get_db] = _stub_db
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# (a) POST /assist
# ---------------------------------------------------------------------------


def test_post_assist_question_generated(client):
    from applire.schemas.cv_sections import AssistStartResponse

    with patch(
        "applire.routers.cv.start_assist_session",
        new_callable=AsyncMock,
        return_value=AssistStartResponse(
            session_id="s1", question="Wie lange nutzen Sie Python?"
        ),
    ):
        r = client.post(
            f"/api/cv/{_CV_ID}/sections/introduction/assist",
            json={"gap_id": "Python"},
        )
    assert r.status_code == 200
    assert r.json()["question"] == "Wie lange nutzen Sie Python?"


def test_post_assist_422_on_invalid_gap_id(client):
    with patch(
        "applire.routers.cv.start_assist_session",
        new_callable=AsyncMock,
        side_effect=ValueError("gap_id not found"),
    ):
        r = client.post(
            f"/api/cv/{_CV_ID}/sections/introduction/assist",
            json={"gap_id": "NonExistentGap"},
        )
    assert r.status_code == 422
    assert "gap_id" in str(r.json().get("detail", "")).lower()


# ---------------------------------------------------------------------------
# (b) PATCH /assist
# ---------------------------------------------------------------------------


def test_patch_assist_suggestion_returned(client):
    from applire.schemas.cv_sections import AssistAnswerResponse

    with patch(
        "applire.routers.cv.submit_assist_answer",
        new_callable=AsyncMock,
        return_value=AssistAnswerResponse(
            suggestion="Erfahrener Python-Entwickler mit 5 Jahren."
        ),
    ):
        r = client.patch(
            f"/api/cv/{_CV_ID}/sections/introduction/assist",
            json={"session_id": "s1", "answer": "5 Jahre"},
        )
    assert r.status_code == 200
    assert r.json()["suggestion"] == "Erfahrener Python-Entwickler mit 5 Jahren."


def test_patch_assist_422_on_invalid_session_id(client):
    with patch(
        "applire.routers.cv.submit_assist_answer",
        new_callable=AsyncMock,
        side_effect=ValueError("Invalid session_id"),
    ):
        r = client.patch(
            f"/api/cv/{_CV_ID}/sections/introduction/assist",
            json={"session_id": "bad-id", "answer": "anything"},
        )
    assert r.status_code == 422
    assert "session" in str(r.json().get("detail", "")).lower()


# ---------------------------------------------------------------------------
# (c) Gap auto-resolve
# ---------------------------------------------------------------------------


def test_patch_section_resolved_gaps_returned_when_keyword_present(client):
    from applire.schemas.cv_sections import SectionPatchResponse

    with patch(
        "applire.routers.cv.patch_cv_section",
        new_callable=AsyncMock,
        return_value=SectionPatchResponse(
            html="<html/>",
            overrides_applied=["introduction"],
            resolved_gaps=["Python"],
        ),
    ):
        r = client.patch(
            f"/api/cv/{_CV_ID}/sections/introduction",
            json={"content": "Python developer", "save_to_profile": False},
        )
    assert r.status_code == 200
    assert "Python" in r.json()["resolved_gaps"]


def test_patch_section_resolved_gaps_empty_when_keyword_absent(client):
    from applire.schemas.cv_sections import SectionPatchResponse

    with patch(
        "applire.routers.cv.patch_cv_section",
        new_callable=AsyncMock,
        return_value=SectionPatchResponse(
            html="<html/>",
            overrides_applied=["introduction"],
            resolved_gaps=[],
        ),
    ):
        r = client.patch(
            f"/api/cv/{_CV_ID}/sections/introduction",
            json={"content": "No matching keywords here", "save_to_profile": False},
        )
    assert r.status_code == 200
    assert r.json()["resolved_gaps"] == []
