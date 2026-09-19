# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Flow endpoints resolve a flow for its owner only (Nougat final, ruling E-1).

GET /api/flow/{id}/state and POST /api/flow/{id}/advance resolved the auth
dependency but never compared ``flow.user_id`` against the authenticated
caller — unlike routers/profile.py and routers/job.py, whose IDOR-scoped
lookups (``get_import_job``, ``get_gap_job``) answer a foreign id exactly like
an unknown one (404, never a distinguishing error). These tests drive the real
FastAPI app (in-memory sqlite, full router + service stack, no mocking of the
endpoint under test) with two distinct authenticated users against one flow.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.main import app as fastapi_app
from applire.models.flow import FlowSession
from applire.models.user import User


def _auth_as(user_id: uuid.UUID) -> MagicMock:
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=user_id))
    return auth


@pytest.fixture(autouse=True)
def _restore_auth_override():
    """`applire.main.app` is a module-level singleton shared across the test
    process; async_client only overrides get_db, so the per-test auth override
    set below must be popped or it leaks into whatever test runs next."""
    yield
    fastapi_app.dependency_overrides.pop(get_auth_provider, None)


async def _make_flow(db: AsyncSession, *, owner_id: uuid.UUID) -> FlowSession:
    db.add(User(id=owner_id, email=f"{owner_id}@example.com"))
    flow = FlowSession(
        user_id=owner_id,
        job_id=None,
        current_step="jd_analysis",
        user_type="new",
        available_actions={"next": "cv_import"},
    )
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return flow


@pytest.mark.asyncio
async def test_get_flow_state_as_other_user_returns_404(
    async_client: AsyncClient, async_db: AsyncSession,
):
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    flow = await _make_flow(async_db, owner_id=owner_id)

    fastapi_app.dependency_overrides[get_auth_provider] = lambda: _auth_as(other_id)
    resp = await async_client.get(f"/api/flow/{flow.id}/state")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_flow_state_as_owner_returns_200(
    async_client: AsyncClient, async_db: AsyncSession,
):
    owner_id = uuid.uuid4()
    flow = await _make_flow(async_db, owner_id=owner_id)

    fastapi_app.dependency_overrides[get_auth_provider] = lambda: _auth_as(owner_id)
    resp = await async_client.get(f"/api/flow/{flow.id}/state")

    assert resp.status_code == 200
    assert resp.json()["flow_id"] == str(flow.id)


@pytest.mark.asyncio
async def test_advance_flow_as_other_user_returns_404(
    async_client: AsyncClient, async_db: AsyncSession,
):
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    flow = await _make_flow(async_db, owner_id=owner_id)

    fastapi_app.dependency_overrides[get_auth_provider] = lambda: _auth_as(other_id)
    resp = await async_client.post(
        f"/api/flow/{flow.id}/advance", json={"step": "cv_import"}
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_advance_flow_as_owner_returns_200(
    async_client: AsyncClient, async_db: AsyncSession,
):
    owner_id = uuid.uuid4()
    flow = await _make_flow(async_db, owner_id=owner_id)

    fastapi_app.dependency_overrides[get_auth_provider] = lambda: _auth_as(owner_id)
    resp = await async_client.post(
        f"/api/flow/{flow.id}/advance", json={"step": "cv_import"}
    )

    assert resp.status_code == 200
    assert resp.json()["current_step"] == "cv_import"
