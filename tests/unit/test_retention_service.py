"""Iteration 17 — Retention Worker additions (unit tests)

Covers the two new TTL rules added in iter17:
  - _tombstone_inactive_applications: soft-delete when expires_at < now
  - _reap_stale_cv_jobs: mark pending/generating CVs failed after 10 min

Uses the same ORM + SQLite approach as iter15/iter16 tests.
JSONB columns use .with_variant(JSON(), "sqlite") so Base.metadata.create_all
works directly — no raw DDL workaround needed.

No Docker, no LLM, no external services.

Run:
    pytest tests/unit/test_iter17_retention.py -v
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.application import Application
from applire.models.cv import CVGenerationStatus, GeneratedCV

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(**kwargs) -> datetime:
    return _now() - timedelta(**kwargs)


def _uid() -> uuid.UUID:
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user         # noqa: F401
    import applire.models.job          # noqa: F401
    import applire.models.profile      # noqa: F401
    import applire.models.gap          # noqa: F401
    import applire.models.cv           # noqa: F401
    import applire.models.session      # noqa: F401
    import applire.models.flow         # noqa: F401
    import applire.models.uploads      # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
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
    """Seed a minimal User + JobAnalysis so FK constraints are satisfied."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis

    user = User(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        email="retention17@applire.community",
        created_at=_now(),
    )
    job = JobAnalysis(
        id=_uid(),
        raw_text_hash="retention17_hash",
        raw_text="Senior Engineer at RetentionCo",
        role_title="Senior Engineer",
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="EN",
    )
    db.add(user)
    db.add(job)
    await db.commit()
    return user, job


@pytest_asyncio.fixture
async def profile(db):
    """Seed a MasterProfile so GeneratedCV FK constraints pass."""
    from applire.models.profile import MasterProfile

    p = MasterProfile(profile_json={}, created_at=_now(), updated_at=_now())
    db.add(p)
    await db.commit()
    return p


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_application(
    db,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    expires_at: datetime,
    deleted_at: datetime | None = None,
) -> Application:
    app = Application(
        user_id=user_id,
        job_analysis_id=job_id,
        expires_at=expires_at,
        deleted_at=deleted_at,
    )
    db.add(app)
    await db.commit()
    return app


async def _seed_cv(
    db,
    job_id: uuid.UUID,
    profile_id: uuid.UUID,
    *,
    status: CVGenerationStatus,
    created_at: datetime,
    deleted_at: datetime | None = None,
) -> GeneratedCV:
    cv = GeneratedCV(
        job_analysis_id=job_id,
        profile_id=profile_id,
        tailored_data={},
        status=status.value,
        created_at=created_at,
        deleted_at=deleted_at,
    )
    db.add(cv)
    await db.commit()
    return cv


# ---------------------------------------------------------------------------
# _tombstone_inactive_applications
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tombstone_inactive_applications_deletes_expired(db, user_and_job):
    from applire.models.job import JobAnalysis
    from applire.retention.worker import _tombstone_inactive_applications

    user, job1 = user_and_job

    # Second job needed for second application (unique constraint on user+job)
    job2 = JobAnalysis(
        id=_uid(), raw_text_hash="h2", raw_text="Job2", role_title="Eng2",
        required_skills=[], nice_to_have_skills=[], keywords=[],
        seniority_level="mid", company_culture_signals=[], language_requirement="EN",
    )
    db.add(job2)
    await db.commit()

    expired = await _seed_application(db, user.id, job1.id, expires_at=_ago(days=1))
    active = await _seed_application(db, user.id, job2.id, expires_at=_now() + timedelta(days=700))

    tombstoned = await _tombstone_inactive_applications(db)
    assert tombstoned == 1

    await db.refresh(expired)
    assert expired.deleted_at is not None

    await db.refresh(active)
    assert active.deleted_at is None


@pytest.mark.asyncio
async def test_tombstone_inactive_applications_skips_already_deleted(db, user_and_job):
    from applire.retention.worker import _tombstone_inactive_applications

    user, job = user_and_job
    await _seed_application(
        db, user.id, job.id,
        expires_at=_ago(days=1),
        deleted_at=_ago(days=5),
    )

    tombstoned = await _tombstone_inactive_applications(db)
    assert tombstoned == 0


@pytest.mark.asyncio
async def test_tombstone_inactive_applications_spares_active(db, user_and_job):
    from applire.retention.worker import _tombstone_inactive_applications

    user, job = user_and_job
    await _seed_application(db, user.id, job.id, expires_at=_now() + timedelta(days=500))

    tombstoned = await _tombstone_inactive_applications(db)
    assert tombstoned == 0


