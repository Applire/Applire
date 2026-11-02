# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Endpoint tests for POST /api/profile/roles."""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.profile import MasterProfile
from applire.schemas.profile import MasterProfileData, ProfileMetadata, WorkEntry


@pytest.mark.asyncio
async def test_add_role_minimal(seed_profile, async_client: AsyncClient):
    profile = MasterProfileData(metadata=ProfileMetadata())
    await seed_profile(profile)

    res = await async_client.post(
        "/api/profile/roles",
        json={
            "title": "Director of QA",
            "company": "Varena",
            "start_date": "2026-06-01",
            "close_roles": [],
            "source": "manual",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["new_role_id"]
    assert body["closed_role_ids"] == []
    assert 0.0 <= body["completeness_score"] <= 1.0


@pytest.mark.asyncio
async def test_add_role_closes_existing(
    seed_profile, async_client: AsyncClient, async_db: AsyncSession
):
    existing = WorkEntry(company="A", role="Lead", start_date="2023-01-01", end_date=None)
    await seed_profile(MasterProfileData(
        work_experience=[existing],
        metadata=ProfileMetadata(),
    ))

    res = await async_client.post(
        "/api/profile/roles",
        json={
            "title": "Director",
            "company": "B",
            "start_date": "2026-06-01",
            "close_roles": [{"role_id": existing.id, "end_date": "2026-05-31"}],
            "source": "manual",
        },
    )
    assert res.status_code == 200
    assert res.json()["closed_role_ids"] == [existing.id]

    # Verify the end_date was actually persisted to the DB, not just reflected
    # in the response.
    result = await async_db.execute(select(MasterProfile))
    record = result.scalars().first()
    persisted = MasterProfileData.model_validate(record.profile_json)
    closed = next(w for w in persisted.work_experience if w.id == existing.id)
    assert closed.end_date == "2026-05-31"


@pytest.mark.asyncio
async def test_add_role_422_on_unknown_close_id(seed_profile, async_client: AsyncClient):
    await seed_profile(MasterProfileData(metadata=ProfileMetadata()))
    res = await async_client.post(
        "/api/profile/roles",
        json={
            "title": "Director", "company": "B", "start_date": "2026-06-01",
            "close_roles": [{"role_id": "does-not-exist", "end_date": "2026-05-31"}],
            "source": "manual",
        },
    )
    assert res.status_code == 422
