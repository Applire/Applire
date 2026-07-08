"""
Iteration 15 — Flow Orchestrator (unit tests)

Covers:
  - VALID_TRANSITIONS graph completeness
  - _compute_actions per (step, user_type)
  - InvalidTransitionError / ArtifactRequiredError raised correctly
  - SQLite persistence of FlowSession model
  - create_flow: new vs returning user_type, idempotent re-creation
  - advance_flow: valid transition + FK written, invalid → error, missing artifact → error
  - advance_flow: completed_at set on "complete" step
  - Unique constraint enforced on (user_id, job_id)

No Docker, no LLM, no external services.

Run:
    pytest tests/unit/test_iter15_flow_orchestrator.py -v
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.services.flow.orchestrator import (
    VALID_TRANSITIONS,
    ArtifactRequiredError,
    InvalidTransitionError,
    _compute_actions,
    advance_flow,
    advance_flow_on_interview_complete,
    create_flow,
)
from applire.schemas.flow import AdvanceFlowRequest, CreateFlowRequest
from applire.models.flow import FlowSession
from applire.models.session import InterviewSession

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user       # noqa: F401
    import applire.models.job        # noqa: F401
    import applire.models.profile    # noqa: F401
    import applire.models.gap        # noqa: F401
    import applire.models.cv         # noqa: F401
    import applire.models.session    # noqa: F401
    import applire.models.flow       # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads    # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def user_and_job(db):
    """Insert a stub user and job analysis; return (user, job)."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis

    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="abc123",
        raw_text="Senior Python Engineer at Fintech GmbH",
        role_title="Senior Python Engineer",
        required_skills=["Python", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )
    db.add(user)
    db.add(job)
    await db.commit()
    return user, job


# ---------------------------------------------------------------------------
# VALID_TRANSITIONS graph
# ---------------------------------------------------------------------------


def test_valid_transitions_completeness():
    """Every reachable step appears as a key in VALID_TRANSITIONS."""
    all_steps = {
        "jd_analysis", "cv_import", "gap_analysis",
        "interview", "cv_generation", "complete",
    }
    assert set(VALID_TRANSITIONS.keys()) == all_steps


def test_valid_transitions_terminal():
    assert VALID_TRANSITIONS["complete"] == []


def test_valid_transitions_new_user_path():
    # new user: jd_analysis → cv_import → gap_analysis → interview → cv_generation → complete
    assert "cv_import" in VALID_TRANSITIONS["jd_analysis"]
    assert "gap_analysis" in VALID_TRANSITIONS["cv_import"]
    assert "interview" in VALID_TRANSITIONS["gap_analysis"]
    assert "cv_generation" in VALID_TRANSITIONS["interview"]
    assert "complete" in VALID_TRANSITIONS["cv_generation"]


def test_valid_transitions_returning_user_skips():
    # returning user skips cv_import (jd_analysis → gap_analysis)
    # and may skip interview (gap_analysis → cv_generation)
    assert "gap_analysis" in VALID_TRANSITIONS["jd_analysis"]
    assert "cv_generation" in VALID_TRANSITIONS["gap_analysis"]


def test_valid_transitions_no_cv_guided_onboarding():
    # US156 / FMEA 2.6 (ADR-016 amended): a user with a job but no CV skips
    # cv_import + gap_analysis — the guided interview builds the profile.
    assert "interview" in VALID_TRANSITIONS["jd_analysis"]


# ---------------------------------------------------------------------------
# _compute_actions
# ---------------------------------------------------------------------------


def test_compute_actions_jd_analysis_new():
    actions = _compute_actions("jd_analysis", "new")
    assert actions == {"next": "cv_import"}


def test_compute_actions_jd_analysis_returning():
    actions = _compute_actions("jd_analysis", "returning")
    assert actions == {"next": "gap_analysis"}


def test_compute_actions_cv_import():
    actions = _compute_actions("cv_import", "new")
    assert actions == {"next": "gap_analysis"}


def test_compute_actions_gap_analysis_new():
    actions = _compute_actions("gap_analysis", "new")
    assert "next" in actions and actions["next"] == "interview"
    assert "skip" in actions and actions["skip"] == "cv_generation"


def test_compute_actions_gap_analysis_returning():
    actions = _compute_actions("gap_analysis", "returning")
    assert actions == {"next": "cv_generation"}


def test_compute_actions_interview():
    assert _compute_actions("interview", "new") == {"next": "cv_generation"}


