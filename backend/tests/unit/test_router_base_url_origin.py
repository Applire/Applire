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

"""#232 — REST document endpoints must build pdf_url/html_url from
``settings.applire_base_url`` (the operator-configured external origin),
never from ``request.base_url``.

Behind a reverse proxy on a non-80/443 port, ``request.base_url`` reflects
whatever Host header the proxy forwards — which drops the real port and
points agents/UIs at the wrong origin (confident false 404s). The MCP server
already gets this right (applire/mcp/server.py uses settings.applire_base_url
everywhere); this test locks the REST routers to the same source.

Each test sets an incoming Host that differs from ``settings.applire_base_url``
and asserts the emitted URL follows the *setting*, not the Host.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from applire.auth import get_auth_provider
from applire.auth.no_auth import NoAuthProvider
from applire.config import settings
from applire.db.session import get_db
from applire.models.cover_letter import CoverLetterStatus
from applire.models.cv import CVGenerationStatus
from applire.schemas.cover_letter import CoverLetterGenerateResponse, CoverLetterStatusResponse
from applire.schemas.cv import CVGenerateResponse
from applire.schemas.flow import CreateFlowResponse, FlowStateResponse

_CV_ID = uuid.uuid4()
_CL_ID = uuid.uuid4()
_FLOW_ID = uuid.uuid4()
_EXPIRES = datetime.now(timezone.utc) + timedelta(days=1)
_NOW = datetime.now(timezone.utc)

# The incoming request's Host — deliberately different from
# settings.applire_base_url, to prove the router does not derive the emitted
# URL from it.
_WRONG_HOST_ORIGIN = "http://wrong-host:9999"
_CONFIGURED_ORIGIN = "https://applire.example.com:8443"


async def _stub_db():
    yield None


@pytest.fixture()
def configured_base_url(monkeypatch):
    monkeypatch.setattr(settings, "applire_base_url", _CONFIGURED_ORIGIN)
    return _CONFIGURED_ORIGIN


def _client(router) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    app.dependency_overrides[get_db] = _stub_db
    app.include_router(router)
    return TestClient(app, base_url=_WRONG_HOST_ORIGIN, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# CV router (backend/applire/routers/cv.py)
# ---------------------------------------------------------------------------


def test_cv_post_generate_uses_configured_base_url(configured_base_url):
    from applire.routers import cv as cv_router_module

    client = _client(cv_router_module.router)
    fake_result = CVGenerateResponse(
        cv_id=_CV_ID,
        status=CVGenerationStatus.pending,
        html_url=f"{configured_base_url}/api/cv/{_CV_ID}/html",
        pdf_url=f"{configured_base_url}/api/cv/{_CV_ID}/pdf",
        expires_at=_EXPIRES,
    )
    with patch.object(cv_router_module, "generate_cv", AsyncMock(return_value=fake_result)) as mock_generate:
        resp = client.post("/api/cv/generate", json={"job_id": str(uuid.uuid4())})
    assert resp.status_code == 201
    called_base_url = mock_generate.call_args.args[5]
    assert called_base_url == configured_base_url
    assert _WRONG_HOST_ORIGIN not in called_base_url


def test_cv_get_status_uses_configured_base_url(configured_base_url):
    from applire.routers import cv as cv_router_module

    client = _client(cv_router_module.router)
    fake_result = cv_router_module.CVStatusResponse(
        cv_id=_CV_ID,
        status=CVGenerationStatus.ready,
        html_url=f"{configured_base_url}/api/cv/{_CV_ID}/html",
        pdf_url=f"{configured_base_url}/api/cv/{_CV_ID}/pdf",
        expires_at=_EXPIRES,
    )
    with patch.object(cv_router_module, "get_cv_status", AsyncMock(return_value=fake_result)) as mock_status:
        resp = client.get(f"/api/cv/{_CV_ID}/status")
    assert resp.status_code == 200
    called_base_url = mock_status.call_args.args[2]
    assert called_base_url == configured_base_url
    assert _WRONG_HOST_ORIGIN not in called_base_url


def test_cv_list_for_job_uses_configured_base_url(configured_base_url):
    from applire.routers import cv as cv_router_module

    client = _client(cv_router_module.router)
    with patch.object(cv_router_module, "list_cvs_for_job", AsyncMock(return_value=[])) as mock_list:
        resp = client.get(f"/api/cv?job_id={uuid.uuid4()}")
    assert resp.status_code == 200
    called_base_url = mock_list.call_args.args[2]
    assert called_base_url == configured_base_url
    assert _WRONG_HOST_ORIGIN not in called_base_url


# ---------------------------------------------------------------------------
# Cover letter router (backend/applire/routers/cover_letter.py)
# ---------------------------------------------------------------------------


def test_cover_letter_post_generate_uses_configured_base_url(configured_base_url):
    from applire.routers import cover_letter as cl_router_module

    client = _client(cl_router_module.router)
    fake_result = CoverLetterGenerateResponse(
        cover_letter_id=_CL_ID,
        status=CoverLetterStatus.pending,
        html_url=f"{configured_base_url}/api/cover-letter/{_CL_ID}/html",
        pdf_url=f"{configured_base_url}/api/cover-letter/{_CL_ID}/pdf",
        expires_at=_EXPIRES,
    )
    with patch.object(cl_router_module, "generate_cover_letter", AsyncMock(return_value=fake_result)) as mock_generate:
        resp = client.post("/api/cover-letter/generate", json={"job_id": str(uuid.uuid4())})
    assert resp.status_code == 201
    called_base_url = mock_generate.call_args.args[4]
    assert called_base_url == configured_base_url
    assert _WRONG_HOST_ORIGIN not in called_base_url


def test_cover_letter_get_by_job_uses_configured_base_url(configured_base_url):
    from applire.routers import cover_letter as cl_router_module

    client = _client(cl_router_module.router)
    fake_result = CoverLetterStatusResponse(
        cover_letter_id=_CL_ID,
        status=CoverLetterStatus.ready,
        html_url=f"{configured_base_url}/api/cover-letter/{_CL_ID}/html",
        pdf_url=f"{configured_base_url}/api/cover-letter/{_CL_ID}/pdf",
        expires_at=_EXPIRES,
    )
    with patch.object(cl_router_module, "get_cover_letter_by_job", AsyncMock(return_value=fake_result)) as mock_get:
        resp = client.get(f"/api/cover-letter/by-job/{uuid.uuid4()}")
    assert resp.status_code == 200
    called_base_url = mock_get.call_args.args[2]
    assert called_base_url == configured_base_url
    assert _WRONG_HOST_ORIGIN not in called_base_url


def test_cover_letter_get_status_uses_configured_base_url(configured_base_url):
    from applire.routers import cover_letter as cl_router_module

    client = _client(cl_router_module.router)
    fake_result = CoverLetterStatusResponse(
        cover_letter_id=_CL_ID,
        status=CoverLetterStatus.ready,
        html_url=f"{configured_base_url}/api/cover-letter/{_CL_ID}/html",
        pdf_url=f"{configured_base_url}/api/cover-letter/{_CL_ID}/pdf",
        expires_at=_EXPIRES,
    )
    with patch.object(cl_router_module, "get_cover_letter_status", AsyncMock(return_value=fake_result)) as mock_get:
        resp = client.get(f"/api/cover-letter/{_CL_ID}/status")
    assert resp.status_code == 200
    called_base_url = mock_get.call_args.args[2]
    assert called_base_url == configured_base_url
    assert _WRONG_HOST_ORIGIN not in called_base_url


# ---------------------------------------------------------------------------
# Flow router (backend/applire/routers/flow.py)
# ---------------------------------------------------------------------------


def test_flow_create_uses_configured_base_url(configured_base_url):
    from applire.routers import flow as flow_router_module

    client = _client(flow_router_module.router)
    fake_result = CreateFlowResponse(
        flow_id=_FLOW_ID,
        user_type="new",
        current_step="jd_analysis",
        available_actions={},
    )
    with patch.object(flow_router_module, "create_flow", AsyncMock(return_value=fake_result)) as mock_create:
        resp = client.post("/api/flow", json={"job_id": str(uuid.uuid4())})
    assert resp.status_code == 201
    called_base_url = mock_create.call_args.kwargs["base_url"]
    assert called_base_url == configured_base_url
    assert _WRONG_HOST_ORIGIN not in called_base_url


def test_flow_get_state_uses_configured_base_url(configured_base_url):
    from applire.routers import flow as flow_router_module

    client = _client(flow_router_module.router)
    fake_result = FlowStateResponse(
        flow_id=_FLOW_ID,
        user_type="new",
        current_step="jd_analysis",
        available_actions={},
        created_at=_NOW,
        updated_at=_NOW,
    )
    with patch.object(flow_router_module, "get_flow_state", AsyncMock(return_value=fake_result)) as mock_state:
        resp = client.get(f"/api/flow/{_FLOW_ID}/state")
    assert resp.status_code == 200
    called_base_url = mock_state.call_args.kwargs["base_url"]
    assert called_base_url == configured_base_url
    assert _WRONG_HOST_ORIGIN not in called_base_url


def test_flow_advance_uses_configured_base_url(configured_base_url):
    from applire.routers import flow as flow_router_module

    client = _client(flow_router_module.router)
    fake_result = FlowStateResponse(
        flow_id=_FLOW_ID,
        user_type="new",
        current_step="gap_analysis",
        available_actions={},
        created_at=_NOW,
        updated_at=_NOW,
    )
    with patch.object(flow_router_module, "advance_flow", AsyncMock(return_value=fake_result)) as mock_advance:
        resp = client.post(f"/api/flow/{_FLOW_ID}/advance", json={"step": "gap_analysis"})
    assert resp.status_code == 200
    called_base_url = mock_advance.call_args.kwargs["base_url"]
    assert called_base_url == configured_base_url
    assert _WRONG_HOST_ORIGIN not in called_base_url