# ---------------------------------------------------------------------------
# _reap_stale_cv_jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reap_stale_pending_cv_marked_failed(db, user_and_job, profile):
    from applire.retention.worker import _reap_stale_cv_jobs

    user, job = user_and_job
    stale = await _seed_cv(
        db, job.id, profile.id,
        status=CVGenerationStatus.pending,
        created_at=_ago(minutes=15),
    )

    reaped = await _reap_stale_cv_jobs(db)
    assert reaped == 1

    result = await db.execute(select(GeneratedCV).where(GeneratedCV.id == stale.id))
    row = result.scalar_one()
    assert row.status == CVGenerationStatus.failed.value
    assert row.error_message == "Generation timed out (stale job reaper)"


@pytest.mark.asyncio
async def test_reap_stale_generating_cv_marked_failed(db, user_and_job, profile):
    from applire.retention.worker import _reap_stale_cv_jobs

    user, job = user_and_job
    stale = await _seed_cv(
        db, job.id, profile.id,
        status=CVGenerationStatus.generating,
        created_at=_ago(minutes=20),
    )

    reaped = await _reap_stale_cv_jobs(db)
    assert reaped == 1

    await db.refresh(stale)
    assert stale.status == CVGenerationStatus.failed.value


@pytest.mark.asyncio
async def test_reap_cv_jobs_spares_recent_pending(db, user_and_job, profile):
    """A pending CV created 2 minutes ago must not be reaped."""
    from applire.retention.worker import _reap_stale_cv_jobs

    user, job = user_and_job
    await _seed_cv(
        db, job.id, profile.id,
        status=CVGenerationStatus.pending,
        created_at=_now() - timedelta(minutes=2),
    )

    reaped = await _reap_stale_cv_jobs(db)
    assert reaped == 0


@pytest.mark.asyncio
async def test_reap_cv_jobs_spares_ready(db, user_and_job, profile):
    from applire.retention.worker import _reap_stale_cv_jobs

    user, job = user_and_job
    await _seed_cv(
        db, job.id, profile.id,
        status=CVGenerationStatus.ready,
        created_at=_ago(minutes=30),
    )

    reaped = await _reap_stale_cv_jobs(db)
    assert reaped == 0


@pytest.mark.asyncio
async def test_reap_cv_jobs_spares_already_failed(db, user_and_job, profile):
    from applire.retention.worker import _reap_stale_cv_jobs

    user, job = user_and_job
    await _seed_cv(
        db, job.id, profile.id,
        status=CVGenerationStatus.failed,
        created_at=_ago(minutes=30),
    )

    reaped = await _reap_stale_cv_jobs(db)
    assert reaped == 0


@pytest.mark.asyncio
async def test_reap_cv_jobs_skips_soft_deleted(db, user_and_job, profile):
    from applire.retention.worker import _reap_stale_cv_jobs

    user, job = user_and_job
    await _seed_cv(
        db, job.id, profile.id,
        status=CVGenerationStatus.pending,
        created_at=_ago(minutes=30),
        deleted_at=_ago(minutes=5),
    )

    reaped = await _reap_stale_cv_jobs(db)
    assert reaped == 0


@pytest.mark.asyncio
async def test_reap_cv_jobs_selects_only_stale(db, user_and_job, profile):
    """Stale pending is reaped; fresh pending is spared in the same run."""
    from applire.models.job import JobAnalysis
    from applire.retention.worker import _reap_stale_cv_jobs

    user, job = user_and_job
    # Second job for second CV (different job_id avoids no unique constraint issue)
    job2 = JobAnalysis(
        id=_uid(), raw_text_hash="cv_retention_h2", raw_text="Job2", role_title="Eng2",
        required_skills=[], nice_to_have_skills=[], keywords=[],
        seniority_level="mid", company_culture_signals=[], language_requirement="EN",
    )
    db.add(job2)
    await db.commit()

    stale = await _seed_cv(db, job.id, profile.id, status=CVGenerationStatus.pending, created_at=_ago(minutes=15))
    fresh = await _seed_cv(db, job2.id, profile.id, status=CVGenerationStatus.pending, created_at=_now() - timedelta(minutes=2))

    reaped = await _reap_stale_cv_jobs(db)
    assert reaped == 1

    result = await db.execute(select(GeneratedCV).where(GeneratedCV.id == stale.id))
    assert result.scalar_one().status == CVGenerationStatus.failed.value

    result = await db.execute(select(GeneratedCV).where(GeneratedCV.id == fresh.id))
    assert result.scalar_one().status == CVGenerationStatus.pending.value