def test_compute_actions_cv_generation():
    assert _compute_actions("cv_generation", "new") == {"next": "complete"}


def test_compute_actions_complete():
    assert _compute_actions("complete", "new") == {}


# ---------------------------------------------------------------------------
# create_flow — new vs returning user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_flow_new_user(db, user_and_job):
    _, job = user_and_job
    request = CreateFlowRequest(job_id=job.id)

    # Patch _resolve_user_type to return "new" (no profile in DB)
    response = await create_flow(request, _STUB_USER_ID, db)

    assert response.user_type == "new"
    assert response.current_step == "jd_analysis"
    assert response.available_actions == {"next": "cv_import"}
    assert response.job_summary.role_title == "Senior Python Engineer"


@pytest.mark.asyncio
async def test_create_flow_returning_user(db, user_and_job):
    _, job = user_and_job

    # Insert a profile with high completeness
    from applire.models.profile import MasterProfile
    profile = MasterProfile(
        profile_json={
            "work_experience": [{"company": "Acme", "role": "Dev", "start_date": "2020-01"}],
            "education": [{"institution": "TU Berlin", "degree": "BSc", "field": "CS"}],
            "skills": [{"name": "Python", "category": "technical", "proficiency": "expert"}],
            "languages": [{"language": "German", "level": "native"}],
            "personal_info": {"first_name": "Max", "last_name": "Muster", "email": "max@test.de"},
            "professional_summary": {"de": "Erfahrener Entwickler", "en": "Experienced developer"},
            "certifications": [],
            "publications": [],
            "volunteer_activities": [],
        }
    )
    db.add(profile)
    await db.commit()

    request = CreateFlowRequest(job_id=job.id)
    response = await create_flow(request, _STUB_USER_ID, db)

    assert response.user_type == "returning"
    assert response.available_actions == {"next": "gap_analysis"}


@pytest.mark.asyncio
async def test_create_flow_idempotent(db, user_and_job):
    """Second create_flow for same (user_id, job_id) returns existing flow_id."""
    _, job = user_and_job
    request = CreateFlowRequest(job_id=job.id)

    r1 = await create_flow(request, _STUB_USER_ID, db)
    r2 = await create_flow(request, _STUB_USER_ID, db)

    assert r1.flow_id == r2.flow_id


@pytest.mark.asyncio
async def test_create_flow_job_not_found(db, user_and_job):
    request = CreateFlowRequest(job_id=uuid.uuid4())
    with pytest.raises(LookupError, match="not found"):
        await create_flow(request, _STUB_USER_ID, db)


# ---------------------------------------------------------------------------
# advance_flow — valid transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_flow_valid_transition(db, user_and_job):
    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)

    gap_id = uuid.uuid4()
    result = await advance_flow(
        flow_resp.flow_id,
        AdvanceFlowRequest(step="gap_analysis", artifact_id=gap_id),
        db,
    )

    # new user skipped cv_import → gap_analysis directly allowed from jd_analysis
    assert result.current_step == "gap_analysis"
    assert result.available_actions["next"] == "interview"


@pytest.mark.asyncio
async def test_advance_flow_writes_artifact_fk(db, user_and_job):
    """artifact_id is stored on the flow record atomically."""
    from applire.models.flow import FlowSession
    from sqlalchemy import select

    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)
    gap_id = uuid.uuid4()

    await advance_flow(
        flow_resp.flow_id,
        AdvanceFlowRequest(step="gap_analysis", artifact_id=gap_id),
        db,
    )

    result = await db.execute(
        select(FlowSession).where(FlowSession.id == flow_resp.flow_id)
    )
    flow = result.scalar_one()
    assert flow.gap_analysis_id == gap_id


@pytest.mark.asyncio
async def test_advance_flow_cv_generation_no_artifact_succeeds(db, user_and_job):
    """interview → cv_generation requires no artifact_id."""
    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)
    flow_id = flow_resp.flow_id

    await advance_flow(flow_id, AdvanceFlowRequest(step="gap_analysis", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="interview", artifact_id=uuid.uuid4()), db)

    # cv_generation must succeed without artifact_id
    result = await advance_flow(flow_id, AdvanceFlowRequest(step="cv_generation"), db)
    assert result.current_step == "cv_generation"


