"""Iteration 17 — Application Entity (unit tests)

Covers:
  - STEP_TO_WORKFLOW_STATUS mapping completeness and values
  - PatchApplicationRequest validator (at least one field required)
  - Application model: default statuses, unique constraint, expires_at set
  - create_application: happy path, denorm from job, user override, LookupError, ConflictError
  - create_application with start_workflow=True: FlowSession created, workflow_status=analyzing
  - list_applications: empty, multiple, filter by workflow_status, filter by user_status,
    excludes soft-deleted
  - get_application: found, LookupError for missing and soft-deleted
  - patch_application: user_status, notes, not-found
  - delete_application: soft-delete, cascade to FlowSession, not-found
  - start_application_workflow: creates FlowSession, ConflictError if already started, not-found
  - sync_workflow_status: maps every step, falls back to analyzing on unknown, resets expires_at

No Docker, no LLM, no external services.

Run:
    pytest tests/unit/test_iter17_application.py -v
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.application import (
    Application,
    STEP_TO_WORKFLOW_STATUS,
    UserStatus,
    WorkflowStatus,
)
from applire.schemas.application import (
    CreateApplicationRequest,
    PatchApplicationRequest,
)
from applire.services.application import (
    ConflictError,
    create_application,
    delete_application,
    get_application,
    list_applications,
    patch_application,
    start_application_workflow,
    sync_workflow_status,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user        # noqa: F401
    import applire.models.job         # noqa: F401
    import applire.models.profile     # noqa: F401
    import applire.models.gap         # noqa: F401
    import applire.models.cv          # noqa: F401
    import applire.models.session     # noqa: F401
    import applire.models.flow        # noqa: F401
    import applire.models.uploads     # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401

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
        company_name="Fintech GmbH",
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
# STEP_TO_WORKFLOW_STATUS mapping
# ---------------------------------------------------------------------------


def test_step_to_workflow_status_all_steps_mapped():
    """Every FlowSession step must map to a WorkflowStatus."""
    expected = {"jd_analysis", "cv_import", "gap_analysis", "interview", "cv_generation", "complete"}
    assert set(STEP_TO_WORKFLOW_STATUS.keys()) == expected


def test_step_to_workflow_status_values():
    assert STEP_TO_WORKFLOW_STATUS["jd_analysis"] == WorkflowStatus.analyzing
    assert STEP_TO_WORKFLOW_STATUS["cv_import"] == WorkflowStatus.analyzing
    assert STEP_TO_WORKFLOW_STATUS["gap_analysis"] == WorkflowStatus.analyzing
    assert STEP_TO_WORKFLOW_STATUS["interview"] == WorkflowStatus.interviewing
    assert STEP_TO_WORKFLOW_STATUS["cv_generation"] == WorkflowStatus.cv_generating
    assert STEP_TO_WORKFLOW_STATUS["complete"] == WorkflowStatus.completed


# ---------------------------------------------------------------------------
# PatchApplicationRequest validator
# ---------------------------------------------------------------------------


def test_patch_request_empty_body_raises():
    """All-None patch body must be rejected by the model validator."""
    with pytest.raises(Exception):  # pydantic ValidationError
        PatchApplicationRequest()


def test_patch_request_accepts_single_field():
    req = PatchApplicationRequest(notes="follow up")
    assert req.notes == "follow up"


def test_patch_request_accepts_user_status():
    req = PatchApplicationRequest(user_status=UserStatus.applied)
    assert req.user_status == UserStatus.applied


# ---------------------------------------------------------------------------
# Application model — defaults and constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_application_default_statuses(db, user_and_job):
    """New application defaults to workflow_status=none, user_status=tracking."""
    _, job = user_and_job
    app = Application(user_id=_STUB_USER_ID, job_analysis_id=job.id)
    db.add(app)
    await db.commit()

    assert app.workflow_status == WorkflowStatus.none.value
    assert app.user_status == UserStatus.tracking.value
    assert app.deleted_at is None
    assert app.expires_at is not None


@pytest.mark.asyncio
async def test_application_unique_constraint(db, user_and_job):
    """Duplicate (user_id, job_analysis_id) raises IntegrityError."""
    _, job = user_and_job
    db.add(Application(user_id=_STUB_USER_ID, job_analysis_id=job.id))
    await db.commit()
    db.add(Application(user_id=_STUB_USER_ID, job_analysis_id=job.id))
    with pytest.raises(IntegrityError):
        await db.commit()


# ---------------------------------------------------------------------------
# create_application
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_application_happy_path(db, user_and_job):
    _, job = user_and_job
    resp = await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db)

    assert resp.user_id == _STUB_USER_ID
    assert resp.job_analysis_id == job.id
    assert resp.workflow_status == WorkflowStatus.none
    assert resp.user_status == UserStatus.tracking
    assert resp.flow_session_id is None


@pytest.mark.asyncio
async def test_create_application_denormalizes_job_fields(db, user_and_job):
    _, job = user_and_job
    resp = await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db)

    assert resp.company_name == "Fintech GmbH"
    assert resp.role_title == "Senior Python Engineer"


@pytest.mark.asyncio
async def test_create_application_user_can_override_denorm_fields(db, user_and_job):
    _, job = user_and_job
    resp = await create_application(
        _STUB_USER_ID,
        CreateApplicationRequest(
            job_analysis_id=job.id,
            company_name="Override Corp",
            role_title="Custom Title",
        ),
        db,
    )

    assert resp.company_name == "Override Corp"
    assert resp.role_title == "Custom Title"


@pytest.mark.asyncio
async def test_create_application_job_not_found(db, user_and_job):
    with pytest.raises(LookupError, match="not found"):
        await create_application(
            _STUB_USER_ID,
            CreateApplicationRequest(job_analysis_id=uuid.uuid4()),
            db,
        )


@pytest.mark.asyncio
async def test_create_application_idempotent_reuse_on_duplicate(db, user_and_job):
    """Re-creating for the same (user, job) reuses the existing Application.

    Regression for the orphaned-application bug: a failed first build leaves an
    Application behind, and the natural retry re-submits the same JD. The retry
    must resume cleanly (idempotent get-or-create), never dead-end on a raw 409.
    """
    _, job = user_and_job
    req = CreateApplicationRequest(job_analysis_id=job.id)
    first = await create_application(_STUB_USER_ID, req, db)

    second = await create_application(_STUB_USER_ID, req, db)

    assert second.id == first.id

    # And it really is one row, not two.
    result = await list_applications(_STUB_USER_ID, db)
    assert result.total == 1


@pytest.mark.asyncio
async def test_create_application_idempotent_reuse_keeps_single_flow(db, user_and_job):
    """Retry with start_workflow=True reuses the existing FlowSession.

    Covers the create→flow seam: the second call must not strand a second
    FlowSession (uq_flow_session_user_job would otherwise collide too).
    """
    _, job = user_and_job
    req = CreateApplicationRequest(job_analysis_id=job.id, start_workflow=True)
    first = await create_application(_STUB_USER_ID, req, db)
    assert first.flow_session_id is not None

    second = await create_application(_STUB_USER_ID, req, db)

    assert second.id == first.id
    assert second.flow_session_id == first.flow_session_id
    assert second.workflow_status == WorkflowStatus.analyzing


@pytest.mark.asyncio
async def test_create_application_reuse_reactivates_soft_deleted(db, user_and_job):
    """The unique constraint ignores deleted_at, so a soft-deleted application
    still owns the (user, job) slot. Re-creating must reactivate it, not 409."""
    _, job = user_and_job
    req = CreateApplicationRequest(job_analysis_id=job.id)
    first = await create_application(_STUB_USER_ID, req, db)
    await delete_application(first.id, db)

    revived = await create_application(_STUB_USER_ID, req, db)

    assert revived.id == first.id
    # It is live again and visible in the pipeline.
    result = await list_applications(_STUB_USER_ID, db)
    assert result.total == 1
    assert result.items[0].id == first.id


@pytest.mark.asyncio
async def test_create_application_with_start_workflow(db, user_and_job):
    """start_workflow=True creates a FlowSession atomically; workflow_status=analyzing."""
    _, job = user_and_job
    resp = await create_application(
        _STUB_USER_ID,
        CreateApplicationRequest(job_analysis_id=job.id, start_workflow=True),
        db,
    )

    assert resp.workflow_status == WorkflowStatus.analyzing
    assert resp.flow_session_id is not None


# ---------------------------------------------------------------------------
# list_applications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_applications_empty(db, user_and_job):
    result = await list_applications(_STUB_USER_ID, db)
    assert result.total == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_list_applications_returns_all(db, user_and_job):
    from applire.models.job import JobAnalysis

    _, job1 = user_and_job
    job2 = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="def456",
        raw_text="Backend Engineer at Startup AG",
        role_title="Backend Engineer",
        required_skills=["Go"],
        nice_to_have_skills=[],
        keywords=["Go"],
        seniority_level="mid",
        company_culture_signals=[],
        language_requirement="EN",
    )
    db.add(job2)
    await db.commit()

    await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job1.id), db)
    await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job2.id), db)

    result = await list_applications(_STUB_USER_ID, db)
    assert result.total == 2


@pytest.mark.asyncio
async def test_list_applications_excludes_deleted(db, user_and_job):
    _, job = user_and_job
    resp = await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db)
    await delete_application(resp.id, db)

    result = await list_applications(_STUB_USER_ID, db)
    assert result.total == 0


@pytest.mark.asyncio
async def test_list_applications_filter_by_user_status(db, user_and_job):
    from applire.models.job import JobAnalysis

    _, job1 = user_and_job
    job2 = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="ghi789",
        raw_text="DevOps Engineer",
        role_title="DevOps Engineer",
        required_skills=["Kubernetes"],
        nice_to_have_skills=[],
        keywords=["K8s"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="EN",
    )
    db.add(job2)
    await db.commit()

    r1 = await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job1.id), db)
    await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job2.id), db)

    await patch_application(r1.id, PatchApplicationRequest(user_status=UserStatus.applied), db)

    result = await list_applications(_STUB_USER_ID, db, user_status=UserStatus.applied)
    assert result.total == 1
    assert result.items[0].user_status == UserStatus.applied


@pytest.mark.asyncio
async def test_list_applications_filter_by_workflow_status(db, user_and_job):
    from applire.models.job import JobAnalysis

    _, job1 = user_and_job
    job2 = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="jkl012",
        raw_text="ML Engineer at AI Corp",
        role_title="ML Engineer",
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=["ML"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="EN",
    )
    db.add(job2)
    await db.commit()

    await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job1.id), db)
    await create_application(
        _STUB_USER_ID,
        CreateApplicationRequest(job_analysis_id=job2.id, start_workflow=True),
        db,
    )

    result = await list_applications(_STUB_USER_ID, db, workflow_status=WorkflowStatus.none)
    assert result.total == 1
    assert result.items[0].workflow_status == WorkflowStatus.none


# ---------------------------------------------------------------------------
# get_application
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_application_found(db, user_and_job):
    _, job = user_and_job
    created = await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db)
    fetched = await get_application(created.id, db)
    assert fetched.id == created.id


@pytest.mark.asyncio
async def test_get_application_not_found(db):
    with pytest.raises(LookupError):
        await get_application(uuid.uuid4(), db)


@pytest.mark.asyncio
async def test_get_application_deleted_raises(db, user_and_job):
    _, job = user_and_job
    resp = await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db)
    await delete_application(resp.id, db)

    with pytest.raises(LookupError):
        await get_application(resp.id, db)


# ---------------------------------------------------------------------------
# patch_application
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_application_updates_user_status(db, user_and_job):
    _, job = user_and_job
    resp = await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db)
    patched = await patch_application(resp.id, PatchApplicationRequest(user_status=UserStatus.applied), db)
    assert patched.user_status == UserStatus.applied


@pytest.mark.asyncio
async def test_patch_application_updates_notes(db, user_and_job):
    _, job = user_and_job
    resp = await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db)
    patched = await patch_application(resp.id, PatchApplicationRequest(notes="call recruiter Monday"), db)
    assert patched.notes == "call recruiter Monday"


@pytest.mark.asyncio
async def test_patch_application_not_found(db):
    with pytest.raises(LookupError):
        await patch_application(uuid.uuid4(), PatchApplicationRequest(notes="x"), db)


# ---------------------------------------------------------------------------
# delete_application
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_application_soft_deletes(db, user_and_job):
    from sqlalchemy import select

    _, job = user_and_job
    resp = await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db)
    await delete_application(resp.id, db)

    result = await db.execute(select(Application).where(Application.id == resp.id))
    app = result.scalar_one()
    assert app.deleted_at is not None


@pytest.mark.asyncio
async def test_delete_application_cascades_to_flow_session(db, user_and_job):
    from applire.models.flow import FlowSession
    from sqlalchemy import select

    _, job = user_and_job
    resp = await create_application(
        _STUB_USER_ID,
        CreateApplicationRequest(job_analysis_id=job.id, start_workflow=True),
        db,
    )
    await delete_application(resp.id, db)

    result = await db.execute(select(FlowSession).where(FlowSession.id == resp.flow_session_id))
    flow = result.scalar_one()
    assert flow.deleted_at is not None


@pytest.mark.asyncio
async def test_delete_application_not_found(db):
    with pytest.raises(LookupError):
        await delete_application(uuid.uuid4(), db)


# ---------------------------------------------------------------------------
# start_application_workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_workflow_creates_flow_session(db, user_and_job):
    _, job = user_and_job
    resp = await create_application(_STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db)
    assert resp.flow_session_id is None

    started = await start_application_workflow(resp.id, _STUB_USER_ID, db)
    assert started.flow_session_id is not None
    assert started.workflow_status == WorkflowStatus.analyzing


@pytest.mark.asyncio
async def test_start_workflow_conflict_if_already_started(db, user_and_job):
    _, job = user_and_job
    resp = await create_application(
        _STUB_USER_ID,
        CreateApplicationRequest(job_analysis_id=job.id, start_workflow=True),
        db,
    )
    with pytest.raises(ConflictError):
        await start_application_workflow(resp.id, _STUB_USER_ID, db)


@pytest.mark.asyncio
async def test_start_workflow_conflict_message_leaks_no_identifiers(db, user_and_job):
    """Any ConflictError surfaced to the API (router passes str(exc) verbatim as
    `detail`) must be user-appropriate — no bare UUID / internal identifier."""
    import re

    _, job = user_and_job
    resp = await create_application(
        _STUB_USER_ID,
        CreateApplicationRequest(job_analysis_id=job.id, start_workflow=True),
        db,
    )
    with pytest.raises(ConflictError) as exc_info:
        await start_application_workflow(resp.id, _STUB_USER_ID, db)

    message = str(exc_info.value)
    uuid_re = re.compile(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    assert not uuid_re.search(message), f"ConflictError leaked a UUID: {message!r}"
    # Also no other obvious internal leakage.
    assert str(resp.id) not in message


@pytest.mark.asyncio
async def test_start_workflow_not_found(db):
    with pytest.raises(LookupError):
        await start_application_workflow(uuid.uuid4(), _STUB_USER_ID, db)


# ---------------------------------------------------------------------------
# sync_workflow_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_workflow_status_interview_step(db, user_and_job):
    from sqlalchemy import select

    _, job = user_and_job
    resp = await create_application(
        _STUB_USER_ID,
        CreateApplicationRequest(job_analysis_id=job.id, start_workflow=True),
        db,
    )
    await sync_workflow_status(resp.id, "interview", db)

    result = await db.execute(select(Application).where(Application.id == resp.id))
    assert result.scalar_one().workflow_status == WorkflowStatus.interviewing.value


@pytest.mark.asyncio
async def test_sync_workflow_status_complete_step(db, user_and_job):
    from sqlalchemy import select

    _, job = user_and_job
    resp = await create_application(
        _STUB_USER_ID,
        CreateApplicationRequest(job_analysis_id=job.id, start_workflow=True),
        db,
    )
    await sync_workflow_status(resp.id, "complete", db)

    result = await db.execute(select(Application).where(Application.id == resp.id))
    assert result.scalar_one().workflow_status == WorkflowStatus.completed.value


@pytest.mark.asyncio
async def test_sync_workflow_status_unknown_step_falls_back_to_analyzing(db, user_and_job):
    from sqlalchemy import select

    _, job = user_and_job
    resp = await create_application(
        _STUB_USER_ID,
        CreateApplicationRequest(job_analysis_id=job.id, start_workflow=True),
        db,
    )
    await sync_workflow_status(resp.id, "nonexistent_future_step", db)

    result = await db.execute(select(Application).where(Application.id == resp.id))
    assert result.scalar_one().workflow_status == WorkflowStatus.analyzing.value


@pytest.mark.asyncio
async def test_sync_workflow_status_resets_expires_at(db, user_and_job):
    from sqlalchemy import select

    _, job = user_and_job
    resp = await create_application(
        _STUB_USER_ID,
        CreateApplicationRequest(job_analysis_id=job.id, start_workflow=True),
        db,
    )
    original_expires = resp.expires_at

    await sync_workflow_status(resp.id, "cv_generation", db)

    result = await db.execute(select(Application).where(Application.id == resp.id))
    app = result.scalar_one()
    # expires_at must be reset to ~730 days from now (≥ original since both are near now)
    # SQLite returns naive datetimes; normalise both sides before comparing.
    def _naive(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt

    assert _naive(app.expires_at) >= _naive(original_expires)
    # Must be far in the future (> 700 days from now)
    min_expected = datetime.now() + timedelta(days=700)
    assert _naive(app.expires_at) > min_expected
