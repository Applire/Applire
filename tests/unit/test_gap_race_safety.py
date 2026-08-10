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

"""Spaghettieis UAT follow-up-flow fix — gap-analysis race safety.

Two simultaneous kick-offs (7 ms apart in the reproduced incident) slipped past
create_gap_job's check-then-insert dedup, ran the full LLM analysis twice, and
inserted two gap_analyses rows with IDENTICAL input fingerprints. The gaps page
then read the second row mid-clustering (gap_clusters=[]) and hung on
"Analyzing your profile…" forever, with the two rows also disagreeing on the
match score (14% vs 21% — the E037 PQ #3 wobble, resurrected).

Safety net under test:
1. A partial unique index rejects a second NON-TERMINAL gap job for the same
   job_analysis_id; create_gap_job recovers from the lost race by returning the
   winner's job.
2. gap_analyses rejects a second live row with the same (job, fingerprint);
   analyze_gaps recovers by returning the winner's row.
3. _run_analysis publishes atomically: a committed row ALWAYS carries its
   clusters; a clustering failure persists nothing (the job fails cleanly and
   is retryable) instead of stranding a half-built row.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.gap_job import GapAnalysisJob, GapJobStatus
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.models.user import User
from applire.providers.llm.mock import MockLLMProvider
from applire.services.gap import analyze_gaps
from applire.services.gap_jobs import create_gap_job
from tests.support.profile_factory import make_master_profile

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


@pytest_asyncio.fixture
async def factory():
    from applire.db.session import Base
    import applire.models.user           # noqa: F401
    import applire.models.job            # noqa: F401
    import applire.models.profile        # noqa: F401
    import applire.models.gap            # noqa: F401
    import applire.models.gap_job        # noqa: F401
    import applire.models.cv             # noqa: F401
    import applire.models.cover_letter   # noqa: F401
    import applire.models.session        # noqa: F401
    import applire.models.flow           # noqa: F401
    import applire.models.application    # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company        # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads        # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, expire_on_commit=False)
    yield f
    await engine.dispose()


@pytest_asyncio.fixture
async def db(factory):
    async with factory() as session:
        yield session


def _profile_json() -> dict:
    return {
        "work_experience": [
            {"company": "Acme", "role": "Dev", "start_date": "2018-01"}
        ],
        "education": [
            {"institution": "TU Berlin", "degree": "BSc", "field": "CS"}
        ],
        "skills": [
            {"name": "Python", "category": "technical", "proficiency": "expert"}
        ],
        "languages": [{"language": "German", "level": "native"}],
        "personal_info": {
            "first_name": "Max",
            "last_name": "Muster",
            "email": "max@test.de",
        },
        "professional_summary": {"de": "Entwickler", "en": "Developer"},
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
    }


@pytest_asyncio.fixture
async def seeded(db):
    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="hash-race",
        raw_text="Senior Python Engineer",
        role_title="Senior Python Engineer",
        required_skills=["Python", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )
    profile = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    db.add_all([user, job, profile])
    await db.commit()
    return job, profile


# ---------------------------------------------------------------------------
# 1. Gap-job kickoff dedup must survive the check-then-insert race
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_nonterminal_gap_job_for_same_job_is_rejected(db, seeded):
    """The DB itself must reject a duplicate live kickoff — 7 ms apart both
    POSTs passed the SELECT check in the reproduced incident."""
    job, _ = seeded
    db.add(GapAnalysisJob(job_analysis_id=job.id, user_id=_STUB_USER_ID))
    await db.commit()

    db.add(GapAnalysisJob(job_analysis_id=job.id, user_id=_STUB_USER_ID))
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_terminal_job_does_not_block_a_new_kickoff(db, seeded):
    """Only NON-terminal jobs dedup — a finished analysis must not block a
    later legitimate re-kickoff (e.g. after a profile change)."""
    job, _ = seeded
    done = GapAnalysisJob(
        job_analysis_id=job.id,
        user_id=_STUB_USER_ID,
        status=GapJobStatus.ready.value,
    )
    db.add(done)
    await db.commit()

    fresh = await create_gap_job(db, job_analysis_id=job.id, user_id=_STUB_USER_ID)
    assert fresh.id != done.id
    assert fresh.status == GapJobStatus.pending.value


@pytest.mark.asyncio
async def test_create_gap_job_returns_winner_when_race_lost(db, seeded):
    """Simulate the lost race: the pre-check sees nothing (the other request's
    insert lands in between), our insert hits the constraint, and create_gap_job
    must recover by returning the winner's job instead of raising."""
    job, _ = seeded
    winner = GapAnalysisJob(job_analysis_id=job.id, user_id=_STUB_USER_ID)
    db.add(winner)
    await db.commit()
    winner_id = winner.id

    from applire.services import gap_jobs as gap_jobs_module

    real_find = gap_jobs_module._find_nonterminal_job
    calls = {"n": 0}

    async def racy_find(session, job_analysis_id):
        # First call = the race window (the winner's insert lands after our
        # check); later calls behave normally, as the recovery re-select does.
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_find(session, job_analysis_id)

    # Rollback (inside the lost race) expires ORM instances — pin the id now.
    job_id = job.id
    with patch(
        "applire.services.gap_jobs._find_nonterminal_job", new=racy_find
    ):
        result = await create_gap_job(
            db, job_analysis_id=job_id, user_id=_STUB_USER_ID
        )

    assert result.id == winner_id
    live = (
        await db.execute(
            select(GapAnalysisJob).where(
                GapAnalysisJob.job_analysis_id == job_id,
                GapAnalysisJob.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    assert len(live) == 1, "the lost race must not leave a duplicate job behind"


# ---------------------------------------------------------------------------
# 2. gap_analyses fingerprint uniqueness must survive the same race
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_live_row_with_same_fingerprint_is_rejected(db, seeded):
    job, profile = seeded
    db.add(
        GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=0.5,
            input_fingerprint="fp-1",
            critical_gaps=[],
            minor_gaps=[],
            strengths=[],
            keyword_gaps=[],
        )
    )
    await db.commit()

    db.add(
        GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=0.6,
            input_fingerprint="fp-1",
            critical_gaps=[],
            minor_gaps=[],
            strengths=[],
            keyword_gaps=[],
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
    await db.rollback()


@pytest.mark.asyncio
async def test_analyze_gaps_recovers_from_fingerprint_race(db, seeded):
    """When the idempotency pre-check loses the race, the constraint fires and
    analyze_gaps must return the winner's row — one row, one score."""
    job, profile = seeded
    provider = MockLLMProvider()
    # Rollback (inside the lost race) expires ORM instances — pin the id now.
    job_id = job.id

    first = await analyze_gaps(job_id, db, provider)

    # Simulate the race window: the pre-check reports "no previous row" even
    # though the winner's row is committed.
    with patch(
        "applire.services.gap._latest_gap_analysis",
        new=AsyncMock(return_value=None),
    ):
        second = await analyze_gaps(job_id, db, provider)

    assert second.id == first.id, "the loser must adopt the winner's row"
    rows = (
        await db.execute(
            select(GapAnalysis).where(
                GapAnalysis.job_analysis_id == job_id,
                GapAnalysis.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    assert len(rows) == 1, "no duplicate analysis row may survive the race"


# ---------------------------------------------------------------------------
# 3. Atomic publish: a committed row always carries its clusters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_committed_analysis_row_always_has_clusters(db, seeded):
    """The incident: GET /gaps read a committed row whose clustering was still
    in flight (gap_clusters=[]) and the page hung on "Analyzing…" forever.
    Classification + clustering must land in ONE commit."""
    job, _ = seeded

    commits_seen: list[bool] = []
    real_commit = db.commit

    async def spy_commit():
        await real_commit()
        rows = (
            await db.execute(
                select(GapAnalysis).where(GapAnalysis.job_analysis_id == job.id)
            )
        ).scalars().all()
        commits_seen.extend(bool(r.gap_clusters) for r in rows)

    with patch.object(db, "commit", side_effect=spy_commit):
        result = await analyze_gaps(job.id, db, MockLLMProvider())

    assert result.gap_clusters, "the mock clustering chain returns clusters"
    assert commits_seen, "at least one commit must have persisted the row"
    assert all(commits_seen), (
        "a gap_analyses row was visible to readers without its clusters — "
        "the 'Analyzing your profile…' hang"
    )


@pytest.mark.asyncio
async def test_clustering_failure_persists_no_row(db, seeded):
    """If clustering dies, nothing may be published: the async job fails with a
    stable code and a retry recomputes cleanly — no half-built row to read."""
    job, _ = seeded

    class _ClusterExploder(MockLLMProvider):
        async def aparse_json(self, prompt, system: str = "", **kwargs):
            if "group semantically related" in system:
                raise RuntimeError("clustering boom")
            return await super().aparse_json(prompt, system=system, **kwargs)

    with pytest.raises(RuntimeError):
        await analyze_gaps(job.id, db, _ClusterExploder())

    rows = (
        await db.execute(
            select(GapAnalysis).where(GapAnalysis.job_analysis_id == job.id)
        )
    ).scalars().all()
    assert rows == [], "a failed analysis must not leave a partial row behind"