@pytest.mark.asyncio
async def test_advance_flow_idempotent_same_step_is_noop(db, user_and_job):
    """Re-advancing to the current step is a no-op, not a 409 (bug 6).

    A benign double-submit (e.g. photo-skip firing twice) must not raise
    InvalidTransitionError — it returns the current state unchanged.
    """
    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)
    flow_id = flow_resp.flow_id

    gap_id = uuid.uuid4()
    await advance_flow(flow_id, AdvanceFlowRequest(step="gap_analysis", artifact_id=gap_id), db)

    # Repeat the exact same transition — must NOT raise
    result = await advance_flow(
        flow_id, AdvanceFlowRequest(step="gap_analysis", artifact_id=gap_id), db
    )
    assert result.current_step == "gap_analysis"


@pytest.mark.asyncio
async def test_advance_flow_idempotent_refreshes_artifact_fk(db, user_and_job):
    """Re-advancing to the current step with a new artifact_id updates the FK.

    Covers CV regeneration: the flow is already 'complete' but a freshly
    generated CV must be recorded.
    """
    from applire.models.flow import FlowSession
    from sqlalchemy import select

    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)
    flow_id = flow_resp.flow_id

    await advance_flow(flow_id, AdvanceFlowRequest(step="gap_analysis", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="interview", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="cv_generation"), db)
    cv_v1 = uuid.uuid4()
    await advance_flow(flow_id, AdvanceFlowRequest(step="complete", artifact_id=cv_v1), db)

    cv_v2 = uuid.uuid4()
    await advance_flow(flow_id, AdvanceFlowRequest(step="complete", artifact_id=cv_v2), db)

    flow = (await db.execute(select(FlowSession).where(FlowSession.id == flow_id))).scalar_one()
    assert flow.current_step == "complete"
    assert flow.generated_cv_id == cv_v2


@pytest.mark.asyncio
async def test_advance_flow_complete_requires_artifact(db, user_and_job):
    """cv_generation → complete requires artifact_id (generated_cv_id)."""
    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)
    flow_id = flow_resp.flow_id

    await advance_flow(flow_id, AdvanceFlowRequest(step="gap_analysis", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="interview", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="cv_generation"), db)

    with pytest.raises(ArtifactRequiredError) as exc_info:
        await advance_flow(flow_id, AdvanceFlowRequest(step="complete"), db)

    assert exc_info.value.step == "complete"
    assert exc_info.value.field == "generated_cv_id"


@pytest.mark.asyncio
async def test_advance_flow_complete_writes_generated_cv_id(db, user_and_job):
    """generated_cv_id is persisted on the flow record when advancing to complete."""
    from applire.models.flow import FlowSession
    from sqlalchemy import select

    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)
    flow_id = flow_resp.flow_id
    cv_id = uuid.uuid4()

    await advance_flow(flow_id, AdvanceFlowRequest(step="gap_analysis", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="interview", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="cv_generation"), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="complete", artifact_id=cv_id), db)

    result = await db.execute(select(FlowSession).where(FlowSession.id == flow_id))
    flow = result.scalar_one()
    assert flow.generated_cv_id == cv_id
    assert flow.completed_at is not None


@pytest.mark.asyncio
async def test_advance_flow_sets_completed_at(db, user_and_job):
    """Advancing to 'complete' sets completed_at."""
    from applire.models.flow import FlowSession
    from sqlalchemy import select

    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)
    flow_id = flow_resp.flow_id

    # Drive to complete step; cv_generation requires no artifact, complete requires generated_cv_id
    await advance_flow(flow_id, AdvanceFlowRequest(step="gap_analysis", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="interview", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="cv_generation"), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="complete", artifact_id=uuid.uuid4()), db)

    result = await db.execute(select(FlowSession).where(FlowSession.id == flow_id))
    flow = result.scalar_one()
    assert flow.completed_at is not None
    assert flow.current_step == "complete"


# ---------------------------------------------------------------------------
# advance_flow — error cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_flow_invalid_transition_raises(db, user_and_job):
    """Advancing to a step not in VALID_TRANSITIONS raises InvalidTransitionError."""
    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)

    with pytest.raises(InvalidTransitionError) as exc_info:
        await advance_flow(
            flow_resp.flow_id,
            AdvanceFlowRequest(step="cv_generation"),  # illegal from jd_analysis
            db,
        )

    err = exc_info.value
    assert err.current == "jd_analysis"
    assert err.target == "cv_generation"
    assert "gap_analysis" in err.allowed or "cv_import" in err.allowed


