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

"""#240 — interview completion must trigger a gap-analysis/match recompute.

Founder-acceptance F7: an interview that closes all gap clusters (64% → 96%
profile completeness) left the match ring on the gaps page and CV workspace
showing the stale pre-interview score, because interview completion
(services/session.py _complete_session) never called analyze_gaps — the
FlowSession.gap_analysis_id FK stayed pointed at the pre-interview row
forever.

These tests drive the REAL analyze_gaps() with the REAL MockLLMProvider
(not a hand-rolled MagicMock) — mocks-only tests of this composition would
prove nothing (2026-07-22 resolve_gap review finding).
"""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# ---------------------------------------------------------------------------
# SQLite fixture — full model set (mirrors test_flow_orchestrator.py / db())
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000042")


def _mock_provider(question="What is your GCP experience?"):
    """A pure-argument-wiring stub — used only where the LLM shape does not
    matter (e.g. the recompute-failure test). Real recompute assertions use
    MockLLMProvider (see _real_provider below)."""
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value=question)
    provider.aparse_json = AsyncMock(return_value={
        "question": question, "choices": None, "approved": True,
    })
    provider.__class__.__name__ = "MockProvider"
    return provider


def _real_provider():
    from applire.providers.llm.mock import MockLLMProvider
    return MockLLMProvider()


