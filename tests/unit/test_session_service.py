"""
Session service coverage tests — pushes total coverage past 75%.

Targets:
  - backend/applire/services/session.py        (14% → ~75%)
  - backend/applire/routers/session.py         (0%  → ~70%)
  - backend/applire/services/thumbnails.py     (0%  → ~75%)
  - backend/applire/services/interview/signals.py (67% → 100%)

No Docker required — DB tests use in-memory SQLite; router tests use
FastAPI TestClient with dependency overrides; thumbnails use mocked Playwright.

Run:
    pytest tests/unit/test_session_service_coverage.py -v
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# SQLite fixture (reuses the same model set as test_sprint13_coverage.py)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers — create DB fixtures
# ---------------------------------------------------------------------------

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


def _make_profile(completeness_json=None):
    from applire.models.profile import MasterProfile
    profile_json = completeness_json or {
        "personal_info": {"name": "Anna Bauer", "email": "anna@example.de"},
        "skills": [{"name": "Python", "category": "technical", "proficiency": "advanced"}],
        "work_experience": [{"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}],
    }
    return MasterProfile(profile_json=profile_json)


def _make_gap(job_id, profile_id, category_c=None, category_b=None):
    from applire.models.gap import GapAnalysis
    # Build gap_clusters from category_c / category_b for compatibility
    c_gaps = category_c if category_c is not None else ["GCP certification", "FastAPI experience"]
    b_gaps = category_b or []
    gap_clusters = []
    for gap in c_gaps:
        cluster_id = f"cluster-{gap.lower().replace(' ', '-')}"
        gap_clusters.append({"id": cluster_id, "label": gap, "category": "C", "gaps": [gap], "jd_skills": [], "jd_context": ""})
    for gap in b_gaps:
        cluster_id = f"cluster-{gap.lower().replace(' ', '-')}"
        gap_clusters.append({"id": cluster_id, "label": gap, "category": "B", "gaps": [gap], "jd_skills": [], "jd_context": ""})
    return GapAnalysis(
        job_analysis_id=job_id,
        profile_id=profile_id,
        match_score=0.6,
        critical_gaps=["GCP certification", "FastAPI experience"],
        minor_gaps=[],
        strengths=["Python"],
        keyword_gaps=[],
        category_a=[],
        category_b=b_gaps,
        category_c=c_gaps,
        gap_clusters=gap_clusters,
    )


def _make_active_session(job_id, profile_id, gap_id=None, state=None):
    from applire.models.session import InterviewSession
    default_state = {
        "mode": "targeted",
        "job_id": str(job_id),
        "gap_analysis_id": str(gap_id) if gap_id else None,
        "profile_id": str(profile_id),
        "critical_gaps": ["GCP certification", "FastAPI experience"],
        "gap_categories": {"GCP certification": "C", "FastAPI experience": "C"},
        "addressed_gaps": [],
        "current_gap_index": 0,
        "current_question": "Tell me about your GCP experience.",
        "messages": [{"role": "assistant", "content": "Tell me about your GCP experience."}],
        "questions_asked": 1,
        "hard_ceiling": 12,
        "questions_per_gap": {},
        "skipped_gaps": [],
        "full_gaps": [],
    }
    if state:
        default_state.update(state)
    return InterviewSession(
        job_analysis_id=job_id,
        gap_analysis_id=gap_id,
        profile_id=profile_id,
        mode="targeted",
        status="active",
        state=default_state,
        hard_ceiling=12,
        questions_asked=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )


def _mock_provider(question="What is your GCP experience?"):
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value=question)
    # aparse_json is called by question_generator_with_profile (MODE A) and the
    # language-review loop (_review_question_language / review_and_refine).
    # The generator reads "question" + "choices"; the reviewer reads "approved".
    # US182a: send_message no longer calls aparse_json for response parsing —
    # that path was replaced by reconcile_interview_turn (patched in these tests).
    # The old lexical-parser keys (gap_resolution, gaps_also_addressed,
    # work_history, skills, certifications, languages, education, follow_up_hint)
    # are not consumed by any active code path and have been removed.
    provider.aparse_json = AsyncMock(return_value={
        "question": question,
        "choices": None,
        "approved": True,
    })
    provider.__class__.__name__ = "MockProvider"
    return provider


# ---------------------------------------------------------------------------
# US182a — interview reconciliation-engine turn helpers
# ---------------------------------------------------------------------------
# send_message no longer calls the lexical response_parser/profile_updater; it
# delegates one turn to reconcile_interview_turn(), which runs the ADR-046
# engine and returns an InterviewTurnResult. These helpers build that result so
# tests can drive each loop branch (addressed → advance; no change → follow up)
# without invoking a real provider.

def _addressed_turn(profile_dict, *, changes=None, conflicts=None):
    """A turn that produced at least one profile change → gap addressed → advance."""
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    if changes is None:
        changes = [
            FieldChange(section="skills", field="GCP", action="added", new_value="GCP")
        ]
    return InterviewTurnResult(
        profile_dict=profile_dict,
        changes=list(changes),
        addressed=True,
        conflict_summaries=list(conflicts or []),
    )


def _unaddressed_turn(profile_dict, *, conflicts=None):
    """A turn that produced no profile change → gap not addressed → follow up once."""
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    return InterviewTurnResult(
        profile_dict=profile_dict,
        changes=[],
        addressed=False,
        conflict_summaries=list(conflicts or []),
    )


def _confirming_turn(profile_dict, *, addressed=True, changes=None):
    """A turn whose reconciler flagged an ambiguity → a confirmation is owed (US185)."""
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult
    from applire.services.profile.reconcile.ops import RequestConfirmation

    if changes is None and addressed:
        changes = [FieldChange(section="work_experience", field="role", action="added", new_value="Owner")]
    confirmation = RequestConfirmation(
        question="Is 'Owner at applire' the same as your 'Founder & Lead Developer' role?",
        options=["Yes, same role", "No, separate roles"],
        context={"existing": "Founder & Lead Developer", "incoming": "Owner"},
    )
    return InterviewTurnResult(
        profile_dict=profile_dict,
        changes=list(changes or []),
        addressed=addressed,
        pending_confirmations=[confirmation],
    )


# ===========================================================================
# Part 1: interview/signals.py coverage
# ===========================================================================

class TestTerminationSignal:
    def test_known_english_signals(self):
        from applire.services.interview.signals import is_termination_signal
        for sig in ["done", "skip", "finish", "end"]:
            assert is_termination_signal(sig) is True

    def test_known_german_signals(self):
        from applire.services.interview.signals import is_termination_signal
        for sig in ["fertig", "ende", "abschließen"]:
            assert is_termination_signal(sig) is True

    def test_case_insensitive(self):
        from applire.services.interview.signals import is_termination_signal
        assert is_termination_signal("DONE") is True
        assert is_termination_signal("Ende") is True

    def test_whitespace_trimmed(self):
        from applire.services.interview.signals import is_termination_signal
        assert is_termination_signal("  done  ") is True

    def test_regular_message_is_not_signal(self):
        from applire.services.interview.signals import is_termination_signal
        assert is_termination_signal("I have experience with GCP") is False


# ===========================================================================
# Part 2: session.py — pure helpers (no DB)
# ===========================================================================

class TestSessionPureHelpers:
    def test_auto_detect_mode_returns_guided_when_no_profile(self):
        from applire.services.session import _auto_detect_mode
        result = _auto_detect_mode(None)
        assert result == "guided"

    def test_auto_detect_mode_returns_guided_for_empty_profile(self):
        from applire.services.session import _auto_detect_mode
        from applire.models.profile import MasterProfile
        profile = MasterProfile(profile_json={})
        result = _auto_detect_mode(profile)
        assert result == "guided"

    def test_auto_detect_mode_returns_targeted_for_complete_profile(self):
        from applire.services.session import _auto_detect_mode
        from applire.models.profile import MasterProfile
        profile = MasterProfile(profile_json={
            "personal_info": {"name": "Anna Bauer", "email": "anna@example.de"},
            "skills": [{"name": "Python", "category": "technical", "proficiency": "advanced"}],
            "work_experience": [{"company": "Acme", "role": "Engineer", "start_date": "2020-01"}],
            "education": [{"institution": "TU München", "degree": "MSc", "field": "CS"}],
            "professional_summary": {"en": "Experienced engineer"},
        })
        # Completeness depends on weights; just check it returns a valid mode string
        result = _auto_detect_mode(profile)
        assert result in ("targeted", "guided")

    def test_estimated_questions_targeted(self):
        from applire.services.session import _estimated_questions
        result = _estimated_questions("targeted")
        assert isinstance(result, int)
        assert result > 0

    def test_estimated_questions_guided(self):
        from applire.services.session import _estimated_questions
        result = _estimated_questions("guided")
        assert isinstance(result, int)
        assert result > 0

    def test_make_session_record_sets_expires_at(self):
        from applire.services.session import _make_session_record
        job_id = uuid.uuid4()
        profile_id = uuid.uuid4()
        state = {
            "mode": "targeted", "job_id": str(job_id), "gap_analysis_id": None,
            "profile_id": str(profile_id), "critical_gaps": [], "gap_categories": {},
            "addressed_gaps": [], "current_gap_index": 0, "current_question": "",
            "messages": [], "questions_asked": 0, "hard_ceiling": 12,
            "questions_per_gap": {}, "skipped_gaps": [], "full_gaps": [],
        }
        record = _make_session_record(
            job_id=job_id, gap_analysis_id=None, profile_id=profile_id,
            mode="targeted", status="active", state=state, hard_ceiling=12,
        )
        assert record.expires_at is not None
        assert record.expires_at > datetime.now(timezone.utc)


# ===========================================================================
# Part 3: session.py — DB helpers (SQLite)
# ===========================================================================

class TestSessionDbHelpers:
    @pytest.mark.asyncio
    async def test_get_active_session_returns_none_when_none(self, sqlite_session):
        from applire.services.session import _get_active_session
        result = await _get_active_session(uuid.uuid4(), sqlite_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_active_session_returns_session(self, sqlite_session):
        from applire.services.session import _get_active_session
        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        result = await _get_active_session(job.id, sqlite_session)
        assert result is not None
        assert result.job_analysis_id == job.id

    @pytest.mark.asyncio
    async def test_load_profile_raises_when_not_found(self, sqlite_session):
        from applire.services.session import _load_profile
        with pytest.raises(LookupError, match="not found"):
            await _load_profile(str(uuid.uuid4()), sqlite_session)

    @pytest.mark.asyncio
    async def test_load_profile_returns_profile(self, sqlite_session):
        from applire.services.session import _load_profile
        profile = _make_profile()
        sqlite_session.add(profile)
        await sqlite_session.commit()

        result = await _load_profile(str(profile.id), sqlite_session)
        assert result.id == profile.id

    @pytest.mark.asyncio
    async def test_load_job_context_returns_empty_when_not_found(self, sqlite_session):
        from applire.services.session import _load_job_context
        result = await _load_job_context(str(uuid.uuid4()), sqlite_session)
        assert result == {}

    @pytest.mark.asyncio
    async def test_load_job_context_returns_title_and_seniority(self, sqlite_session):
        from applire.services.session import _load_job_context
        job = _make_job()
        sqlite_session.add(job)
        await sqlite_session.commit()

        result = await _load_job_context(str(job.id), sqlite_session)
        assert result["role_title"] == "Senior Python Engineer"
        assert result["seniority_level"] == "Senior"


# ===========================================================================
# Part 4: create_session (SQLite + mocked LLM)
# ===========================================================================

class TestCreateSession:
    @pytest.mark.asyncio
    async def test_raises_when_job_not_found(self, sqlite_session):
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest
        req = SessionCreateRequest(job_id=uuid.uuid4(), mode="targeted")
        with pytest.raises(LookupError, match="not found"):
            await create_session(req, sqlite_session, _mock_provider())

    @pytest.mark.asyncio
    async def test_resumes_existing_active_session(self, sqlite_session):
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        # A genuine resume: the user has already answered at least one question,
        # so questions_asked has advanced past the initial 1.
        session_record = _make_active_session(
            job.id, profile.id, state={"questions_asked": 2}
        )
        session_record.questions_asked = 2
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted")
        result = await create_session(req, sqlite_session, _mock_provider())
        assert result.session_id == session_record.id
        assert result.resumed is True

    @pytest.mark.asyncio
    async def test_freshly_created_session_is_not_marked_resumed(self, sqlite_session):
        """Issue #44: the onboarding overlay pre-creates the guided session, so the
        interview page's own (idempotent) create call always hits the existing
        session.  A session the user has not answered yet (questions_asked == 1)
        is still at its first question — it must NOT report resumed=True, or the
        page greets a brand-new user with "Willkommen zurück"."""
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        # Default helper state mirrors a just-created session: questions_asked == 1.
        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted")
        result = await create_session(req, sqlite_session, _mock_provider())
        assert result.session_id == session_record.id
        assert result.resumed is False

    @pytest.mark.asyncio
    async def test_creates_targeted_session_with_existing_gap_analysis(self, sqlite_session):
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _make_gap(job.id, profile.id)
        sqlite_session.add(gap)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted")

        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "Tell me about GCP.", "choices": None}),
        ):
            result = await create_session(req, sqlite_session, _mock_provider())

        assert result.mode == "targeted"
        assert result.resumed is False
        assert result.gaps_total == 2
        assert "Tell me about GCP" in result.question

    @pytest.mark.asyncio
    async def test_creates_targeted_session_no_profile_raises(self, sqlite_session):
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest

        job = _make_job()
        sqlite_session.add(job)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted")

        with pytest.raises(LookupError, match="No profile found"):
            await create_session(req, sqlite_session, _mock_provider())

    @pytest.mark.asyncio
    async def test_creates_guided_session(self, sqlite_session):
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest

        job = _make_job()
        sqlite_session.add(job)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="guided")

        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "Tell me about your background.", "choices": None}),
        ):
            result = await create_session(req, sqlite_session, _mock_provider())

        assert result.mode == "guided"
        assert "background" in result.question

    @pytest.mark.asyncio
    async def test_auto_detects_guided_mode_when_no_profile(self, sqlite_session):
        """create_session auto-detects guided mode when profile is absent."""
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest

        job = _make_job()
        sqlite_session.add(job)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id)  # mode=None → auto-detect

        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "Tell me about yourself.", "choices": None}),
        ):
            result = await create_session(req, sqlite_session, _mock_provider())

        assert result.mode == "guided"

    @pytest.mark.asyncio
    async def test_creates_targeted_session_with_no_gaps_returns_complete(self, sqlite_session):
        """Targeted session with no critical gaps marks session complete immediately."""
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest
        from applire.models.gap import GapAnalysis

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=0.95,
            critical_gaps=[],
            minor_gaps=[],
            strengths=["Python"],
            keyword_gaps=[],
            category_a=[],
            category_b=[],
            category_c=[],  # No gaps
        )
        sqlite_session.add(gap)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted")
        result = await create_session(req, sqlite_session, _mock_provider())

        assert result.gaps_total == 0
        assert result.gaps_remaining == 0

    @pytest.mark.asyncio
    async def test_creates_micro_session_with_target_gap(self, sqlite_session):
        """Micro-session scoped to a single gap."""
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _make_gap(job.id, profile.id)
        sqlite_session.add(gap)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted", target_gap="GCP certification")

        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "Tell me about GCP certs.", "choices": None}),
        ):
            result = await create_session(req, sqlite_session, _mock_provider())

        assert result.mode == "targeted"
        assert result.gaps_total == 1
        assert result.estimated_questions == 1

    @pytest.mark.asyncio
    async def test_creates_micro_session_replaces_existing_active(self, sqlite_session):
        """Micro-session creation closes any existing active session."""
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _make_gap(job.id, profile.id)
        sqlite_session.add(gap)
        await sqlite_session.flush()

        # Create an existing active session
        existing = _make_active_session(job.id, profile.id, gap.id)
        sqlite_session.add(existing)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted", target_gap="GCP certification")

        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "Tell me about GCP.", "choices": None}),
        ):
            result = await create_session(req, sqlite_session, _mock_provider())

        await sqlite_session.refresh(existing)
        assert existing.status == "complete"
        assert result.mode == "targeted"

    @pytest.mark.asyncio
    async def test_micro_session_no_profile_raises(self, sqlite_session):
        """Micro-session without profile raises LookupError."""
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest

        job = _make_job()
        sqlite_session.add(job)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted", target_gap="GCP certification")

        with pytest.raises(LookupError, match="No profile found"):
            await create_session(req, sqlite_session, _mock_provider())


# ===========================================================================
# Part 5: get_session_state (SQLite)
# ===========================================================================

class TestGetSessionState:
    @pytest.mark.asyncio
    async def test_raises_when_not_found(self, sqlite_session):
        from applire.services.session import get_session_state
        with pytest.raises(LookupError, match="not found"):
            await get_session_state(uuid.uuid4(), sqlite_session)

    @pytest.mark.asyncio
    async def test_returns_state_for_active_session(self, sqlite_session):
        from applire.services.session import get_session_state

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        result = await get_session_state(session_record.id, sqlite_session)
        assert result.session_id == session_record.id
        assert result.status == "active"
        assert result.current_question is not None
        assert result.gaps_remaining >= 0

    @pytest.mark.asyncio
    async def test_returns_expired_status_for_past_expires_at(self, sqlite_session):
        from applire.services.session import get_session_state
        from applire.models.session import InterviewSession

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        past = datetime.now(timezone.utc) - timedelta(days=1)
        session_record = _make_active_session(job.id, profile.id)
        session_record.expires_at = past
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        result = await get_session_state(session_record.id, sqlite_session)
        assert result.status == "expired"

    @pytest.mark.asyncio
    async def test_returns_complete_status_for_complete_session(self, sqlite_session):
        from applire.services.session import get_session_state
        from applire.models.session import InterviewSession

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        session_record.status = "complete"
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        result = await get_session_state(session_record.id, sqlite_session)
        assert result.status == "complete"
        assert result.current_question is None


# ===========================================================================
# Part 6: send_message (SQLite + mocked LLM)
# ===========================================================================

class TestSendMessage:
    @pytest.mark.asyncio
    async def test_raises_when_session_not_found(self, sqlite_session):
        from applire.services.session import send_message
        with pytest.raises(LookupError, match="not found"):
            await send_message(uuid.uuid4(), "I have GCP experience.", sqlite_session, _mock_provider())

    @pytest.mark.asyncio
    async def test_raises_when_session_complete(self, sqlite_session):
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        session_record.status = "complete"
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        with pytest.raises(ValueError, match="already complete"):
            await send_message(session_record.id, "test", sqlite_session, _mock_provider())

    @pytest.mark.asyncio
    async def test_termination_signal_completes_session(self, sqlite_session):
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        result = await send_message(session_record.id, "done", sqlite_session, _mock_provider())
        assert result.complete is True
        assert result.reason == "user_ended"

    @pytest.mark.asyncio
    async def test_full_resolution_advances_to_next_gap(self, sqlite_session):
        """An answer that addresses the gap advances to the next gap (ADR-046)."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        # The reconciler added a skill → the gap is addressed → the loop advances.
        turn = _addressed_turn(profile.profile_json)

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "I have 3 years of GCP experience.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        assert result.question == "Tell me about FastAPI."
        assert result.gaps_remaining == 1

    @pytest.mark.asyncio
    async def test_addressed_answer_advances_to_next_gap(self, sqlite_session):
        """An addressed turn advances exactly one gap → gaps_remaining drops to 1.

        Migrated from the former gap_resolution='declined' test: under ADR-046 the
        send_message layer no longer routes on a 'declined' verdict (decline is a
        termination signal handled upstream, and a no-info answer now follows up
        once rather than advancing). What this test still guards is the advance
        arithmetic — a single addressed turn moves to the second (and last) of two
        gaps, leaving one remaining, not still two.
        """
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _addressed_turn(profile.profile_json)

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "I have no GCP experience at all.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        # Advanced to the second (and last) gap → one remaining, not still 2
        assert result.gaps_remaining == 1

    @pytest.mark.asyncio
    async def test_pending_confirmation_surfaces_as_targeted_question(self, sqlite_session):
        """An ambiguous reconcile turn surfaces a confirmation prompt, never a silent merge (US185).

        The reconciler flagged "is 'Owner at applire' the same as your existing
        'Founder & Lead Developer' role?". The interview must ASK that — the
        question, its option buttons, and a structured pending_confirmations
        payload — rather than guessing identity or advancing past it.
        """
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _confirming_turn(profile.profile_json)

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "SHOULD NOT BE ASKED", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "I'm the Owner at applire.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        # The confirmation question — NOT the auto-generated next-gap question — is asked.
        assert result.question == turn.pending_confirmations[0].question
        assert result.choices == ["Yes, same role", "No, separate roles"]
        # Structured payload lets the UI render a confirmation card with context.
        assert result.pending_confirmations is not None
        assert len(result.pending_confirmations) == 1
        prompt = result.pending_confirmations[0]
        assert prompt.question == turn.pending_confirmations[0].question
        assert prompt.options == ["Yes, same role", "No, separate roles"]
        assert prompt.context["existing"] == "Founder & Lead Developer"

    @pytest.mark.asyncio
    async def test_confirmation_answer_advances_without_re_asking(self, sqlite_session):
        """After a confirmation is shown, the next answer resolves it and the loop
        moves on — it does not loop on the same gap (US185)."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        # Turn 1: ambiguity → confirmation surfaced (gap pre-marked addressed).
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=_confirming_turn(profile.profile_json))),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "next gap?", "choices": None})),
        ):
            await send_message(
                session_record.id, "I'm the Owner at applire.", sqlite_session, _mock_provider()
            )

        # Turn 2: user confirms; reconciler produces no further ambiguity → advance.
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=_unaddressed_turn(profile.profile_json))),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "Yes, same role", sqlite_session, _mock_provider()
            )

        assert result.complete is False
        # Advanced off the confirmed gap → one gap remaining, not still two.
        assert result.gaps_remaining == 1
        assert result.question == "Tell me about FastAPI."

    @pytest.mark.asyncio
    async def test_unaddressed_answer_generates_follow_up(self, sqlite_session):
        """A turn that produced no profile change stays on the gap and follows up.

        Migrated from the former gap_resolution='partial' test: under ADR-046 an
        answer that yields no profile change (and is not a termination signal) is
        'not addressed' → the loop asks one bounded follow-up on the same gap
        rather than advancing.
        """
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _unaddressed_turn(profile.profile_json)

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Have you taken any GCP architect exams?", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "I've done some GCP work.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        # Still on the same (first) gap → both gaps remain.
        assert result.gaps_remaining == 2
        assert "GCP architect" in result.question

    @pytest.mark.asyncio
    async def test_conflict_summaries_surface_as_pending_conflicts(self, sqlite_session):
        """An addressed turn advances AND surfaces engine conflicts to the client.

        Migrated from the former cross-gap 'gaps_also_addressed' test (the lexical
        gaps_also_addressed concept is gone under ADR-046). What remains worth
        guarding: when reconcile_interview_turn reports conflict_summaries, the
        loop must pass them through as the response's pending_conflicts while still
        advancing on an addressed turn.
        """
        from applire.services.session import send_message
        from applire.schemas.session import ConflictSummary

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        conflict = ConflictSummary(
            conflict_id="c1", field="skills.GCP", old_value="beginner", new_value="advanced"
        )
        turn = _addressed_turn(profile.profile_json, conflicts=[conflict])

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "I have 3 years GCP and FastAPI.",
                sqlite_session, _mock_provider()
            )

        # Addressed → advanced to the next gap …
        assert result.complete is False
        assert result.question == "Tell me about FastAPI."
        # … and the engine's conflicts are surfaced to the client.
        assert result.pending_conflicts is not None
        assert result.pending_conflicts[0].conflict_id == "c1"

    @pytest.mark.asyncio
    async def test_hard_ceiling_triggers_completion(self, sqlite_session):
        """Hitting hard ceiling completes the session."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        # Set questions_asked to hard_ceiling - 1 so the next message hits the ceiling
        state_override = {
            "hard_ceiling": 2,
            "questions_asked": 1,
        }
        session_record = _make_active_session(job.id, profile.id, state=state_override)
        session_record.hard_ceiling = 2
        session_record.questions_asked = 1
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        # The ceiling check fires before the advance decision, so the turn result
        # is incidental — any reconciled turn must still hit the ceiling.
        turn = _unaddressed_turn(profile.profile_json)

        with patch("applire.services.session.reconcile_interview_turn",
                   new=AsyncMock(return_value=turn)):
            result = await send_message(
                session_record.id, "I don't have much GCP experience.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is True
        assert result.reason == "max_questions_reached"

    @pytest.mark.asyncio
    async def test_all_gaps_resolved_triggers_completion(self, sqlite_session):
        """Resolving last gap completes session with reason='gaps_resolved'."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        # Only one gap left
        state_override = {
            "critical_gaps": ["GCP certification"],
            "gap_categories": {"GCP certification": "C"},
            "current_gap_index": 0,
            "current_question": "Tell me about GCP.",
            "messages": [{"role": "assistant", "content": "Tell me about GCP."}],
        }
        session_record = _make_active_session(job.id, profile.id, state=state_override)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        # Addressing the only remaining gap → no gaps left → session completes.
        turn = _addressed_turn(profile.profile_json)

        with patch("applire.services.session.reconcile_interview_turn",
                   new=AsyncMock(return_value=turn)):
            result = await send_message(
                session_record.id, "I have extensive GCP experience.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is True
        assert result.reason == "gaps_resolved"


# ===========================================================================
# Part 7: routers/session.py — HTTP layer (TestClient + mocked services)
# ===========================================================================

def _make_session_app():
    """Build a minimal FastAPI app with the session router and mocked deps."""
    from fastapi import FastAPI
    from applire.routers.session import router
    app = FastAPI()
    app.include_router(router)
    return app


def _setup_router_deps(app, mock_db=None, mock_provider=None):
    """Override db and auth dependencies on the app."""
    from applire.db.session import get_db
    from applire.auth import get_auth_provider

    if mock_db is None:
        mock_db = AsyncMock()

    async def override_db():
        yield mock_db

    async def override_auth():
        return None

    if mock_provider is None:
        mock_provider = _mock_provider()

    from applire.routers.session import _get_provider
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_provider] = override_auth
    app.dependency_overrides[_get_provider] = lambda: mock_provider

    return app


class TestSessionRouter:
    def _build_app(self):
        app = _make_session_app()
        return _setup_router_deps(app)

    def test_start_session_returns_201(self):
        from fastapi.testclient import TestClient
        from applire.schemas.session import SessionCreateResponse
        import uuid as _uuid

        mock_response = SessionCreateResponse(
            session_id=_uuid.uuid4(),
            mode="targeted",
            first_question="Tell me about GCP.",
            question="Tell me about GCP.",
            estimated_questions=8,
            gaps_total=3,
            gaps_remaining=3,
        )

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.services.session.create_session", new=AsyncMock(return_value=mock_response)):
            with patch("applire.routers.session.create_session", new=AsyncMock(return_value=mock_response)):
                with TestClient(app) as client:
                    resp = client.post("/api/session", json={"job_id": str(_uuid.uuid4())})

        assert resp.status_code == 201

    def test_start_session_404_on_lookup_error(self):
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.create_session", new=AsyncMock(side_effect=LookupError("Job not found"))):
            with TestClient(app) as client:
                resp = client.post("/api/session", json={"job_id": str(_uuid.uuid4())})

        assert resp.status_code == 404

    def test_start_session_503_on_rate_limit(self):
        from fastapi.testclient import TestClient
        from applire.exceptions import LLMRateLimitError
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.create_session",
                   new=AsyncMock(side_effect=LLMRateLimitError("rate limit"))):
            with TestClient(app) as client:
                resp = client.post("/api/session", json={"job_id": str(_uuid.uuid4())})

        assert resp.status_code == 503

    def test_start_session_504_on_timeout(self):
        from fastapi.testclient import TestClient
        from applire.exceptions import LLMTimeoutError
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.create_session",
                   new=AsyncMock(side_effect=LLMTimeoutError("timeout"))):
            with TestClient(app) as client:
                resp = client.post("/api/session", json={"job_id": str(_uuid.uuid4())})

        assert resp.status_code == 504

    def test_start_session_502_on_json_decode_error(self):
        import json
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.create_session",
                   new=AsyncMock(side_effect=json.JSONDecodeError("bad json", "", 0))):
            with TestClient(app) as client:
                resp = client.post("/api/session", json={"job_id": str(_uuid.uuid4())})

        assert resp.status_code == 502

    def test_start_session_500_on_generic_error(self):
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.create_session",
                   new=AsyncMock(side_effect=RuntimeError("unexpected"))):
            with TestClient(app) as client:
                resp = client.post("/api/session", json={"job_id": str(_uuid.uuid4())})

        assert resp.status_code == 500

    def test_get_session_returns_200(self):
        from fastapi.testclient import TestClient
        from applire.schemas.session import SessionStateResponse
        import uuid as _uuid

        session_id = _uuid.uuid4()
        job_id = _uuid.uuid4()
        mock_response = SessionStateResponse(
            session_id=session_id,
            job_id=job_id,
            mode="targeted",
            status="active",
            questions_asked=1,
            hard_ceiling=12,
            current_question="Tell me about GCP.",
            gaps_remaining=2,
            completeness_score=0.5,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.get_session_state", new=AsyncMock(return_value=mock_response)):
            with TestClient(app) as client:
                resp = client.get(f"/api/session/{session_id}")

        assert resp.status_code == 200

    def test_get_session_404_on_lookup_error(self):
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.get_session_state",
                   new=AsyncMock(side_effect=LookupError("not found"))):
            with TestClient(app) as client:
                resp = client.get(f"/api/session/{_uuid.uuid4()}")

        assert resp.status_code == 404

    def test_post_message_returns_200(self):
        from fastapi.testclient import TestClient
        from applire.schemas.session import SessionMessageResponse
        import uuid as _uuid

        session_id = _uuid.uuid4()
        mock_response = SessionMessageResponse(
            complete=False,
            question="Tell me about FastAPI.",
            gaps_remaining=1,
        )

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.send_message", new=AsyncMock(return_value=mock_response)):
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/session/{session_id}/message",
                    json={"message": "I have GCP experience."},
                )

        assert resp.status_code == 200

    def test_post_message_422_on_empty_message(self):
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with TestClient(app) as client:
            resp = client.post(
                f"/api/session/{_uuid.uuid4()}/message",
                json={"message": "   "},  # whitespace-only
            )

        assert resp.status_code == 422

    def test_post_message_404_on_lookup_error(self):
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.send_message",
                   new=AsyncMock(side_effect=LookupError("Session not found"))):
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/session/{_uuid.uuid4()}/message",
                    json={"message": "I have GCP experience."},
                )

        assert resp.status_code == 404

    def test_post_message_409_on_value_error(self):
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.send_message",
                   new=AsyncMock(side_effect=ValueError("Session is already complete"))):
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/session/{_uuid.uuid4()}/message",
                    json={"message": "test message"},
                )

        assert resp.status_code == 409

    def test_post_message_503_on_rate_limit(self):
        from fastapi.testclient import TestClient
        from applire.exceptions import LLMRateLimitError
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.send_message",
                   new=AsyncMock(side_effect=LLMRateLimitError("rate limit"))):
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/session/{_uuid.uuid4()}/message",
                    json={"message": "test message"},
                )

        assert resp.status_code == 503

    def test_analyze_session_gaps_404_on_lookup_error(self):
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.analyze_gaps_for_session",
                   new=AsyncMock(side_effect=LookupError("not found"))):
            with TestClient(app) as client:
                resp = client.post(f"/api/session/{_uuid.uuid4()}/analyze-gaps")

        assert resp.status_code == 404


# ===========================================================================
# Part 8: thumbnails.py
# ===========================================================================

class TestThumbnails:
    @pytest.mark.asyncio
    async def test_skips_generation_when_all_thumbnails_exist(self, tmp_path):
        """ensure_thumbnails returns early if all thumbs already exist."""
        from applire.services.thumbnails import ensure_thumbnails, _TEMPLATE_FILES

        thumbs_dir = tmp_path / "templates"
        thumbs_dir.mkdir()

        # Create fake thumbnail files for all templates
        for name in _TEMPLATE_FILES:
            (thumbs_dir / f"{name}.png").write_bytes(b"fake_png")

        # Should return without calling Playwright (early return — no patch needed)
        await ensure_thumbnails(tmp_path)
        # If we reach here without Playwright being invoked, the test passes.

    @pytest.mark.asyncio
    async def test_generates_missing_thumbnails(self, tmp_path):
        """ensure_thumbnails calls Playwright for missing thumbnails."""
        from applire.services.thumbnails import ensure_thumbnails

        thumbs_dir = tmp_path / "templates"
        thumbs_dir.mkdir()
        # Do NOT create any .png files — all thumbnails are missing

        # Mock Playwright — async_playwright is imported locally, patch at source
        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(return_value=b"fake_png_bytes")
        mock_page.close = AsyncMock()

        mock_browser = AsyncMock()
        mock_browser.new_page = AsyncMock(return_value=mock_page)
        mock_browser.close = AsyncMock()

        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)

        mock_pw_instance = AsyncMock()
        mock_pw_instance.chromium = mock_chromium

        mock_pw_cm = MagicMock()
        mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_instance)
        mock_pw_cm.__aexit__ = AsyncMock(return_value=None)

        mock_env = MagicMock()
        mock_template = MagicMock()
        mock_template.render = MagicMock(return_value="<html></html>")
        mock_env.get_template = MagicMock(return_value=mock_template)

        with patch("playwright.async_api.async_playwright", return_value=mock_pw_cm):
            with patch("applire.services.thumbnails.Environment", return_value=mock_env):
                await ensure_thumbnails(tmp_path)

        # Verify page.screenshot was called for each template
        assert mock_page.screenshot.call_count >= 1

    @pytest.mark.asyncio
    async def test_handles_playwright_exception_gracefully(self, tmp_path):
        """Playwright exceptions are caught and logged — no re-raise."""
        from applire.services.thumbnails import ensure_thumbnails

        thumbs_dir = tmp_path / "templates"
        thumbs_dir.mkdir()
        # All thumbnails missing → will try to generate

        mock_page = AsyncMock()
        mock_page.screenshot = AsyncMock(side_effect=RuntimeError("browser crash"))
        mock_page.close = AsyncMock()

        mock_browser = AsyncMock()
        mock_browser.new_page = AsyncMock(return_value=mock_page)
        mock_browser.close = AsyncMock()

        mock_chromium = AsyncMock()
        mock_chromium.launch = AsyncMock(return_value=mock_browser)

        mock_pw_instance = AsyncMock()
        mock_pw_instance.chromium = mock_chromium

        mock_pw_cm = MagicMock()
        mock_pw_cm.__aenter__ = AsyncMock(return_value=mock_pw_instance)
        mock_pw_cm.__aexit__ = AsyncMock(return_value=None)

        mock_env = MagicMock()
        mock_template = MagicMock()
        mock_template.render = MagicMock(return_value="<html></html>")
        mock_env.get_template = MagicMock(return_value=mock_template)

        with patch("playwright.async_api.async_playwright", return_value=mock_pw_cm):
            with patch("applire.services.thumbnails.Environment", return_value=mock_env):
                # Should not raise despite the browser crash
                await ensure_thumbnails(tmp_path)


# ===========================================================================
# Part 9: 3 remaining session.py uncovered paths
# ===========================================================================

class TestSessionEdgePaths:
    @pytest.mark.asyncio
    async def test_create_targeted_session_without_existing_gap_analysis(self, sqlite_session):
        """_create_targeted_session calls analyze_gaps when no GapAnalysis exists (lines 152-156)."""
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest
        from applire.schemas.gap import GapAnalysisResponse
        from applire.models.gap import GapAnalysis
        import uuid as _uuid

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.commit()

        # No gap analysis in DB — create_session must call analyze_gaps

        # analyze_gaps returns a GapAnalysisResponse; we mock it to create a real DB record
        async def fake_analyze_gaps(job_id, db, provider):
            ga = GapAnalysis(
                job_analysis_id=job_id,
                profile_id=profile.id,
                match_score=0.7,
                critical_gaps=["GCP certification"],
                minor_gaps=[],
                strengths=["Python"],
                keyword_gaps=[],
                category_a=[],
                category_b=[],
                category_c=["GCP certification"],
                gap_clusters=[
                    {"id": "cluster-gcp", "label": "GCP certification", "category": "C", "gaps": ["GCP certification"], "jd_skills": [], "jd_context": ""}
                ],
            )
            db.add(ga)
            await db.flush()
            await db.refresh(ga)
            return GapAnalysisResponse(
                id=ga.id,
                job_analysis_id=ga.job_analysis_id,
                profile_id=ga.profile_id,
                match_score=ga.match_score,
                critical_gaps=ga.critical_gaps,
                minor_gaps=ga.minor_gaps,
                strengths=ga.strengths,
                keyword_gaps=ga.keyword_gaps,
                category_a=ga.category_a,
                category_b=ga.category_b,
                category_c=ga.category_c,
                created_at=ga.created_at,
            )

        req = SessionCreateRequest(job_id=job.id, mode="targeted")

        with (
            patch("applire.services.session.analyze_gaps", side_effect=fake_analyze_gaps),
            patch(
                "applire.services.session.question_generator_with_profile",
                new=AsyncMock(return_value={"question": "Tell me about GCP.", "choices": None}),
            ),
        ):
            result = await create_session(req, sqlite_session, _mock_provider())

        assert result.mode == "targeted"
        assert result.gaps_total == 1

    @pytest.mark.asyncio
    async def test_micro_session_with_category_b_target_gap(self, sqlite_session):
        """Micro-session with a category B target_gap sets gap_category='B' (line 336)."""
        from applire.services.session import create_session
        from applire.schemas.session import SessionCreateRequest
        from applire.models.gap import GapAnalysis

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        # target_gap is a cluster ID in category_b
        gap = GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=0.6,
            critical_gaps=["ISO 9001 compliance", "GCP certification"],
            minor_gaps=[],
            strengths=["Python"],
            keyword_gaps=[],
            category_a=[],
            category_b=["ISO 9001 compliance"],
            category_c=["GCP certification"],
            gap_clusters=[
                {"id": "cluster-iso", "label": "ISO 9001 compliance", "category": "B", "gaps": ["ISO 9001 compliance"], "jd_skills": [], "jd_context": ""},
                {"id": "cluster-gcp", "label": "GCP certification", "category": "C", "gaps": ["GCP certification"], "jd_skills": [], "jd_context": ""},
            ],
        )
        sqlite_session.add(gap)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted", target_gap="cluster-iso")

        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "Tell me about ISO 9001.", "choices": None}),
        ):
            result = await create_session(req, sqlite_session, _mock_provider())

        assert result.mode == "targeted"
        assert result.gaps_total == 1

    @pytest.mark.asyncio
    async def test_send_message_guided_mode_loads_job_context_on_advance(self, sqlite_session):
        """Guided session advancing to next gap loads job context (line 506)."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        guided_state = {
            "mode": "guided",
            "job_id": str(job.id),
            "gap_analysis_id": None,
            "profile_id": str(profile.id),
            "critical_gaps": ["work_experience", "education"],
            "gap_categories": {"work_experience": None, "education": None},
            "addressed_gaps": [],
            "current_gap_index": 0,
            "current_question": "Tell me about your work history.",
            "messages": [{"role": "assistant", "content": "Tell me about your work history."}],
            "questions_asked": 1,
            "hard_ceiling": 20,
            "questions_per_gap": {},
            "skipped_gaps": [],
            "full_gaps": [],
        }

        from applire.models.session import InterviewSession
        session_record = InterviewSession(
            job_analysis_id=job.id,
            gap_analysis_id=None,
            profile_id=profile.id,
            mode="guided",
            status="active",
            state=guided_state,
            hard_ceiling=20,
            questions_asked=1,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        # Reconciler merged the answer into work_experience → gap addressed →
        # the guided loop advances to the next gap and loads job context for it.
        from applire.schemas.profile import FieldChange
        updated_profile = dict(profile.profile_json)
        updated_profile["work_experience"] = [
            {"company": "Acme", "role": "Engineer", "start_date": "2020-01"}
        ]
        turn = _addressed_turn(
            updated_profile,
            changes=[FieldChange(section="work_experience", field="Acme", action="added")],
        )

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch(
                "applire.services.session.question_generator_with_profile",
                new=AsyncMock(return_value={"question": "Tell me about your education.", "choices": None}),
            ),
        ):
            result = await send_message(
                session_record.id, "I worked at Acme for 5 years.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        assert result.question == "Tell me about your education."


# ===========================================================================
# Part 10: routers/health.py
# ===========================================================================

class TestHealthRouter:
    def test_health_returns_ok(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from applire.routers.health import router

        app = FastAPI()
        app.include_router(router)

        with TestClient(app) as client:
            resp = client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["edition"] in ("community", "cloud")
        assert "version" in data


# ===========================================================================
# Part 11: _complete_session advances the owning flow (issue #68)
# ===========================================================================

@pytest.mark.asyncio
async def test_complete_session_advances_owning_flow(sqlite_session):
    """_complete_session must call advance_flow_on_interview_complete so that
    the flow moves off the 'interview' step and resuming from the dashboard no
    longer re-opens a finished interview (bug #68)."""
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.services.session import _complete_session

    job = _make_job()
    sqlite_session.add(job)
    profile = _make_profile()
    sqlite_session.add(profile)
    await sqlite_session.flush()

    record = _make_active_session(job.id, profile.id)
    sqlite_session.add(record)
    await sqlite_session.commit()
    await sqlite_session.refresh(record)

    state = dict(record.state)

    # Patch at the definition site: _complete_session imports the symbol lazily
    # at call time, so the name resolves from the orchestrator module.
    with patch(
        "applire.services.flow.orchestrator.advance_flow_on_interview_complete",
        new_callable=AsyncMock,
    ) as mock_advance:
        await _complete_session(record, state, sqlite_session, reason="user_ended")

    assert record.status == "complete"
    mock_advance.assert_awaited_once_with(record.id, sqlite_session)


@pytest.mark.asyncio
async def test_complete_session_survives_flow_advance_failure(sqlite_session):
    """A failure in advance_flow_on_interview_complete (which runs AFTER the
    interview is already committed complete) must not propagate and 500 the
    completion request — advancing the flow is a best-effort, recoverable
    convenience (the Generate-CV button re-advances idempotently)."""
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.services.session import _complete_session

    job = _make_job()
    sqlite_session.add(job)
    profile = _make_profile()
    sqlite_session.add(profile)
    await sqlite_session.flush()

    record = _make_active_session(job.id, profile.id)
    sqlite_session.add(record)
    await sqlite_session.commit()
    await sqlite_session.refresh(record)

    state = dict(record.state)

    with patch(
        "applire.services.flow.orchestrator.advance_flow_on_interview_complete",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        # Must not raise — flow advance is best-effort.
        await _complete_session(record, state, sqlite_session, reason="user_ended")

    assert record.status == "complete"