@pytest.mark.asyncio
async def test_advance_flow_from_complete_raises(db, user_and_job):
    """No transitions are allowed from the terminal 'complete' step."""
    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)
    flow_id = flow_resp.flow_id

    # Reach complete; cv_generation requires no artifact, complete requires generated_cv_id
    await advance_flow(flow_id, AdvanceFlowRequest(step="gap_analysis", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="interview", artifact_id=uuid.uuid4()), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="cv_generation"), db)
    await advance_flow(flow_id, AdvanceFlowRequest(step="complete", artifact_id=uuid.uuid4()), db)

    with pytest.raises(InvalidTransitionError) as exc_info:
        await advance_flow(flow_id, AdvanceFlowRequest(step="cv_generation"), db)

    assert exc_info.value.current == "complete"
    assert exc_info.value.allowed == []


@pytest.mark.asyncio
async def test_advance_flow_missing_artifact_raises(db, user_and_job):
    """Advancing to a step that requires artifact_id without one raises ArtifactRequiredError."""
    _, job = user_and_job
    flow_resp = await create_flow(CreateFlowRequest(job_id=job.id), _STUB_USER_ID, db)

    with pytest.raises(ArtifactRequiredError) as exc_info:
        await advance_flow(
            flow_resp.flow_id,
            AdvanceFlowRequest(step="gap_analysis"),  # artifact_id=None
            db,
        )

    assert exc_info.value.step == "gap_analysis"
    assert exc_info.value.field == "gap_analysis_id"


@pytest.mark.asyncio
async def test_advance_flow_flow_not_found(db, user_and_job):
    with pytest.raises(LookupError):
        await advance_flow(uuid.uuid4(), AdvanceFlowRequest(step="gap_analysis"), db)


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flow_session_sqlite_persistence(db, user_and_job):
    """FlowSession CRUD round-trip via SQLite."""
    from applire.models.flow import FlowSession
    from sqlalchemy import select

    _, job = user_and_job
    flow = FlowSession(
        user_id=_STUB_USER_ID,
        job_id=job.id,
        current_step="jd_analysis",
        user_type="new",
        available_actions={"next": "cv_import"},
    )
    db.add(flow)
    await db.commit()

    result = await db.execute(select(FlowSession).where(FlowSession.id == flow.id))
    persisted = result.scalar_one()
    assert persisted.current_step == "jd_analysis"
    assert persisted.available_actions == {"next": "cv_import"}


@pytest.mark.asyncio
async def test_unique_constraint_enforced(db, user_and_job):
    """Inserting a duplicate (user_id, job_id) raises IntegrityError."""
    from applire.models.flow import FlowSession

    _, job = user_and_job

    flow1 = FlowSession(
        user_id=_STUB_USER_ID,
        job_id=job.id,
        current_step="jd_analysis",
        user_type="new",
        available_actions={},
    )
    db.add(flow1)
    await db.commit()

    flow2 = FlowSession(
        user_id=_STUB_USER_ID,
        job_id=job.id,
        current_step="jd_analysis",
        user_type="new",
        available_actions={},
    )
    db.add(flow2)
    with pytest.raises(IntegrityError):
        await db.commit()


# ---------------------------------------------------------------------------
# advance_flow_on_interview_complete
# ---------------------------------------------------------------------------


async def _make_completed_interview_flow(db, user, job, *, current_step="interview"):
    sess = InterviewSession(
        job_analysis_id=job.id,
        profile_id=uuid.uuid4(),   # FK not enforced on in-memory sqlite
        status="complete",
        state={},
    )
    db.add(sess)
    await db.commit()
    await db.refresh(sess)

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        current_step=current_step,
        user_type="new",
        available_actions={"next": "cv_generation"},
        interview_session_id=sess.id,
    )
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return sess, flow


@pytest.mark.asyncio
async def test_complete_interview_advances_flow_to_cv_generation(db, user_and_job):
    user, job = user_and_job
    sess, flow = await _make_completed_interview_flow(db, user, job)

    await advance_flow_on_interview_complete(sess.id, db)

    await db.refresh(flow)
    assert flow.current_step == "cv_generation"


@pytest.mark.asyncio
async def test_complete_interview_no_owning_flow_is_noop(db):
    # Mode C / profile-enrich: no flow points at this session — must not raise.
    await advance_flow_on_interview_complete(uuid.uuid4(), db)


@pytest.mark.asyncio
async def test_complete_interview_flow_not_on_interview_step_unchanged(db, user_and_job):
    user, job = user_and_job
    sess, flow = await _make_completed_interview_flow(
        db, user, job, current_step="cv_generation"
    )

    await advance_flow_on_interview_complete(sess.id, db)

    await db.refresh(flow)
    assert flow.current_step == "cv_generation"  # untouched
