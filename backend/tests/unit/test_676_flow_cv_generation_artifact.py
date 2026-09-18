# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#676 line 35 — the REST door's seam for the CV id at `cv_generation`.

The behaviour twin runs the AGENT door (`tests/unit/test_676_flow_cv_generation
_artifact.py`). ADR-058 clause 2: the same act may not behave differently by
door, and `advance_flow` is one service behind two doors — so each door gets its
own named seam test and neither may be inferred from the other.

These drive the real FastAPI app (in-memory sqlite, full router + service stack,
no mocking) over POST /api/flow/{flow_id}/advance.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _job_and_cv(async_db: AsyncSession):
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job = JobAnalysis(
        raw_text_hash=f"hash-{uuid.uuid4()}",
        raw_text="Sample job description",
        role_title="Software Engineer",
        seniority_level="mid",
        language_requirement="English",
    )
    profile = make_master_profile(
        profile_json={"personal_info": {}, "work_experience": []}
    )
    async_db.add_all([job, profile])
    await async_db.flush()
    cv = GeneratedCV(job_analysis_id=job.id, profile_id=profile.id, tailored_data={})
    async_db.add(cv)
    await async_db.commit()
    await async_db.refresh(cv)
    return job, cv


@pytest.mark.asyncio
async def test_rest_door_records_the_cv_id_at_cv_generation(
    async_client: AsyncClient, async_db: AsyncSession,
):
    """The CV page posts `{step: "cv_generation", artifact_id: cvId}` — until
    #676 line 35 the id was dropped and `cv_summary` came back null."""
    job, cv = await _job_and_cv(async_db)

    create_resp = await async_client.post("/api/flow", json={"job_id": str(job.id)})
    assert create_resp.status_code == 201
    flow_id = create_resp.json()["flow_id"]

    # Control, same door: a REQUIRED step is unchanged by the split.
    advance = await async_client.post(
        f"/api/flow/{flow_id}/advance", json={"step": "interview"},
    )
    assert advance.status_code == 422, "interview still REQUIRES its artifact_id"

    # The CV route: jd_analysis -> gap_analysis -> cv_generation.
    from applire.models.gap import GapAnalysis

    gap = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=uuid.uuid4(),
        input_fingerprint=f"fp-{uuid.uuid4()}",
        match_score=0.5,
        critical_gaps=[], minor_gaps=[], strengths=[], keyword_gaps=[],
    )
    async_db.add(gap)
    await async_db.commit()
    await async_db.refresh(gap)
    r = await async_client.post(
        f"/api/flow/{flow_id}/advance",
        json={"step": "gap_analysis", "artifact_id": str(gap.id)},
    )
    assert r.status_code == 200

    r = await async_client.post(
        f"/api/flow/{flow_id}/advance",
        json={"step": "cv_generation", "artifact_id": str(cv.id)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["current_step"] == "cv_generation"
    assert body["cv_summary"] is not None
    assert body["cv_summary"]["cv_id"] == str(cv.id)
    assert body["notices"] == []


@pytest.mark.asyncio
async def test_rest_door_answers_an_unrecordable_id_with_a_notice(
    async_client: AsyncClient, async_db: AsyncSession,
):
    """`cv_import` records no artifact: 200 plus a notice, never a 422 and never
    a silent drop (ruling D-4)."""
    job, cv = await _job_and_cv(async_db)

    create_resp = await async_client.post("/api/flow", json={"job_id": str(job.id)})
    flow_id = create_resp.json()["flow_id"]

    r = await async_client.post(
        f"/api/flow/{flow_id}/advance",
        json={"step": "cv_import", "artifact_id": str(cv.id)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["current_step"] == "cv_import"
    assert len(body["notices"]) == 1
    assert "cv_import" in body["notices"][0]


@pytest.mark.asyncio
async def test_rest_door_still_advances_cv_generation_without_an_id(
    async_client: AsyncClient, async_db: AsyncSession,
):
    """The gaps page's own button posts no artifact_id — unchanged."""
    from applire.models.gap import GapAnalysis

    job, _cv = await _job_and_cv(async_db)
    gap = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=uuid.uuid4(),
        input_fingerprint=f"fp-{uuid.uuid4()}",
        match_score=0.5,
        critical_gaps=[], minor_gaps=[], strengths=[], keyword_gaps=[],
    )
    async_db.add(gap)
    await async_db.commit()
    await async_db.refresh(gap)

    create_resp = await async_client.post("/api/flow", json={"job_id": str(job.id)})
    flow_id = create_resp.json()["flow_id"]
    await async_client.post(
        f"/api/flow/{flow_id}/advance",
        json={"step": "gap_analysis", "artifact_id": str(gap.id)},
    )
    r = await async_client.post(
        f"/api/flow/{flow_id}/advance", json={"step": "cv_generation"},
    )
    assert r.status_code == 200
    assert r.json()["current_step"] == "cv_generation"
    assert r.json()["cv_summary"] is None
