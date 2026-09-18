# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#676 line 35 (edge UAT 2026-09-18) — the CV id passed at `cv_generation`.

`advance_flow(step="cv_generation", artifact_id=<cv_id>)` was accepted and
SILENTLY dropped: `cv_generation` was not in `_ARTIFACT_FIELD`, so the call
answered `current_step: cv_generation` with `cv_summary: null` while
`flow_sessions.generated_cv_id` stayed NULL until the agent happened to re-pass
the same id at `complete`.

These run the MCP tool FUNCTION and the orchestrator in-process against a real
`sqlite+aiosqlite` session — the repo-root stdio tier pins registration and
return SHAPE, this file pins behaviour (same split as
`test_mcp_profile_health.py`). The REST door's own seam test is the twin file
`backend/tests/unit/test_676_flow_cv_generation_artifact.py`.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from mcp.shared.exceptions import McpError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile


@pytest_asyncio.fixture
async def db():
    import importlib
    import pkgutil

    import applire.models  # noqa: F401

    for _m in pkgutil.iter_modules(applire.models.__path__):
        importlib.import_module(f"applire.models.{_m.name}")
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _seed(db):
    """A user, a job, a profile and a rendered CV — the state an agent reaches
    right after `generate_cv` returns a `cv_id`."""
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    user = User(email=f"kaile-{uuid.uuid4()}@example.org")
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
    db.add_all([user, job, profile])
    await db.flush()
    cv = GeneratedCV(job_analysis_id=job.id, profile_id=profile.id, tailored_data={})
    db.add(cv)
    await db.commit()
    await db.refresh(cv)
    return user, job, cv


async def _flow_at(db, user, job, step: str):
    """A flow parked on `step` — built through the model, because the point of
    each test below is what ADVANCING does, not how the flow got there."""
    from applire.models.flow import FlowSession

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        current_step=step,
        user_type="returning",
        available_actions={},
    )
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return flow


async def _advance(db, flow_id, step, artifact_id=None):
    """Drive the AGENT door (the MCP tool function), not the service directly."""
    from applire.mcp.server import advance_flow as advance_tool

    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)):
        return await advance_tool(
            flow_id=str(flow_id),
            step=step,
            artifact_id=str(artifact_id) if artifact_id else None,
        )


# ---------------------------------------------------------------------------
# The dropped id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cv_generation_records_the_cv_id_at_the_agent_door(db):
    """THE defect: the id passed at the step that produced the artifact lands on
    the flow record. Mutation kill — delete `"cv_generation"` from
    `_ARTIFACT_FIELD` and this test fails on both assertions."""
    user, job, cv = await _seed(db)
    flow = await _flow_at(db, user, job, "interview")

    payload = await _advance(db, flow.id, "cv_generation", cv.id)

    assert payload["current_step"] == "cv_generation"
    assert payload["cv_summary"] is not None, (
        "the agent was told the step succeeded while the flow did not know its CV"
    )
    assert payload["cv_summary"]["cv_id"] == str(cv.id)
    assert payload["notices"] == [], "a recorded id is not a notice"

    await db.refresh(flow)
    stored = flow
    assert stored.generated_cv_id == cv.id, (
        "flow_sessions.generated_cv_id stayed NULL — the id was dropped"
    )


@pytest.mark.asyncio
async def test_the_id_is_recorded_on_the_idempotent_re_advance(db):
    """The exact edge-UAT shape: the interview page advances into
    `cv_generation` with no id, the CV page re-advances with one. The second
    call is the idempotent branch, which is where the drop happened."""
    user, job, cv = await _seed(db)
    flow = await _flow_at(db, user, job, "interview")

    await _advance(db, flow.id, "cv_generation")           # no artifact yet
    payload = await _advance(db, flow.id, "cv_generation", cv.id)

    assert payload["cv_summary"]["cv_id"] == str(cv.id)
    await db.refresh(flow)
    stored = flow
    assert stored.generated_cv_id == cv.id


@pytest.mark.asyncio
async def test_cv_generation_without_an_artifact_id_still_advances(db):
    """Back-compat / negative control. Three callers advance into this step with
    no id (the gaps page, the interview page, `advance_flow_on_interview_complete`)
    because the step is entered in order to GENERATE the CV. Mutation kill for
    `_ARTIFACT_REQUIRED`: add `"cv_generation"` to it and this test fails."""
    user, job, _cv = await _seed(db)
    flow = await _flow_at(db, user, job, "interview")

    payload = await _advance(db, flow.id, "cv_generation")

    assert payload["current_step"] == "cv_generation"
    assert payload["cv_summary"] is None
    await db.refresh(flow)
    stored = flow
    assert stored.generated_cv_id is None


