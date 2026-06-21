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

"""Unit tests for GET /api/profile/uploads endpoint."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from applire.auth import get_auth_provider
from applire.db.session import get_db
from applire.routers.profile import router

_USER_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_NOW = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)


class _MockAuth:
    async def get_current_user(self, request):
        user = MagicMock()
        user.id = _USER_ID
        return user


def _make_upload_record(
    filename: str,
    mime: str = "application/pdf",
    *,
    gate_status: str | None = None,
    staged_extraction: dict | None = None,
) -> MagicMock:
    r = MagicMock()
    r.id = uuid.uuid4()
    r.original_filename = filename
    r.mime_type = mime
    r.byte_size = 102400
    r.created_at = _NOW
    r.gate_status = gate_status
    r.staged_extraction = staged_extraction
    return r


@pytest.fixture()
def client():
    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: _MockAuth()
    app.include_router(router)
    return app


def test_get_uploads_returns_200_with_records(client):
    record = _make_upload_record("Lebenslauf.pdf")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [record]
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    async def _stub_db():
        yield mock_db

    client.dependency_overrides[get_db] = _stub_db
    tc = TestClient(client, raise_server_exceptions=True)
    resp = tc.get("/api/profile/uploads")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["original_filename"] == "Lebenslauf.pdf"
    assert data[0]["completeness_score"] is None


def test_get_uploads_exposes_open_gate_and_staged_name(client):
    """US167: parked gated uploads expose gate_status + the staged CV name so the
    frontend can badge them and re-open the resolve dialog."""
    record = _make_upload_record(
        "Markus_Brandt_CV.pdf",
        gate_status="name_divergence",
        staged_extraction={"personal_info": {"name": "Markus Brandt"}},
    )
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [record]
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    async def _stub_db():
        yield mock_db

    client.dependency_overrides[get_db] = _stub_db
    tc = TestClient(client, raise_server_exceptions=True)
    resp = tc.get("/api/profile/uploads")

    assert resp.status_code == 200
    item = resp.json()[0]
    assert item["gate_status"] == "name_divergence"
    assert item["staged_name"] == "Markus Brandt"


def test_get_uploads_clean_record_has_no_gate(client):
    """A normally-merged upload reports no open gate."""
    record = _make_upload_record("clean.pdf")
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [record]
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    async def _stub_db():
        yield mock_db

    client.dependency_overrides[get_db] = _stub_db
    tc = TestClient(client, raise_server_exceptions=True)
    resp = tc.get("/api/profile/uploads")

    assert resp.status_code == 200
    item = resp.json()[0]
    assert item["gate_status"] is None
    assert item["staged_name"] is None


def test_get_uploads_returns_empty_list_when_no_records(client):
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    async def _stub_db():
        yield mock_db

    client.dependency_overrides[get_db] = _stub_db
    tc = TestClient(client, raise_server_exceptions=True)
    resp = tc.get("/api/profile/uploads")

    assert resp.status_code == 200
    assert resp.json() == []


def test_get_uploads_returns_at_most_10_records(client):
    records = [_make_upload_record(f"cv_{i}.pdf") for i in range(12)]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = records[:10]
    mock_db = AsyncMock()
    mock_db.execute.return_value = mock_result

    async def _stub_db():
        yield mock_db

    client.dependency_overrides[get_db] = _stub_db
    tc = TestClient(client, raise_server_exceptions=True)
    resp = tc.get("/api/profile/uploads")

    assert resp.status_code == 200
    assert len(resp.json()) == 10
