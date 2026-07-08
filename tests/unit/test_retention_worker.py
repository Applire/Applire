"""
Iteration 10 — Retention Worker (unit tests)
Each TTL rule tested with an in-memory SQLite fixture.

Models use PostgreSQL JSONB which SQLite doesn't support, so we create
tables with raw DDL (TEXT instead of JSONB) and insert data via raw SQL.
No Docker or real Postgres required.

Run:
    pytest tests/unit/ -v
"""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_SQLITE_URL = "sqlite+aiosqlite:///:memory:"

# Minimal DDL — JSONB → TEXT, UUID → TEXT for SQLite compatibility.
_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS master_profiles (
    id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS job_analyses (
    id TEXT PRIMARY KEY,
    raw_text_hash TEXT NOT NULL UNIQUE,
    raw_text TEXT NOT NULL,
    source_url TEXT,
    role_title TEXT NOT NULL,
    required_skills TEXT NOT NULL DEFAULT '[]',
    nice_to_have_skills TEXT NOT NULL DEFAULT '[]',
    keywords TEXT NOT NULL DEFAULT '[]',
    seniority_level TEXT NOT NULL,
    company_culture_signals TEXT NOT NULL DEFAULT '[]',
    language_requirement TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS gap_analyses (
    id TEXT PRIMARY KEY,
    job_analysis_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    match_score INTEGER NOT NULL,
    critical_gaps TEXT NOT NULL DEFAULT '[]',
    minor_gaps TEXT NOT NULL DEFAULT '[]',
    strengths TEXT NOT NULL DEFAULT '[]',
    keyword_gaps TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS interview_sessions (
    id TEXT PRIMARY KEY,
    job_analysis_id TEXT NOT NULL,
    gap_analysis_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    state TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS generated_cvs (
    id TEXT PRIMARY KEY,
    job_analysis_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    tailored_data TEXT NOT NULL DEFAULT '{}',
    template TEXT NOT NULL DEFAULT 'classic_german',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS cv_import_jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    filename TEXT NOT NULL DEFAULT 'upload',
    status TEXT NOT NULL DEFAULT 'pending',
    error_code TEXT,
    error_message TEXT,
    result TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS gap_analysis_jobs (
    id TEXT PRIMARY KEY,
    job_analysis_id TEXT NOT NULL,
    user_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error_code TEXT,
    error_message TEXT,
    result_gap_analysis_id TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS generated_cover_letters (
    id TEXT PRIMARY KEY,
    job_analysis_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    letter_data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    job_analysis_id TEXT NOT NULL,
    workflow_status TEXT NOT NULL DEFAULT 'none',
    user_status TEXT NOT NULL DEFAULT 'tracking',
    submitted_cv_id TEXT,
    submitted_cover_letter_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    deleted_at TEXT
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(**kwargs) -> datetime:
    return _now() - timedelta(**kwargs)


def _ts(dt: datetime) -> str:
    return dt.isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all retention-relevant tables."""
    engine = create_async_engine(_SQLITE_URL, echo=False)
    async with engine.begin() as conn:
        for stmt in _CREATE_TABLES.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(text(stmt))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Rule 10.7 — uploads (graceful no-op when table absent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_uploads_returns_zero_when_table_absent(db):
    from applire.retention.worker import _purge_uploads

    result = await _purge_uploads(db)
    assert result == 0


# ---------------------------------------------------------------------------
# Rule 10.8 — interview_sessions (30-day TTL)
# ---------------------------------------------------------------------------


async def _seed_session(db: AsyncSession, *, updated_at: datetime) -> str:
    sid = _uid()
    await db.execute(
        text(
            "INSERT INTO interview_sessions "
            "(id, job_analysis_id, gap_analysis_id, profile_id, status, state, created_at, updated_at) "
            "VALUES (:id, :jid, :gid, :pid, 'active', '{}', :now, :upd)"
        ),
        {"id": sid, "jid": _uid(), "gid": _uid(), "pid": _uid(),
         "now": _ts(_now()), "upd": _ts(updated_at)},
    )
    await db.commit()
    return sid


@pytest.mark.asyncio
async def test_purge_sessions_deletes_old_sessions(db):
    from applire.retention.worker import _purge_sessions

    await _seed_session(db, updated_at=_ago(days=31))
    await _seed_session(db, updated_at=_ago(days=5))

    deleted = await _purge_sessions(db)
    assert deleted == 1


@pytest.mark.asyncio
async def test_purge_sessions_spares_recent_sessions(db):
    from applire.retention.worker import _purge_sessions

    await _seed_session(db, updated_at=_ago(days=10))

    deleted = await _purge_sessions(db)
    assert deleted == 0


# ---------------------------------------------------------------------------
# Rule 10.9 — generated_cvs (expires_at TTL)
# ---------------------------------------------------------------------------


async def _seed_cv(db: AsyncSession, *, expires_at: datetime, deleted_at: datetime | None = None) -> str:
    cid = _uid()
    await db.execute(
        text(
            "INSERT INTO generated_cvs "
            "(id, job_analysis_id, profile_id, tailored_data, template, created_at, expires_at, deleted_at) "
            "VALUES (:id, :jid, :pid, '{}', 'classic_german', :now, :exp, :del)"
        ),
        {"id": cid, "jid": _uid(), "pid": _uid(),
         "now": _ts(_now()), "exp": _ts(expires_at),
         "del": _ts(deleted_at) if deleted_at else None},
    )
    await db.commit()
    return cid


@pytest.mark.asyncio
async def test_purge_cvs_deletes_expired(db):
    from applire.retention.worker import _purge_cvs

    await _seed_cv(db, expires_at=_ago(days=1))
    await _seed_cv(db, expires_at=_now() + timedelta(days=89))

    deleted = await _purge_cvs(db)
    assert deleted == 1


@pytest.mark.asyncio
async def test_purge_cvs_spares_unexpired(db):
    from applire.retention.worker import _purge_cvs

    await _seed_cv(db, expires_at=_now() + timedelta(days=45))

    deleted = await _purge_cvs(db)
    assert deleted == 0


# ---------------------------------------------------------------------------
# E036 follow-up — cv_import_jobs (short TTL)
# ---------------------------------------------------------------------------


async def _seed_import_job(db: AsyncSession, *, expires_at: datetime, deleted_at: datetime | None = None) -> str:
    jid = _uid()
    await db.execute(
        text(
            "INSERT INTO cv_import_jobs "
            "(id, filename, status, created_at, expires_at, deleted_at) "
            "VALUES (:id, 'cv.pdf', 'ready', :now, :exp, :del)"
        ),
        {"id": jid, "now": _ts(_now()), "exp": _ts(expires_at),
         "del": _ts(deleted_at) if deleted_at else None},
    )
    await db.commit()
    return jid


@pytest.mark.asyncio
async def test_purge_import_jobs_deletes_expired(db):
    from applire.retention.worker import _purge_import_jobs

    # Date-scale offsets (like _purge_cvs) so the SQLite TEXT compare stays correct:
    # the worker binds a datetime, which aiosqlite renders space-separated, and stored
    # ISO strings are 'T'-separated — a same-day (time-only) gap would flip the lexical
    # compare. Postgres (timestamptz) is unaffected; this is a harness-only concern.
    await _seed_import_job(db, expires_at=_ago(days=1))
    await _seed_import_job(db, expires_at=_now() + timedelta(days=1))

    deleted = await _purge_import_jobs(db)
    assert deleted == 1


@pytest.mark.asyncio
async def test_purge_import_jobs_returns_zero_when_table_absent(db):
    from applire.retention.worker import _purge_import_jobs

    await db.execute(text("DROP TABLE cv_import_jobs"))
    await db.commit()
    assert await _purge_import_jobs(db) == 0


# ---------------------------------------------------------------------------
# E037 N2 — gap_analysis_jobs (short TTL)
# ---------------------------------------------------------------------------


async def _seed_gap_job(db: AsyncSession, *, expires_at: datetime, deleted_at: datetime | None = None) -> str:
    jid = _uid()
    await db.execute(
        text(
            "INSERT INTO gap_analysis_jobs "
            "(id, job_analysis_id, status, created_at, expires_at, deleted_at) "
            "VALUES (:id, :jaid, 'ready', :now, :exp, :del)"
        ),
        {"id": jid, "jaid": _uid(), "now": _ts(_now()), "exp": _ts(expires_at),
         "del": _ts(deleted_at) if deleted_at else None},
    )
    await db.commit()
    return jid


@pytest.mark.asyncio
async def test_purge_gap_jobs_deletes_expired(db):
    from applire.retention.worker import _purge_gap_jobs

    # Date-scale offsets — see the note in test_purge_import_jobs_deletes_expired
    # (bound datetime vs stored ISO string under the SQLite harness).
    await _seed_gap_job(db, expires_at=_ago(days=1))
    await _seed_gap_job(db, expires_at=_now() + timedelta(days=1))

    deleted = await _purge_gap_jobs(db)
    assert deleted == 1


@pytest.mark.asyncio
async def test_purge_gap_jobs_returns_zero_when_table_absent(db):
    from applire.retention.worker import _purge_gap_jobs

    await db.execute(text("DROP TABLE gap_analysis_jobs"))
    await db.commit()
    assert await _purge_gap_jobs(db) == 0


# ---------------------------------------------------------------------------
# Rule 10.10 — master_profiles soft-delete after 24 months
# ---------------------------------------------------------------------------


async def _seed_profile(db: AsyncSession, *, updated_at: datetime, deleted_at: datetime | None = None) -> str:
    pid = _uid()
    await db.execute(
        text(
            "INSERT INTO master_profiles (id, profile_json, created_at, updated_at, deleted_at) "
            "VALUES (:id, '{}', :now, :upd, :del)"
        ),
        {"id": pid, "now": _ts(_now()), "upd": _ts(updated_at),
         "del": _ts(deleted_at) if deleted_at else None},
    )
    await db.commit()
    return pid


@pytest.mark.asyncio
async def test_tombstone_inactive_profiles(db):
    from applire.retention.worker import _tombstone_inactive_profiles

    inactive_id = await _seed_profile(db, updated_at=_ago(days=731))
    active_id = await _seed_profile(db, updated_at=_ago(days=100))

    tombstoned = await _tombstone_inactive_profiles(db)
    assert tombstoned == 1

    row = (await db.execute(text("SELECT deleted_at FROM master_profiles WHERE id = :id"), {"id": inactive_id})).one()
    assert row[0] is not None

    row = (await db.execute(text("SELECT deleted_at FROM master_profiles WHERE id = :id"), {"id": active_id})).one()
    assert row[0] is None


@pytest.mark.asyncio
async def test_tombstone_skips_already_deleted_profiles(db):
    from applire.retention.worker import _tombstone_inactive_profiles

    await _seed_profile(db, updated_at=_ago(days=800), deleted_at=_ago(days=5))

    tombstoned = await _tombstone_inactive_profiles(db)
    assert tombstoned == 0


# ---------------------------------------------------------------------------
# Rule 10.10 — users soft-delete after 24 months
# ---------------------------------------------------------------------------


async def _seed_user(db: AsyncSession, *, created_at: datetime, deleted_at: datetime | None = None) -> str:
    uid = _uid()
    await db.execute(
        text(
            "INSERT INTO users (id, email, created_at, deleted_at) "
            "VALUES (:id, :email, :cat, :del)"
        ),
        {"id": uid, "email": f"user-{uid[:8]}@example.com",
         "cat": _ts(created_at), "del": _ts(deleted_at) if deleted_at else None},
    )
    await db.commit()
    return uid


@pytest.mark.asyncio
async def test_tombstone_inactive_users(db):
    from applire.retention.worker import _tombstone_inactive_users

    inactive_id = await _seed_user(db, created_at=_ago(days=731))
    recent_id = await _seed_user(db, created_at=_ago(days=100))

    tombstoned = await _tombstone_inactive_users(db)
    assert tombstoned == 1

    row = (await db.execute(text("SELECT deleted_at FROM users WHERE id = :id"), {"id": inactive_id})).one()
    assert row[0] is not None

    row = (await db.execute(text("SELECT deleted_at FROM users WHERE id = :id"), {"id": recent_id})).one()
    assert row[0] is None


@pytest.mark.asyncio
async def test_tombstone_skips_already_deleted_users(db):
    from applire.retention.worker import _tombstone_inactive_users

    await _seed_user(db, created_at=_ago(days=800), deleted_at=_ago(days=10))

    tombstoned = await _tombstone_inactive_users(db)
    assert tombstoned == 0


# ---------------------------------------------------------------------------
# OperationalError logging — tombstone functions must not swallow errors silently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tombstone_inactive_profiles_logs_warning_on_operational_error(caplog):
    """OperationalError (e.g. lost DB connection) must be logged, not silently discarded."""
    import logging
    from unittest.mock import AsyncMock
    from sqlalchemy.exc import OperationalError
    from applire.retention.worker import _tombstone_inactive_profiles

    mock_db = AsyncMock()
    mock_db.execute.side_effect = OperationalError("stmt", {}, Exception("connection lost"))
    mock_db.rollback = AsyncMock()

    with caplog.at_level(logging.WARNING, logger="applire.retention.worker"):
        result = await _tombstone_inactive_profiles(mock_db)

    assert result == 0
    assert any(rec.levelname == "WARNING" for rec in caplog.records), (
        "expected a WARNING log when OperationalError is caught"
    )


@pytest.mark.asyncio
async def test_tombstone_inactive_users_logs_warning_on_operational_error(caplog):
    """OperationalError during user tombstone must be logged as a warning."""
    import logging
    from unittest.mock import AsyncMock
    from sqlalchemy.exc import OperationalError
    from applire.retention.worker import _tombstone_inactive_users

    mock_db = AsyncMock()
    mock_db.execute.side_effect = OperationalError("stmt", {}, Exception("connection lost"))
    mock_db.rollback = AsyncMock()

    with caplog.at_level(logging.WARNING, logger="applire.retention.worker"):
        result = await _tombstone_inactive_users(mock_db)

    assert result == 0
    assert any(rec.levelname == "WARNING" for rec in caplog.records)


@pytest.mark.asyncio
async def test_tombstone_inactive_applications_logs_warning_on_operational_error(caplog):
    """OperationalError during application tombstone must be logged as a warning."""
    import logging
    from unittest.mock import AsyncMock
    from sqlalchemy.exc import OperationalError
    from applire.retention.worker import _tombstone_inactive_applications

    mock_db = AsyncMock()
    mock_db.execute.side_effect = OperationalError("stmt", {}, Exception("connection lost"))
    mock_db.rollback = AsyncMock()

    with caplog.at_level(logging.WARNING, logger="applire.retention.worker"):
        result = await _tombstone_inactive_applications(mock_db)

    assert result == 0
    assert any(rec.levelname == "WARNING" for rec in caplog.records)


@pytest.mark.asyncio
async def test_purge_import_jobs_binds_a_datetime_not_a_string():
    """Regression: the expires_at cutoff must be bound as a datetime, not an
    ISO string. asyncpg infers the bind type from the timestamptz column and
    rejects a str ('expected a datetime.date or datetime.datetime instance, got
    str') — crashing the retention worker on Postgres. SQLite accepted the string
    (lexical TEXT compare), which is why this hid from the other unit tests.
    """
    from datetime import datetime
    from unittest.mock import AsyncMock
    from applire.retention.worker import _purge_import_jobs

    mock_db = AsyncMock()

    await _purge_import_jobs(mock_db)

    _clause, params = mock_db.execute.call_args.args
    assert isinstance(params["now"], datetime), (
        f"expires_at cutoff must be a datetime for asyncpg, got {type(params['now'])}"
    )


@pytest.mark.asyncio
async def test_purge_gap_jobs_binds_a_datetime_not_a_string():
    """Regression: same as the import-jobs purge — bind a datetime, not an ISO
    string, so asyncpg accepts the timestamptz comparison on Postgres."""
    from datetime import datetime
    from unittest.mock import AsyncMock
    from applire.retention.worker import _purge_gap_jobs

    mock_db = AsyncMock()

    await _purge_gap_jobs(mock_db)

    _clause, params = mock_db.execute.call_args.args
    assert isinstance(params["now"], datetime), (
        f"expires_at cutoff must be a datetime for asyncpg, got {type(params['now'])}"
    )


# ---------------------------------------------------------------------------
# E039/US219 — submitted-pin retention guard (ADR-005 amendment 2026-07-06)
# A generated document pinned as submitted on an ACTIVE application is exempt
# from the TTL purge; once the application is tombstoned the pin no longer
# protects it and the document re-enters the normal purge.
# ---------------------------------------------------------------------------


async def _seed_cover_letter(db: AsyncSession, *, expires_at: datetime, deleted_at: datetime | None = None) -> str:
    cid = _uid()
    await db.execute(
        text(
            "INSERT INTO generated_cover_letters "
            "(id, job_analysis_id, profile_id, letter_data, created_at, expires_at, deleted_at) "
            "VALUES (:id, :jid, :pid, '{}', :now, :exp, :del)"
        ),
        {"id": cid, "jid": _uid(), "pid": _uid(),
         "now": _ts(_now()), "exp": _ts(expires_at),
         "del": _ts(deleted_at) if deleted_at else None},
    )
    await db.commit()
    return cid


async def _seed_application(
    db: AsyncSession,
    *,
    submitted_cv_id: str | None = None,
    submitted_cover_letter_id: str | None = None,
    deleted_at: datetime | None = None,
) -> str:
    aid = _uid()
    await db.execute(
        text(
            "INSERT INTO applications "
            "(id, user_id, job_analysis_id, workflow_status, user_status, "
            " submitted_cv_id, submitted_cover_letter_id, created_at, updated_at, expires_at, deleted_at) "
            "VALUES (:id, :uid, :jid, 'none', 'applied', :cv, :cl, :now, :now, :exp, :del)"
        ),
        {"id": aid, "uid": _uid(), "jid": _uid(),
         "cv": submitted_cv_id, "cl": submitted_cover_letter_id,
         "now": _ts(_now()), "exp": _ts(_now() + timedelta(days=700)),
         "del": _ts(deleted_at) if deleted_at else None},
    )
    await db.commit()
    return aid


@pytest.mark.asyncio
async def test_purge_cvs_spares_pinned_on_active_application(db):
    from applire.retention.worker import _purge_cvs

    pinned = await _seed_cv(db, expires_at=_ago(days=1))
    await _seed_application(db, submitted_cv_id=pinned)

    deleted = await _purge_cvs(db)
    assert deleted == 0

    row = (await db.execute(
        text("SELECT COUNT(*) FROM generated_cvs WHERE id = :id"), {"id": pinned}
    )).one()
    assert row[0] == 1


@pytest.mark.asyncio
async def test_purge_cvs_deletes_pinned_on_tombstoned_application(db):
    """The pin follows the application lifecycle: tombstoned app → pin no longer protects.

    The worker must RELEASE the tombstoned application's pin before deleting —
    on Postgres the FK (applications.submitted_cv_id → generated_cvs.id) makes
    the DELETE crash otherwise (found on the live stack; SQLite doesn't enforce
    FKs, so only the released-pin effect is assertable here)."""
    from applire.retention.worker import _purge_cvs

    pinned = await _seed_cv(db, expires_at=_ago(days=1))
    app_id = await _seed_application(db, submitted_cv_id=pinned, deleted_at=_ago(days=2))

    deleted = await _purge_cvs(db)
    assert deleted == 1

    row = (await db.execute(
        text("SELECT submitted_cv_id FROM applications WHERE id = :id"), {"id": app_id}
    )).one()
    assert row[0] is None, "tombstoned application's pin must be released before the purge"


@pytest.mark.asyncio
async def test_purge_cvs_unpinned_normal_ttl_with_applications_present(db):
    """Unpinned expired CVs still purge normally even when active applications exist."""
    from applire.retention.worker import _purge_cvs

    await _seed_cv(db, expires_at=_ago(days=1))
    await _seed_application(db)  # active, but pins nothing

    deleted = await _purge_cvs(db)
    assert deleted == 1


@pytest.mark.asyncio
async def test_purge_cover_letters_spares_pinned_on_active_application(db):
    from applire.retention.worker import _purge_cover_letters

    pinned = await _seed_cover_letter(db, expires_at=_ago(days=1))
    await _seed_application(db, submitted_cover_letter_id=pinned)

    deleted = await _purge_cover_letters(db)
    assert deleted == 0


@pytest.mark.asyncio
async def test_purge_cover_letters_deletes_pinned_on_tombstoned_application(db):
    from applire.retention.worker import _purge_cover_letters

    pinned = await _seed_cover_letter(db, expires_at=_ago(days=1))
    app_id = await _seed_application(db, submitted_cover_letter_id=pinned, deleted_at=_ago(days=2))

    deleted = await _purge_cover_letters(db)
    assert deleted == 1

    row = (await db.execute(
        text("SELECT submitted_cover_letter_id FROM applications WHERE id = :id"), {"id": app_id}
    )).one()
    assert row[0] is None, "tombstoned application's pin must be released before the purge"


@pytest.mark.asyncio
async def test_purge_cover_letters_deletes_expired_unpinned(db):
    from applire.retention.worker import _purge_cover_letters

    await _seed_cover_letter(db, expires_at=_ago(days=1))
    await _seed_cover_letter(db, expires_at=_now() + timedelta(days=30))

    deleted = await _purge_cover_letters(db)
    assert deleted == 1


@pytest.mark.asyncio
async def test_count_submitted_exempt_counts_both_artifact_kinds(db):
    """The worker report's submitted_exempt = expired-but-pinned rows (CVs + cover
    letters) protected by an active application this run (ADR-005 auditability)."""
    from applire.retention.worker import _count_submitted_exempt

    pinned_cv = await _seed_cv(db, expires_at=_ago(days=1))
    await _seed_application(db, submitted_cv_id=pinned_cv)
    pinned_cl = await _seed_cover_letter(db, expires_at=_ago(days=1))
    await _seed_application(db, submitted_cover_letter_id=pinned_cl)

    # Not exempt: unexpired pin (clock not up), expired-unpinned, tombstoned app's pin.
    fresh_cv = await _seed_cv(db, expires_at=_now() + timedelta(days=30))
    await _seed_application(db, submitted_cv_id=fresh_cv)
    await _seed_cv(db, expires_at=_ago(days=1))
    dead_pin = await _seed_cv(db, expires_at=_ago(days=1))
    await _seed_application(db, submitted_cv_id=dead_pin, deleted_at=_ago(days=2))

    assert await _count_submitted_exempt(db) == 2


@pytest.mark.asyncio
async def test_count_submitted_exempt_zero_when_tables_absent(db):
    from applire.retention.worker import _count_submitted_exempt

    await db.execute(text("DROP TABLE applications"))
    await db.commit()
    assert await _count_submitted_exempt(db) == 0
