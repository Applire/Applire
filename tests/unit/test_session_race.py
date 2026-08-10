# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""
create_session race-safety tests (chocolate UAT-fix sprint).

The interview page's init effect double-fires in React dev StrictMode; the
old check-then-create idempotency let both requests pass the active-session
check, producing duplicate active sessions, duplicated LLM spend, and a
MultipleResultsFound 500 on every later call.

Covers:
  - unique partial index: one active session per job
  - create_session recovers from a lost insert race by returning the winner
  - _get_active_session tolerates legacy duplicates (pre-migration rows)
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile  # noqa: E402


@pytest_asyncio.fixture
async def sqlite_session():
    """In-memory SQLite async session — no Docker required."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _make_job(**kwargs):
    from applire.models.job import JobAnalysis

    defaults = dict(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Senior Python Engineer role requiring GCP and FastAPI.",
        role_title="Senior Python Engineer",
        required_skills=["Python", "GCP", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="English",
    )
    defaults.update(kwargs)
    return JobAnalysis(**defaults)


def _make_profile():
    return make_master_profile(
        profile_json={
            "personal_info": {"name": "Anna Bauer", "email": "anna@example.de"},
            "skills": [{"name": "Python", "category": "technical", "proficiency": "advanced"}],
            "work_experience": [{"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}],
        }
    )


def _make_gap(job_id, profile_id):
    from applire.models.gap import GapAnalysis

    return GapAnalysis(
        job_analysis_id=job_id,
        profile_id=profile_id,
        match_score=0.6,
        critical_gaps=["GCP certification"],
        minor_gaps=[],
        strengths=["Python"],
        keyword_gaps=[],
        category_a=[],
        category_b=[],
        category_c=["GCP certification"],
        gap_clusters=[
            {
                "id": "cluster-gcp",
                "label": "GCP certification",
                "category": "C",
                "gaps": ["GCP certification"],
                "jd_skills": [],
                "jd_context": "",
            }
        ],
    )


def _make_session_record(job_id, profile_id, status="active", created_at=None):
    from applire.models.session import InterviewSession

    now = created_at or datetime.now(timezone.utc)
    return InterviewSession(
        job_analysis_id=job_id,
        gap_analysis_id=None,
        profile_id=profile_id,
        mode="targeted",
        status=status,
        state={
            "mode": "targeted",
            "job_id": str(job_id),
            "profile_id": str(profile_id),
            "critical_gaps": ["cluster-gcp"],
            "gap_categories": {"cluster-gcp": "C"},
            "gap_clusters_by_id": {},
            "current_gap_index": 0,
            "current_question": "Wie lange GCP?",
            "messages": [],
            "questions_asked": 1,
        },
        questions_asked=1,
        hard_ceiling=12,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(days=30),
    )


def _mock_provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="Wie lange Python?")
    return provider


class TestUniqueActiveSessionIndex:
    @pytest.mark.asyncio
    async def test_second_active_session_for_same_job_is_rejected(self, sqlite_session):
        job = _make_job()
        profile = _make_profile()
        sqlite_session.add_all([job, profile])
        await sqlite_session.flush()

        sqlite_session.add(_make_session_record(job.id, profile.id))
        await sqlite_session.commit()

        sqlite_session.add(_make_session_record(job.id, profile.id))
        with pytest.raises(IntegrityError):
            await sqlite_session.commit()
        await sqlite_session.rollback()

    @pytest.mark.asyncio
    async def test_completed_sessions_do_not_block_new_active_one(self, sqlite_session):
        job = _make_job()
        profile = _make_profile()
        sqlite_session.add_all([job, profile])
        await sqlite_session.flush()

        sqlite_session.add(_make_session_record(job.id, profile.id, status="complete"))
        sqlite_session.add(_make_session_record(job.id, profile.id, status="complete"))
        await sqlite_session.commit()

        sqlite_session.add(_make_session_record(job.id, profile.id, status="active"))
        await sqlite_session.commit()


class TestCreateSessionRace:
    @pytest.mark.asyncio
    async def test_lost_race_returns_winner_session(self, sqlite_session):
        """Both StrictMode requests pass the active-session check; the loser's
        insert must surface the winner's session instead of raising 500.  The
        winner is freshly created (the user has answered nothing yet), so it is
        NOT reported as a resume — no "Willkommen zurück" banner (issue #44)."""
        from applire.services import session as session_service
        from applire.schemas.session import SessionCreateRequest

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add_all([job, profile])
        await sqlite_session.flush()
        gap = _make_gap(job.id, profile.id)
        sqlite_session.add(gap)
        await sqlite_session.flush()

        winner = _make_session_record(job.id, profile.id)
        sqlite_session.add(winner)
        await sqlite_session.commit()

        # Simulate the check running before the rival commit landed:
        # first idempotency lookup sees nothing, recovery lookup sees the winner.
        real_lookup = session_service._get_active_session
        calls = {"n": 0}

        async def racy_lookup(job_id, db):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return await real_lookup(job_id, db)

        req = SessionCreateRequest(job_id=job.id, mode="targeted")
        with patch.object(session_service, "_get_active_session", racy_lookup), patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "Tell me about GCP.", "choices": None}),
        ):
            result = await session_service.create_session(
                req, sqlite_session, _mock_provider()
            )

        assert result.session_id == winner.id
        assert result.resumed is False


class TestLegacyDuplicateTolerance:
    @pytest.mark.asyncio
    async def test_get_active_session_returns_newest_duplicate(self, sqlite_session):
        """Pre-migration databases may hold duplicate active sessions; the
        lookup must return the newest instead of raising MultipleResultsFound."""
        from applire.services.session import _get_active_session

        # Drop the unique index to recreate the legacy state.
        await sqlite_session.execute(
            text("DROP INDEX uq_interview_sessions_active_per_job")
        )

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add_all([job, profile])
        await sqlite_session.flush()

        older = _make_session_record(
            job.id, profile.id, created_at=datetime(2026, 6, 1, tzinfo=timezone.utc)
        )
        newer = _make_session_record(
            job.id, profile.id, created_at=datetime(2026, 6, 10, tzinfo=timezone.utc)
        )
        sqlite_session.add_all([older, newer])
        await sqlite_session.commit()

        result = await _get_active_session(job.id, sqlite_session)
        assert result is not None
        assert result.id == newer.id
