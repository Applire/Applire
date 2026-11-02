# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chained endpoint tests for the post-hire flow.

Exercises: mark application as hired → add new role (with previous-role close)
→ verify both the application status and the persisted profile state. Uses
the SQLite-backed async_client fixture (no Docker).
"""
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.application import Application, UserStatus
from applire.models.profile import MasterProfile
from applire.schemas.profile import (
    MasterProfileData,
    ProfileMetadata,
    WorkEntry,
)


@pytest.mark.asyncio
async def test_mark_hired_then_add_role(
    seed_profile,
    seed_application,
    async_client: AsyncClient,
    async_db: AsyncSession,
):
    """T1 path: mark application as hired, then add the new role with
    auto-close of the previous role."""
    # Seed: profile with one open role + one applied application
    existing = WorkEntry(
        company="Acme",
        role="Lead",
        start_date="2023-01-01",
        end_date=None,
    )
    await seed_profile(MasterProfileData(
        work_experience=[existing],
        metadata=ProfileMetadata(),
    ))
    app_row = await seed_application(user_status=UserStatus.applied.value)

    # 1. Mark as hired
    res1 = await async_client.post(
        f"/api/applications/{app_row.id}/mark-hired"
    )
    assert res1.status_code == 200
    body1 = res1.json()
    assert body1["user_status"] == "hired"
    assert "action=add-role" in body1["redirect_url"]
    assert f"application_id={app_row.id}" in body1["redirect_url"]

    # 2. Add new role with previous-role close
    res2 = await async_client.post(
        "/api/profile/roles",
        json={
            "title": "Director of QA",
            "company": "Varena",
            "start_date": "2026-06-01",
            "close_roles": [
                {"role_id": existing.id, "end_date": "2026-05-31"}
            ],
            "source": "application",
            "source_ref": str(app_row.id),
        },
    )
    assert res2.status_code == 200
    body2 = res2.json()
    new_role_id = body2["new_role_id"]
    assert existing.id in body2["closed_role_ids"]

    # 3. Verify persisted profile state
    result = await async_db.execute(select(MasterProfile))
    record = result.scalars().first()
    profile = MasterProfileData.model_validate(record.profile_json)
    new_role = next(w for w in profile.work_experience if w.id == new_role_id)
    old_role = next(w for w in profile.work_experience if w.id == existing.id)
    assert new_role.company == "Varena"
    assert new_role.end_date is None
    assert old_role.end_date == "2026-05-31"
    # The new role should be at the top of the work history
    assert profile.work_experience[0].id == new_role_id

    # 4. Verify application is hired in the DB
    result = await async_db.execute(
        select(Application).where(Application.id == app_row.id)
    )
    app_after = result.scalar_one()
    assert app_after.user_status == "hired"


@pytest.mark.asyncio
async def test_side_role_keeps_existing_open(
    seed_profile,
    async_client: AsyncClient,
    async_db: AsyncSession,
):
    """Side-role case: add a new parallel role without closing any existing one."""
    open_role = WorkEntry(
        company="Acme",
        role="Day Job",
        start_date="2023-01-01",
        end_date=None,
    )
    await seed_profile(MasterProfileData(
        work_experience=[open_role],
        metadata=ProfileMetadata(),
    ))

    res = await async_client.post(
        "/api/profile/roles",
        json={
            "title": "Founder",
            "company": "MyStartup",
            "start_date": "2026-06-01",
            "close_roles": [],  # parallel — nothing closes
            "source": "manual",
        },
    )
    assert res.status_code == 200
    assert res.json()["closed_role_ids"] == []

    # Verify the existing role is still open in the DB
    result = await async_db.execute(select(MasterProfile))
    record = result.scalars().first()
    profile = MasterProfileData.model_validate(record.profile_json)
    still_open = next(w for w in profile.work_experience if w.id == open_role.id)
    assert still_open.end_date is None
    # The new role and the previous role both have end_date=None
    open_count = sum(1 for w in profile.work_experience if w.end_date is None)
    assert open_count == 2
