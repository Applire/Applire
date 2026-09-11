# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""REST door for #676 line 1 (was #581, closed NOT_PLANNED — the collector line
is the authority): advance_flow used to write the artifact FK straight from
request.artifact_id with no lookup, so a wrong-referent id reached db.commit()
and raised a raw IntegrityError nothing caught — a bare 500 on the REST door
(routers/flow.py) and the MCP door (mcp/server.py) alike.

These tests drive the real FastAPI app (in-memory sqlite, full router + service
stack, no mocking) over POST /api/flow/{flow_id}/advance to prove the REST door
now answers a typed 422 instead.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_advance_with_unknown_artifact_id_returns_422(
    async_client: AsyncClient,
):
    """A gap_analysis artifact_id that matches no row must 422, not 500."""
    create_resp = await async_client.post("/api/flow", json={})
    assert create_resp.status_code == 201
    flow_id = create_resp.json()["flow_id"]

    bogus_id = str(uuid.uuid4())
    advance_resp = await async_client.post(
        f"/api/flow/{flow_id}/advance",
        json={"step": "gap_analysis", "artifact_id": bogus_id},
    )

    assert advance_resp.status_code == 422
    detail = advance_resp.json()["detail"]
    assert bogus_id in detail
    assert "gap_analysis" in detail


@pytest.mark.asyncio
async def test_advance_with_an_id_from_the_wrong_table_still_422s(
    async_client: AsyncClient, async_db: AsyncSession,
):
    """#581 (adversarial): the lookup is a plain PK `db.get()` against the
    STEP'S OWN model — never a check that the id merely exists somewhere. A
    real, persisted row from a DIFFERENT table (here: a GeneratedCV id passed
    for the gap_analysis step) must 422 exactly like a wholly unknown id,
    never a 500 or a false accept, because GapAnalysis and GeneratedCV mint
    UUIDs from separate, non-overlapping id spaces."""
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job = JobAnalysis(
        raw_text_hash=f"hash-{uuid.uuid4()}", raw_text="Sample job description",
        role_title="Software Engineer", seniority_level="mid",
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

    create_resp = await async_client.post("/api/flow", json={"job_id": str(job.id)})
    assert create_resp.status_code == 201
    flow_id = create_resp.json()["flow_id"]

    # cv.id is a REAL row, just in the wrong table for the gap_analysis step.
    advance_resp = await async_client.post(
        f"/api/flow/{flow_id}/advance",
        json={"step": "gap_analysis", "artifact_id": str(cv.id)},
    )
    assert advance_resp.status_code == 422
    detail = advance_resp.json()["detail"]
    assert str(cv.id) in detail
    assert "gap_analysis" in detail


@pytest.mark.asyncio
async def test_advance_with_real_artifact_id_still_succeeds(
    async_client: AsyncClient,
    async_db: AsyncSession,
):
    """Control: a genuine referent still advances normally — the lookup must
    not over-reject valid artifact_ids."""
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis

    job = JobAnalysis(
        raw_text_hash=f"hash-{uuid.uuid4()}",
        raw_text="Sample job description",
        role_title="Software Engineer",
        seniority_level="mid",
        language_requirement="English",
    )
    async_db.add(job)
    await async_db.flush()

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
    assert create_resp.status_code == 201
    flow_id = create_resp.json()["flow_id"]

    advance_resp = await async_client.post(
        f"/api/flow/{flow_id}/advance",
        json={"step": "gap_analysis", "artifact_id": str(gap.id)},
    )
    assert advance_resp.status_code == 200
    assert advance_resp.json()["current_step"] == "gap_analysis"
