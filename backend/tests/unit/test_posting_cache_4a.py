# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared immutable posting cache (ADR-092 cl. 5, S-17, RD-2, MD-10; US334; Strawberry 4a)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from applire.models.application import Application
from applire.models.job import JobAnalysis
from applire.ownership import OwnedNotFound
from applire.services.job import ensure_application_link, get_job_for_user


async def _job(db, **kw) -> JobAnalysis:
    j = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Engineer, Python.",
        role_title=kw.pop("role_title", "Engineer"),
        company_name=kw.pop("company_name", "Acme"),
        language_requirement="English",
        **kw,
    )
    db.add(j)
    await db.flush()
    return j


@pytest.mark.asyncio
async def test_get_job_for_user_needs_a_link(async_db, two_users):
    a, b = two_users
    job = await _job(async_db)
    with pytest.raises(OwnedNotFound):
        await get_job_for_user(async_db, job.id, a.id)
    await ensure_application_link(async_db, job, a.id)
    assert (await get_job_for_user(async_db, job.id, a.id)).id == job.id
    with pytest.raises(OwnedNotFound) as exc:
        await get_job_for_user(async_db, job.id, b.id)
    assert exc.value.status_code == 404 and exc.value.detail == "job not found"


@pytest.mark.asyncio
async def test_get_job_for_user_soft_deleted_link_still_counts(async_db, two_users):
    from datetime import datetime, timezone

    a, _ = two_users
    job = await _job(async_db)
    app = await ensure_application_link(async_db, job, a.id)
    app.deleted_at = datetime.now(timezone.utc)
    await async_db.flush()
    assert (await get_job_for_user(async_db, job.id, a.id)).id == job.id


@pytest.mark.asyncio
async def test_get_job_for_user_missing_and_deleted_posting_are_404(async_db, two_users):
    from datetime import datetime, timezone

    a, _ = two_users
    with pytest.raises(OwnedNotFound):
        await get_job_for_user(async_db, uuid.uuid4(), a.id)
    job = await _job(async_db)
    await ensure_application_link(async_db, job, a.id)
    job.deleted_at = datetime.now(timezone.utc)
    await async_db.flush()
    with pytest.raises(OwnedNotFound):
        await get_job_for_user(async_db, job.id, a.id)


@pytest.mark.asyncio
async def test_ensure_link_is_get_or_create_and_overrides_stay_private(async_db, two_users):
    a, b = two_users
    job = await _job(async_db, role_title="Engineer", company_name="Acme")
    app_a = await ensure_application_link(async_db, job, a.id, role_title_override="  Staff Engineer ")
    again = await ensure_application_link(async_db, job, a.id)
    assert again.id == app_a.id
    assert app_a.role_title == "Staff Engineer" and app_a.company_name == "Acme"
    assert app_a.user_status == "tracking"
    app_b = await ensure_application_link(async_db, job, b.id, company_name_override="Acme GmbH")
    assert app_b.id != app_a.id
    assert (app_b.role_title, app_b.company_name) == ("Engineer", "Acme GmbH")
    await async_db.refresh(job)
    assert (job.role_title, job.company_name) == ("Engineer", "Acme"), "shared posting untouched"
    n = await async_db.scalar(select(func.count()).select_from(Application))
    assert n == 2


@pytest.mark.asyncio
async def test_ensure_link_blank_override_keeps_value(async_db, two_users):
    a, _ = two_users
    job = await _job(async_db)
    await ensure_application_link(async_db, job, a.id, role_title_override="Lead")
    app = await ensure_application_link(async_db, job, a.id, role_title_override="   ")
    assert app.role_title == "Lead"
