# backend/tests/unit/test_iter24_gap_resolve.py
"""Unit tests for gap auto-resolve on PATCH (task 24.3)."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from applire.auth import get_auth_provider

from applire.auth.no_auth import NoAuthProvider
from applire.db.session import get_db
from applire.routers.cv import router

_CV_ID = str(uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"))


async def _stub_db():
    yield None


@pytest.fixture()
def client():
    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    app.dependency_overrides[get_db] = _stub_db
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=True)


def test_patch_section_returns_resolved_gaps_when_keyword_present(client):
    from applire.schemas.cv_sections import SectionPatchResponse

    mock_response = SectionPatchResponse(
        html="<html/>",
        overrides_applied=["introduction"],
        resolved_gaps=["Python"],
    )
    with patch(
        "applire.routers.cv.patch_cv_section",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = client.patch(
            f"/api/cv/{_CV_ID}/sections/introduction",
            json={"content": "Python developer with 5 years", "save_to_profile": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert "Python" in data["resolved_gaps"]


def test_patch_section_returns_empty_resolved_gaps_when_keyword_absent(client):
    from applire.schemas.cv_sections import SectionPatchResponse

    mock_response = SectionPatchResponse(
        html="<html/>",
        overrides_applied=["introduction"],
        resolved_gaps=[],
    )
    with patch(
        "applire.routers.cv.patch_cv_section",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        response = client.patch(
            f"/api/cv/{_CV_ID}/sections/introduction",
            json={"content": "Generic content with no gap keywords", "save_to_profile": False},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["resolved_gaps"] == []
