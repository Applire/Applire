# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A letter that has been pending or generating for longer than any render can
take is shown as failed when it is polled, like a CV (``get_cv_status``'s
inline check). adv/plans re-probe 2, R2-1: a letter whose render stopped
while the database was down stayed ``generating`` for 25 minutes — letters
had no inline check, only the retention service's stale-job loop."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


async def _letter(async_db, status, minutes_ago):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job = JobAnalysis(
        raw_text_hash=f"hash-{uuid.uuid4()}", raw_text="x", role_title="Engineer",
        seniority_level="mid", language_requirement="English",
    )
    profile = make_master_profile(profile_json={"personal_info": {}, "work_experience": []})
    async_db.add_all([job, profile])
    await async_db.flush()
    letter = GeneratedCoverLetter(
        job_analysis_id=job.id, profile_id=profile.id, status=status,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )
    async_db.add(letter)
    await async_db.commit()
    return letter


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "generating"])
async def test_a_letter_unfinished_past_the_bound_is_shown_failed_with_the_retry(async_db, status):
    from applire.services.cover_letter import get_cover_letter_status
    from tests.support.owners import HARNESS_USER_ID

    letter = await _letter(async_db, status, minutes_ago=11)
    out = await get_cover_letter_status(letter.id, async_db, "http://x", user_id=HARNESS_USER_ID)
    assert out.status == "failed"
    assert out.error_message  # the generic "generate it again"
    assert out.html_url is None and out.pdf_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status,minutes_ago,expected", [
    ("generating", 2, "generating"),  # still within the bound: still running
    ("pending", 9, "pending"),
    ("ready", 60, "ready"),  # a finished letter is never turned
])
async def test_a_letter_within_the_bound_or_finished_keeps_its_status(async_db, status, minutes_ago, expected):
    from applire.services.cover_letter import get_cover_letter_status
    from tests.support.owners import HARNESS_USER_ID

    letter = await _letter(async_db, status, minutes_ago=minutes_ago)
    out = await get_cover_letter_status(letter.id, async_db, "http://x", user_id=HARNESS_USER_ID)
    assert out.status == expected