@pytest.mark.asyncio
async def test_complete_still_requires_and_records_the_cv_id(db):
    """`complete` is unchanged in both directions — it still refuses without an
    id and still writes the same field."""
    user, job, cv = await _seed(db)
    flow = await _flow_at(db, user, job, "cv_generation")

    with pytest.raises(McpError) as exc:
        await _advance(db, flow.id, "complete")
    assert "artifact_id" in str(exc.value)

    payload = await _advance(db, flow.id, "complete", cv.id)
    assert payload["current_step"] == "complete"
    await db.refresh(flow)
    stored = flow
    assert stored.generated_cv_id == cv.id


@pytest.mark.asyncio
async def test_a_wrong_referent_id_at_cv_generation_is_still_refused(db):
    """#676 line 1's lookup covers the new step too: the id must resolve in
    `GeneratedCV`, not merely exist somewhere."""
    user, job, _cv = await _seed(db)
    flow = await _flow_at(db, user, job, "interview")

    with pytest.raises(McpError) as exc:
        await _advance(db, flow.id, "cv_generation", uuid.uuid4())
    assert "cv_generation" in str(exc.value)


# ---------------------------------------------------------------------------
# The notice — never a silent drop, never an error (ruling D-4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_id_at_a_step_that_records_none_comes_back_as_a_notice(db):
    """`cv_import` records no artifact. The call SUCCEEDS (an error would break
    agents that follow today's tool description) and says what it could not do.
    Mutation kill: drop the `notices.append(...)` line and this test fails."""
    user, job, cv = await _seed(db)
    flow = await _flow_at(db, user, job, "jd_analysis")

    payload = await _advance(db, flow.id, "cv_import", cv.id)

    assert payload["current_step"] == "cv_import", "the transition itself must succeed"
    assert len(payload["notices"]) == 1
    notice = payload["notices"][0]
    assert "not recorded" in notice and "cv_import" in notice


@pytest.mark.asyncio
async def test_a_step_that_records_nothing_and_gets_no_id_says_nothing(db):
    """Negative control: the notice is about the id, not about the step."""
    user, job, _cv = await _seed(db)
    flow = await _flow_at(db, user, job, "jd_analysis")

    payload = await _advance(db, flow.id, "cv_import")
    assert payload["notices"] == []


@pytest.mark.asyncio
async def test_a_read_of_the_state_never_carries_a_notice(db):
    """`get_flow_state` reports the record, not a call that was not made."""
    from applire.mcp.server import get_flow_state as get_state_tool

    user, job, cv = await _seed(db)
    flow = await _flow_at(db, user, job, "jd_analysis")
    await _advance(db, flow.id, "cv_import", cv.id)

    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)):
        state = await get_state_tool(flow_id=str(flow.id))
    assert state["notices"] == []


def test_the_notice_names_every_recording_step_and_the_letter():
    """Drift guard (#603's lesson): the notice's mapping sentence is DERIVED from
    `_ARTIFACT_FIELD`, so a future step cannot be added without appearing in what
    the agent is told — and the sentence states the one thing #673 line 46 asks
    for, that the cover letter is not a flow step."""
    from applire.services.flow.orchestrator import (
        _ARTIFACT_FIELD,
        unrecordable_artifact_notice,
    )

    notice = unrecordable_artifact_notice("cv_import")
    for step, field in _ARTIFACT_FIELD.items():
        assert step in notice, f"{step} missing from the notice"
        assert field in notice, f"{field} missing from the notice"
    assert "generate_cover_letter" in notice


def test_every_recording_step_has_a_model_to_resolve_against():
    """The two maps are one fact: a step that writes an FK must have a model for
    `_check_artifact_exists`, or a bad id reaches `db.commit()` as a raw
    IntegrityError (the #676 line 1 defect) at the new step."""
    from applire.services.flow.orchestrator import _ARTIFACT_FIELD, _ARTIFACT_MODEL

    assert set(_ARTIFACT_FIELD) == set(_ARTIFACT_MODEL)


def test_required_is_a_subset_of_recorded():
    """A step cannot require an id it has nowhere to put."""
    from applire.services.flow.orchestrator import _ARTIFACT_FIELD, _ARTIFACT_REQUIRED

    assert _ARTIFACT_REQUIRED <= set(_ARTIFACT_FIELD)
    assert "cv_generation" not in _ARTIFACT_REQUIRED
