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

"""Issue #74 (finding F6) — the Mode-C profile-enrichment interview must resume.

The enrich launch (`POST /api/profile/enrich/start`) is the no-JD completion
interview (`mode='profile_enrich'`, ``job_analysis_id IS NULL``). It does NOT go
through the Flow Orchestrator's ``_complete_session`` machinery that PR #69 fixed
for the JD-gap interview (Mode A), so it had no resume guard at all: leaving the
interview mid-way and launching it again minted a *second* active
``profile_enrich`` session and re-asked Q1, leaving orphaned duplicate sessions
for one profile.

These tests pin the contract: at most one active Mode-C session per profile —
a second launch resumes the in-flight one; a fresh one is only created when none
is open.
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


@pytest_asyncio.fixture
async def sqlite_session():
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
    import applire.models.uploads  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _make_profile():
    from applire.models.profile import MasterProfile

    return MasterProfile(profile_json={
        "personal_info": {"name": "Sven Hartmann", "email": "sven@example.de"},
        "skills": [{"name": "Python", "category": "technical", "proficiency": "advanced"}],
        "work_experience": [{"company": "Logivia", "role": "Engineer", "start_date": "2020-01"}],
        "education": [{"institution": "TU", "degree": "MSc", "field": "CS"}],
        "professional_summary": {"en": "Experienced engineer"},
    })


def _mock_provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="(should not be called)")
    provider.aparse_json = AsyncMock(return_value={})
    provider.__class__.__name__ = "MockProvider"
    return provider


@pytest.fixture(autouse=True)
def _stub_gap_and_question(monkeypatch):
    """Make enrich launch deterministic — no LLM, fixed gap set + question."""
    import applire.routers.profile_enrich as pe

    monkeypatch.setattr(
        pe,
        "gap_detector_mode_c",
        lambda profile_data, scope=None: [
            "budget_managed: Senior Engineer @ Logivia",
            "team_size: Senior Engineer @ Logivia",
        ],
    )

    async def _fake_question(state, profile_data, provider, lang="en"):
        gap = state["critical_gaps"][state["current_gap_index"]]
        return {"question": f"Tell me about {gap}"}

    monkeypatch.setattr(pe, "question_generator_with_profile", _fake_question)


def _count_active_enrich(rows):
    return [
        r for r in rows
        if r.mode == "profile_enrich" and r.status == "active"
    ]


class TestEnrichSessionResume:
    @pytest.mark.asyncio
    async def test_relaunch_returns_the_same_active_session(self, sqlite_session):
        from applire.routers.profile_enrich import start_enrich_session
        from applire.schemas.enrich import EnrichStartRequest
        from applire.models.session import InterviewSession

        prof = _make_profile()
        sqlite_session.add(prof)
        await sqlite_session.commit()
        provider = _mock_provider()

        first = await start_enrich_session(
            EnrichStartRequest(), sqlite_session, provider, None
        )
        second = await start_enrich_session(
            EnrichStartRequest(), sqlite_session, provider, None
        )

        # Resume, do not duplicate: same session id back, one active row only.
        assert second.session_id == first.session_id
        rows = (await sqlite_session.execute(select(InterviewSession))).scalars().all()
        assert len(_count_active_enrich(rows)) == 1

    @pytest.mark.asyncio
    async def test_relaunch_preserves_in_progress_state(self, sqlite_session):
        """The resumed session keeps its progress — it does not reset to Q1."""
        from applire.routers.profile_enrich import (
            start_enrich_session,
            respond_to_enrich,
        )
        from applire.schemas.enrich import EnrichStartRequest, EnrichRespondRequest
        from applire.models.session import InterviewSession

        prof = _make_profile()
        sqlite_session.add(prof)
        await sqlite_session.commit()
        provider = _mock_provider()

        first = await start_enrich_session(
            EnrichStartRequest(), sqlite_session, provider, None
        )
        # Answer one question so the session is mid-flight.
        await respond_to_enrich(
            first.session_id,
            EnrichRespondRequest(answer="I managed a 250k budget"),
            sqlite_session,
            provider,
            None,
        )
        rec = await sqlite_session.get(InterviewSession, first.session_id)
        advanced_index = rec.state["current_gap_index"]
        assert advanced_index > 0  # we actually moved forward

        # Relaunch — must resume the same session at its advanced position.
        resumed = await start_enrich_session(
            EnrichStartRequest(), sqlite_session, provider, None
        )
        assert resumed.session_id == first.session_id
        rec2 = await sqlite_session.get(InterviewSession, first.session_id)
        assert rec2.state["current_gap_index"] == advanced_index

    @pytest.mark.asyncio
    async def test_respond_surfaces_pending_confirmation(self, sqlite_session, monkeypatch):
        """An ambiguous reconcile turn surfaces a confirmation in the enrich drawer (US185)."""
        from applire.routers.profile_enrich import (
            start_enrich_session,
            respond_to_enrich,
        )
        from applire.schemas.enrich import EnrichStartRequest, EnrichRespondRequest
        from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult
        from applire.services.profile.reconcile.ops import RequestConfirmation
        import applire.routers.profile_enrich as pe

        prof = _make_profile()
        sqlite_session.add(prof)
        await sqlite_session.commit()
        provider = _mock_provider()

        first = await start_enrich_session(
            EnrichStartRequest(), sqlite_session, provider, None
        )

        confirmation = RequestConfirmation(
            question="Is 'Owner at applire' the same as your 'Founder & Lead Developer' role?",
            options=["Yes, same role", "No, separate roles"],
            context={"existing": "Founder & Lead Developer"},
        )

        async def _confirming(**kwargs):
            return InterviewTurnResult(
                profile_dict=prof.profile_json,
                changes=[],
                addressed=False,
                pending_confirmations=[confirmation],
            )

        monkeypatch.setattr(pe, "reconcile_interview_turn", _confirming)

        resp = await respond_to_enrich(
            first.session_id,
            EnrichRespondRequest(answer="I'm the Owner at applire"),
            sqlite_session,
            provider,
            None,
        )

        assert resp.pending_confirmations is not None
        assert len(resp.pending_confirmations) == 1
        assert resp.pending_confirmations[0].question == confirmation.question
        assert resp.pending_confirmations[0].options == ["Yes, same role", "No, separate roles"]
        # The confirmation is the prompt the user sees next, not the auto-generated gap question.
        assert resp.next_question == confirmation.question
        assert resp.done is False

    @pytest.mark.asyncio
    async def test_fresh_session_when_none_open(self, sqlite_session):
        """A brand-new session IS created when nothing is in flight."""
        from applire.routers.profile_enrich import start_enrich_session
        from applire.schemas.enrich import EnrichStartRequest
        from applire.models.session import InterviewSession

        prof = _make_profile()
        sqlite_session.add(prof)
        await sqlite_session.commit()
        provider = _mock_provider()

        resp = await start_enrich_session(
            EnrichStartRequest(), sqlite_session, provider, None
        )

        rows = (await sqlite_session.execute(select(InterviewSession))).scalars().all()
        assert len(_count_active_enrich(rows)) == 1
        assert resp.session_id is not None
        assert resp.first_question

    @pytest.mark.asyncio
    async def test_completed_session_does_not_block_a_new_launch(self, sqlite_session):
        """A *complete* Mode-C session must not be resumed — start a fresh one."""
        from applire.routers.profile_enrich import start_enrich_session
        from applire.schemas.enrich import EnrichStartRequest
        from applire.models.session import InterviewSession

        prof = _make_profile()
        sqlite_session.add(prof)
        await sqlite_session.flush()
        sqlite_session.add(InterviewSession(
            job_analysis_id=None,
            profile_id=prof.id,
            mode="profile_enrich",
            status="complete",
            state={"mode": "profile_enrich", "critical_gaps": []},
            hard_ceiling=6,
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        ))
        await sqlite_session.commit()
        provider = _mock_provider()

        resp = await start_enrich_session(
            EnrichStartRequest(), sqlite_session, provider, None
        )

        rows = (await sqlite_session.execute(select(InterviewSession))).scalars().all()
        active = _count_active_enrich(rows)
        assert len(active) == 1
        assert resp.session_id == active[0].id