async def _seed(db, *, with_flow=True, old_match_score=0.40, old_fingerprint="fp-pre-interview"):
    """Job + profile + a stale pre-interview GapAnalysis (+ owning FlowSession
    pointed at it) + an active targeted InterviewSession with ONE remaining
    critical gap, so the next answer closes the last gap cluster."""
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.gap import GapAnalysis
    from applire.models.session import InterviewSession
    from applire.models.flow import FlowSession
    from applire.models.user import User

    job = JobAnalysis(
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
    profile = MasterProfile(profile_json={
        "personal_info": {"name": "Anna Bauer", "email": "anna@example.de"},
        "skills": [{"name": "Python", "category": "technical", "proficiency": "advanced"}],
        "work_experience": [{"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}],
    })
    db.add_all([job, profile])
    await db.flush()

    old_gap = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        match_score=old_match_score,
        input_fingerprint=old_fingerprint,
        critical_gaps=["GCP certification"],
        minor_gaps=[],
        strengths=["Python"],
        keyword_gaps=[],
        category_a=[],
        category_b=[],
        category_c=["GCP certification"],
        gap_clusters=[{
            "id": "cluster-gcp", "label": "GCP certification", "category": "C",
            "gaps": ["GCP certification"], "jd_skills": ["GCP"], "jd_context": "",
        }],
    )
    db.add(old_gap)
    await db.flush()

    flow = None
    if with_flow:
        user = User(id=_STUB_USER_ID, email="local@applire.community")
        db.add(user)
        await db.flush()
        flow = FlowSession(
            user_id=_STUB_USER_ID,
            job_id=job.id,
            current_step="interview",
            user_type="new",
            available_actions={"next": "cv_generation"},
            gap_analysis_id=old_gap.id,
        )
        db.add(flow)

    session_record = InterviewSession(
        job_analysis_id=job.id,
        gap_analysis_id=old_gap.id,
        profile_id=profile.id,
        mode="targeted",
        status="active",
        state={
            "mode": "targeted",
            "job_id": str(job.id),
            "gap_analysis_id": str(old_gap.id),
            "profile_id": str(profile.id),
            "critical_gaps": ["GCP certification"],
            "gap_categories": {"GCP certification": "C"},
            "addressed_gaps": [],
            "current_gap_index": 0,
            "current_question": "Tell me about your GCP experience.",
            "messages": [{"role": "assistant", "content": "Tell me about your GCP experience."}],
            "questions_asked": 1,
            "hard_ceiling": 12,
            "questions_per_gap": {},
            "skipped_gaps": [],
            "full_gaps": [],
        },
        hard_ceiling=12,
        questions_asked=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(session_record)
    await db.commit()
    await db.refresh(job)
    await db.refresh(profile)
    await db.refresh(old_gap)
    await db.refresh(session_record)
    if flow is not None:
        await db.refresh(flow)
    return job, profile, old_gap, session_record, flow


def _addressed_turn(profile_dict):
    """A turn that mutates the profile (so the recompute fingerprint differs
    from the stale pre-interview row)."""
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    new_profile = dict(profile_dict)
    new_profile["skills"] = list(profile_dict.get("skills", [])) + [
        {"name": "GCP", "category": "technical", "proficiency": "advanced"}
    ]
    return InterviewTurnResult(
        profile_dict=new_profile,
        changes=[FieldChange(section="skills", field="GCP", action="added", new_value="GCP")],
        addressed=True,
        conflict_summaries=[],
    )


# ===========================================================================
# 1. gaps_resolved completion triggers a real recompute + repoint
# ===========================================================================

class TestCompletionTriggersRecompute:
    @pytest.mark.asyncio
    async def test_gaps_resolved_completion_recomputes_and_repoints_flow(self, db):
        from applire.services.session import send_message
        from applire.models.gap import GapAnalysis
        from applire.models.flow import FlowSession

        job, profile, old_gap, session_record, flow = await _seed(db)
        turn = _addressed_turn(profile.profile_json)

        with patch(
            "applire.services.session.reconcile_interview_turn",
            new=AsyncMock(return_value=turn),
        ):
            result = await send_message(
                session_record.id,
                "I ran production GCP infrastructure for three years at Acme.",
                db,
                _real_provider(),
            )

        assert result.complete is True
        assert result.reason == "gaps_resolved"

        # A NEW gap_analyses row was created for the job (profile changed →
        # fingerprint differs → real recompute, not idempotent reuse).
        all_rows = (
            await db.execute(
                select(GapAnalysis).where(GapAnalysis.job_analysis_id == job.id)
            )
        ).scalars().all()
        assert len(all_rows) == 2
        new_row = next(r for r in all_rows if r.id != old_gap.id)
        assert new_row.input_fingerprint != old_gap.input_fingerprint

        # The owning flow's FK follows the recompute — no longer the stale row.
        flow_after = (
            await db.execute(select(FlowSession).where(FlowSession.id == flow.id))
        ).scalar_one()
        assert flow_after.gap_analysis_id == new_row.id
        assert flow_after.gap_analysis_id != old_gap.id

    @pytest.mark.asyncio
    async def test_user_ended_completion_also_recomputes(self, db):
        """The 'done' / user_ended completion path inherits the recompute too —
        not just the gaps_resolved happy path. Earlier turns (this test's
        setup stands in for them) already wrote a profile change straight to
        the DB, so by the time the user says 'done' the fingerprint has moved
        on from the pre-interview row — 'done' must still pick that up."""
        from applire.services.session import send_message
        from applire.models.gap import GapAnalysis
        from applire.models.flow import FlowSession

        job, profile, old_gap, session_record, flow = await _seed(db)
        profile.profile_json = {
            **profile.profile_json,
            "skills": list(profile.profile_json.get("skills", [])) + [
                {"name": "GCP", "category": "technical", "proficiency": "advanced"}
            ],
        }
        db.add(profile)
        await db.commit()

        result = await send_message(
            session_record.id, "done", db, _real_provider()
        )

        assert result.complete is True
        assert result.reason == "user_ended"

        all_rows = (
            await db.execute(
                select(GapAnalysis).where(GapAnalysis.job_analysis_id == job.id)
            )
        ).scalars().all()
        assert len(all_rows) == 2
        new_row = next(r for r in all_rows if r.id != old_gap.id)

        flow_after = (
            await db.execute(select(FlowSession).where(FlowSession.id == flow.id))
        ).scalar_one()
        assert flow_after.gap_analysis_id == new_row.id

    @pytest.mark.asyncio
    async def test_recompute_failure_does_not_break_completion(self, db):
        """A recompute error must NOT break interview completion — completion
        is the user's data; the score refresh is best-effort."""
        from applire.services.session import send_message

        job, profile, old_gap, session_record, flow = await _seed(db)
        turn = _addressed_turn(profile.profile_json)

        with (
            patch(
                "applire.services.session.reconcile_interview_turn",
                new=AsyncMock(return_value=turn),
            ),
            patch(
                "applire.services.session.analyze_gaps",
                new=AsyncMock(side_effect=RuntimeError("LLM exploded")),
            ),
        ):
            result = await send_message(
                session_record.id,
                "I ran production GCP infrastructure for three years at Acme.",
                db,
                _mock_provider(),
            )

        assert result.complete is True
        assert result.reason == "gaps_resolved"

        # The interview itself is still marked complete in the DB despite the
        # recompute blowing up.
        from applire.models.session import InterviewSession
        refreshed = (
            await db.execute(
                select(InterviewSession).where(InterviewSession.id == session_record.id)
            )
        ).scalar_one()
        assert refreshed.status == "complete"

    @pytest.mark.asyncio
    async def test_max_questions_reached_completion_also_recomputes(self, db):
        """The ceiling-hit branch (targeted micro-session, ceiling=1) is the
        one resolve_gap rides — it must recompute too."""
        from applire.services.session import send_message
        from applire.models.gap import GapAnalysis
        from applire.models.flow import FlowSession

        job, profile, old_gap, session_record, flow = await _seed(db)
        session_record.hard_ceiling = 1
        session_record.questions_asked = 0
        state = dict(session_record.state)
        state["hard_ceiling"] = 1
        state["questions_asked"] = 0
        session_record.state = state
        db.add(session_record)
        await db.commit()

        turn = _addressed_turn(profile.profile_json)
        with patch(
            "applire.services.session.reconcile_interview_turn",
            new=AsyncMock(return_value=turn),
        ):
            result = await send_message(
                session_record.id,
                "I ran production GCP infrastructure for three years at Acme.",
                db,
                _real_provider(),
            )

        assert result.complete is True
        assert result.reason == "max_questions_reached"

        all_rows = (
            await db.execute(
                select(GapAnalysis).where(GapAnalysis.job_analysis_id == job.id)
            )
        ).scalars().all()
        assert len(all_rows) == 2
        new_row = next(r for r in all_rows if r.id != old_gap.id)

        flow_after = (
            await db.execute(select(FlowSession).where(FlowSession.id == flow.id))
        ).scalar_one()
        assert flow_after.gap_analysis_id == new_row.id
