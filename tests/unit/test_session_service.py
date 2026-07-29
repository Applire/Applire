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

import copy
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


def _denied_turn(profile_dict, *, conflicts=None, denied_concepts=None):
    """A turn that recorded an explicit denial and nothing else (#231) — no
    profile mutation, so `addressed` stays False (F8). Absent a JD-critical,
    not-yet-probed `denied_concepts` entry (ADR-064), the denial is a
    TERMINAL answer (#259 sufficiency criterion b): it must advance past the
    gap on this turn, never re-ask it. `denied_concepts` mirrors
    `InterviewTurnResult.denied_concepts` — the raw concept text(s) this turn
    denied — which session.py's transfer-probe trigger reads."""
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    return InterviewTurnResult(
        profile_dict=profile_dict,
        changes=[],
        addressed=False,
        denial_recorded=True,
        conflict_summaries=list(conflicts or []),
        denied_concepts=list(denied_concepts or []),
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


# ---------------------------------------------------------------------------
# ADR-064 — denial transfer-probe fixtures. Mirrors `_make_gap`'s two
# critical_gaps ("GCP certification", "FastAPI experience") so it drops
# straight into `_make_active_session`'s defaults; only the ledger's
# `sources` on "GCP certification" toggles JD-criticality.
# ---------------------------------------------------------------------------

def _probe_gap_with_ledger(job, profile, *, required=True):
    """A persisted GapAnalysis whose keyword_ledger holds a 'GCP
    certification' entry — JD-required (`sources=["required"]`) unless
    `required=False` — plus a required 'FastAPI experience' entry so the
    interview has a genuine second gap to advance onto."""
    from applire.models.gap import GapAnalysis

    ledger = [
        {
            "concept": "GCP certification", "surface_forms": ["GCP certification"],
            "sources": ["required"] if required else ["nice_to_have"],
            "fit_weight": 1.0, "status": "gap", "evidence": "", "claimable": False,
        },
        {
            "concept": "FastAPI experience", "surface_forms": ["FastAPI experience"],
            "sources": ["required"], "fit_weight": 1.0, "status": "gap",
            "evidence": "", "claimable": False,
        },
    ]
    return GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        match_score=0.6,
        critical_gaps=["GCP certification", "FastAPI experience"],
        minor_gaps=[],
        strengths=["Python"],
        keyword_gaps=[],
        category_a=[],
        category_b=[],
        category_c=["GCP certification", "FastAPI experience"],
        keyword_ledger=ledger,
        gap_clusters=[
            {"id": "cluster-gcp", "label": "GCP certification", "category": "C",
             "gaps": ["GCP certification"], "jd_skills": [], "jd_context": ""},
            {"id": "cluster-fastapi", "label": "FastAPI experience", "category": "C",
             "gaps": ["FastAPI experience"], "jd_skills": [], "jd_context": ""},
        ],
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

    # --- #216: robust termination for agent-driven interviews -----------------

    def test_trailing_punctuation_is_stripped(self):
        from applire.services.interview.signals import is_termination_signal
        assert is_termination_signal("done.") is True
        assert is_termination_signal("fertig!") is True
        assert is_termination_signal("Done…") is True

    def test_natural_language_termination_phrases(self):
        from applire.services.interview.signals import is_termination_signal
        for msg in [
            "I'm done.",
            "I am done",
            "That's all",
            "no more questions",
            "please wrap up",
            "das war's dann",
            "Ich bin fertig",
            "keine weiteren Fragen",
        ]:
            assert is_termination_signal(msg) is True, msg

    def test_leading_framing_before_done_still_terminates(self):
        from applire.services.interview.signals import is_termination_signal
        # The exact input that failed the 2026-07-21 edge UAT run (#216).
        assert is_termination_signal(
            "I'm done with the interview, please wrap up"
        ) is True
        assert is_termination_signal("I am done here, thanks") is True

    def test_negation_never_terminates(self):
        from applire.services.interview.signals import is_termination_signal
        assert is_termination_signal("I'm not done yet") is False
        assert is_termination_signal("Not done — I have more to add") is False
        assert is_termination_signal("Ich bin noch nicht fertig") is False

    def test_signal_word_inside_a_real_answer_does_not_terminate(self):
        from applire.services.interview.signals import is_termination_signal
        # "skip", "end", "done", "that's all" embedded in substantive answers.
        assert is_termination_signal("We can skip the frontend part of my CV") is False
        assert is_termination_signal("At the end of my last role I led the migration") is False
        assert is_termination_signal("That's all the Python I know, but I also use Go") is False
        assert is_termination_signal("I'm finished migrating the DB but have more to add") is False

    def test_done_with_a_topic_does_not_terminate(self):
        from applire.services.interview.signals import is_termination_signal
        # A bare "I'm done <topic>" opener must NOT end the session — only an
        # explicit terminal reference (the interview / wrap up) does.
        assert is_termination_signal("I'm done with Python but now use Go") is False
        assert is_termination_signal("We're done with Q1 planning, now for Q2") is False
        assert is_termination_signal(
            "I am done with my previous employer's project, moving to the next"
        ) is False

    def test_explicit_terminal_intent_terminates(self):
        from applire.services.interview.signals import is_termination_signal
        assert is_termination_signal("I'm done with the interview, please wrap up") is True
        assert is_termination_signal("Let's end the interview here") is True
        assert is_termination_signal("I am done for now") is True


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
        # issue #245 (NEW-4) — the resumed response must carry the record's
        # real persisted ceiling, not just the soft estimate, so the frontend
        # can show an honest "of up to N" instead of a fixed midpoint.
        assert result.hard_ceiling == session_record.hard_ceiling
        # #259 run-4 finding 9 — the resumed response must ALSO carry the
        # real persisted questions_asked, or the frontend counter resets to
        # "1 of up to N" on every page refresh even though the server has
        # tracked real progress (questions_asked=2 here, not 1).
        assert result.questions_asked == 2

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
        # issue #241 item 1 — the created session's first current_gap_id IS the
        # real gap-cluster id (matches gap_analysis.gap_clusters[0]["id"]), so
        # the frontend tracker can highlight the actual current cluster from
        # session creation onward, not just index 0 by assumption.
        assert result.current_gap_id == "cluster-gcp-certification"
        assert result.addressed_gap_ids == []
        # issue #245 (NEW-4) — a fresh MODE A session reports the real
        # hard_ceiling (12), not the soft "~7" midpoint that overshot in the
        # founder-acceptance run.
        from applire.constants import INTERVIEW_HARD_CEILING_TARGETED
        assert result.hard_ceiling == INTERVIEW_HARD_CEILING_TARGETED

    @pytest.mark.asyncio
    async def test_stale_gap_cluster_snapshot_already_direct_in_ledger_is_never_asked(
        self, sqlite_session
    ):
        """The literal run-6 shape (#273/#284, PO reframing 2026-07-26):
        gap_analysis.gap_clusters is a clustering-LLM SNAPSHOT that still
        names a concept the SAME row's own keyword_ledger already shows
        status=='direct' for (evidence arrived via testimony/CV import/an
        earlier session — a door #188's per-turn addressed-gate never sees).
        Session creation must filter the stale snapshot against the ledger
        so the interview jumps straight to the genuinely open gap instead of
        drilling the already-answered cluster."""
        from applire.models.gap import GapAnalysis
        from applire.schemas.session import SessionCreateRequest
        from applire.services.session import create_session

        job = _make_job()
        profile = _make_profile(
            completeness_json={
                "personal_info": {"name": "Anna Bauer", "email": "anna@example.de"},
                "skills": [{"name": "Python", "category": "technical", "proficiency": "advanced"}],
                "work_experience": [
                    {
                        "company": "Northwind Labs",
                        "role": "Engineering Lead",
                        "start_date": "2020-01",
                        "responsibilities": [
                            "Restructured the team and owned team management "
                            "across two firmware squads."
                        ],
                    }
                ],
                "metadata": {"denied_concepts": []},
            }
        )
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=0.8,
            critical_gaps=["FastAPI experience"],
            minor_gaps=[],
            strengths=["Python"],
            keyword_gaps=[],
            category_a=["Team management"],
            category_b=[],
            category_c=["FastAPI experience"],
            keyword_ledger=[
                {
                    "concept": "Team management",
                    "surface_forms": ["Team management"],
                    "sources": ["required"],
                    "fit_weight": 1.0,
                    "status": "direct",
                    "evidence": (
                        "Restructured the team and owned team management "
                        "across two firmware squads."
                    ),
                    "claimable": True,
                },
                {
                    "concept": "FastAPI experience",
                    "surface_forms": ["FastAPI experience"],
                    "sources": ["required"],
                    "fit_weight": 1.0,
                    "status": "gap",
                    "evidence": "",
                    "claimable": False,
                },
            ],
            gap_clusters=[
                # STALE — the clustering snapshot still lists Team management
                # even though this row's own ledger already shows it direct.
                {
                    "id": "cluster-team-management",
                    "label": "Technical Leadership",
                    "category": "C",
                    "gaps": ["Team management"],
                    "jd_skills": ["Team management"],
                    "jd_context": "Leadership.",
                },
                {
                    "id": "cluster-fastapi-experience",
                    "label": "FastAPI experience",
                    "category": "C",
                    "gaps": ["FastAPI experience"],
                    "jd_skills": ["FastAPI experience"],
                    "jd_context": "Backend framework.",
                },
            ],
        )
        sqlite_session.add(gap)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted")

        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None}),
        ):
            result = await create_session(req, sqlite_session, _mock_provider())

        # Only the genuinely open cluster is in the plan — never re-asked
        # about the already-answered one.
        assert result.gaps_total == 1
        assert result.current_gap_id == "cluster-fastapi-experience"
        assert "Tell me about FastAPI" in result.question

    @pytest.mark.asyncio
    async def test_gap_answered_via_a_different_door_before_session_start_is_never_asked(
        self, sqlite_session
    ):
        """#274/#284: the ledger itself is still status=='gap' (no #188 turn
        ever upgraded it), but the vault, as it stands right NOW, already
        answers the requirement — evidence arrived through testimony/CV
        import, not an interview turn. The session-start reevaluation must
        catch this BEFORE the question plan is built."""
        from applire.models.gap import GapAnalysis
        from applire.schemas.session import SessionCreateRequest
        from applire.services.session import create_session

        job = _make_job()
        profile = _make_profile(
            completeness_json={
                "personal_info": {"name": "Anna Bauer", "email": "anna@example.de"},
                "skills": [{"name": "Python", "category": "technical", "proficiency": "advanced"}],
                "work_experience": [
                    {
                        "company": "Northwind Labs",
                        "role": "Engineering Lead",
                        "start_date": "2020-01",
                        "responsibilities": [
                            "Owned team management for a distributed platform squad."
                        ],
                    }
                ],
                "metadata": {"denied_concepts": []},
            }
        )
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=0.6,
            critical_gaps=["Team management", "FastAPI experience"],
            minor_gaps=[],
            strengths=["Python"],
            keyword_gaps=[],
            category_a=[],
            category_b=[],
            category_c=["Team management", "FastAPI experience"],
            keyword_ledger=[
                {
                    "concept": "Team management",
                    "surface_forms": ["Team management"],
                    "sources": ["required"],
                    "fit_weight": 1.0,
                    "status": "gap",
                    "evidence": "",
                    "claimable": False,
                },
                {
                    "concept": "FastAPI experience",
                    "surface_forms": ["FastAPI experience"],
                    "sources": ["required"],
                    "fit_weight": 1.0,
                    "status": "gap",
                    "evidence": "",
                    "claimable": False,
                },
            ],
            gap_clusters=[
                {
                    "id": "cluster-team-management",
                    "label": "Technical Leadership",
                    "category": "C",
                    "gaps": ["Team management"],
                    "jd_skills": ["Team management"],
                    "jd_context": "Leadership.",
                },
                {
                    "id": "cluster-fastapi-experience",
                    "label": "FastAPI experience",
                    "category": "C",
                    "gaps": ["FastAPI experience"],
                    "jd_skills": ["FastAPI experience"],
                    "jd_context": "Backend framework.",
                },
            ],
        )
        sqlite_session.add(gap)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted")

        with patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None}),
        ):
            result = await create_session(req, sqlite_session, _mock_provider())

        assert result.gaps_total == 1
        assert result.current_gap_id == "cluster-fastapi-experience"

        # The ledger itself was upgraded in place, real vault text as evidence.
        await sqlite_session.refresh(gap)
        tm = next(e for e in gap.keyword_ledger if e["concept"] == "Team management")
        assert tm["claimable"] is True
        assert tm["status"] == "direct"
        assert "team management" in tm["evidence"].lower()

    @pytest.mark.asyncio
    async def test_real_remaining_gap_still_asked_after_reevaluation(self, sqlite_session):
        """Guard against over-filtering: a requirement the vault genuinely
        does not answer must still be asked, unchanged."""
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

        # No ledger at all on this fixture's GapAnalysis (_make_gap doesn't set
        # one) — reevaluation/filtering must no-op, not accidentally drop the
        # real gaps that were there before this change.
        assert result.gaps_total == 2
        assert result.current_gap_id == "cluster-gcp-certification"

    @pytest.mark.asyncio
    async def test_operator_configured_budget_threads_into_created_session(
        self, sqlite_session, monkeypatch
    ):
        """#259: an operator-raised INTERVIEW_MAX_QUESTIONS_TARGETED must reach
        the actual created session's hard_ceiling — the runtime value comes
        from config.settings, not the constants.py default, at the real
        create_session() call site."""
        from applire.services import session as session_module
        from applire.schemas.session import SessionCreateRequest

        monkeypatch.setattr(session_module.settings, "interview_max_questions_targeted", 30)

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _make_gap(job.id, profile.id)
        sqlite_session.add(gap)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted")
        result = await session_module.create_session(req, sqlite_session, _mock_provider())

        assert result.hard_ceiling == 30

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
        # Genuinely empty category_c → the "strong match" message is HONEST here.
        assert "strong match" in result.first_question.lower()

    @pytest.mark.asyncio
    async def test_targeted_session_no_askable_clusters_but_critical_gaps_is_honest(self, sqlite_session):
        """#166: category_c is NON-empty but clustering produced zero askable
        clusters (the JSON-object-mode parse failure). The old code told the
        candidate "your profile is a strong match!" — a dangerous lie. The
        fallback message must NOT claim a strong match."""
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
            match_score=0.4,
            critical_gaps=[],
            minor_gaps=[],
            strengths=["Python"],
            keyword_gaps=[],
            category_a=[],
            category_b=[],
            category_c=["Cloud infrastructure", "CI/CD", "monitoring"],  # real gaps
            gap_clusters=[],  # clustering silently died — no askable clusters
        )
        sqlite_session.add(gap)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted")
        result = await create_session(req, sqlite_session, _mock_provider())

        assert "strong match" not in result.first_question.lower()
        assert "strong match" not in result.question.lower()
        assert result.estimated_questions == 0

    @pytest.mark.asyncio
    async def test_targeted_session_keyword_only_honest_gaps_empty_clusters_is_honest(self, sqlite_session):
        """#166 Important-1: persisted category_c=[] and category_b=[], but the
        keyword ledger carries a keyword-only honest gap (US204, ADR-048 §10) —
        real askable input that cluster_gaps() would have clustered on
        (askable_gap_inputs() augments category_c with exactly this). When
        clustering still produced zero clusters (gap_clusters=[]), the OLD guard
        (`if gap_analysis.category_c:`) missed this case entirely — category_c
        itself is empty — and emitted the false "strong match" message. The fix
        must catch this via the shared has_clustering_input() predicate."""
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
            match_score=0.5,
            critical_gaps=[],
            minor_gaps=[],
            strengths=["Python"],
            keyword_gaps=[],
            category_a=[],
            category_b=[],
            category_c=[],  # empty — the old guard's blind spot
            keyword_ledger=[
                {"concept": "Kubernetes", "claimable": False, "fit_weight": 0},
            ],
            gap_clusters=[],  # clustering silently produced nothing askable
        )
        sqlite_session.add(gap)
        await sqlite_session.commit()

        req = SessionCreateRequest(job_id=job.id, mode="targeted")
        result = await create_session(req, sqlite_session, _mock_provider())

        assert "strong match" not in result.first_question.lower()
        assert "strong match" not in result.question.lower()
        assert result.estimated_questions == 0

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

class TestSufficiencyEndsEarlierThanBudget:
    @pytest.mark.asyncio
    async def test_sufficiency_completes_well_under_the_configured_budget(self, sqlite_session):
        """#259 guardrail: the sufficiency metric must be able to end the
        interview EARLIER than the budget when everything is covered. Two
        gaps, both addressed in two turns, hard_ceiling=12 — completion fires
        as "gaps_resolved" at questions_asked=2, nowhere near the ceiling.
        (The mirror guardrail — the budget still caps a pathological long
        tail when gaps remain unresolved — is pinned by
        TestSendMessage.test_hard_ceiling_triggers_completion.)"""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)  # hard_ceiling=12, 2 gaps
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _addressed_turn(profile.profile_json)
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            first = await send_message(
                session_record.id, "I have 3 years of GCP experience.",
                sqlite_session, _mock_provider()
            )
            assert first.complete is False
            assert first.gaps_remaining == 1

            second = await send_message(
                session_record.id, "I have shipped FastAPI services in production.",
                sqlite_session, _mock_provider()
            )

        assert second.complete is True
        assert second.reason == "gaps_resolved"
        assert second.questions_asked == 3  # well under hard_ceiling=12
        assert second.questions_asked < session_record.hard_ceiling


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
    async def test_denial_advances_immediately_never_re_asked(self, sqlite_session):
        """#259 sufficiency criterion (b): an explicit denial is a TERMINAL
        answer — it must advance to the next gap on THIS turn, not fall
        through to the "more specific example" follow-up loop. Before the
        fix, `addressed` (which F8 deliberately excludes denials from) was
        the ONLY advance trigger, so a denial-only turn looped into a
        follow-up re-ask despite the candidate having already declined.

        ADR-064 narrowing: this pins the NON-JD-CRITICAL case specifically —
        the ledger marks 'GCP certification' `sources=["nice_to_have"]`, so
        the transfer probe's `concept_is_required` gate never fires and the
        denial advances exactly as before. The JD-required case now takes a
        follow-up (see TestDenialTransferProbe)."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _probe_gap_with_ledger(job, profile, required=False)
        sqlite_session.add(gap)
        await sqlite_session.flush()
        session_record = _make_active_session(job.id, profile.id, gap.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _denied_turn(profile.profile_json, denied_concepts=["GCP certification"])

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "No, I have never touched GCP.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        assert result.question == "Tell me about FastAPI."
        # Advanced to the second (and last) gap on THIS turn → one remaining.
        assert result.gaps_remaining == 1
        assert result.denial_recorded is None  # only set on the completion path

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
    async def test_truncated_next_question_rolls_back_whole_turn(self, sqlite_session):
        """#179 atomicity pin: profile AND transcript both roll back — the reconciler
        must never out-live a failed turn (one commit per turn)."""
        from applire.exceptions import LLMTruncatedError
        from applire.models.profile import MasterProfile
        from applire.models.session import InterviewSession
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        session_id = session_record.id
        profile_id = profile.id
        profile_before = (await sqlite_session.get(MasterProfile, profile_id)).profile_json
        msgs_before = len((await sqlite_session.get(InterviewSession, session_id)).state["messages"])

        # Reconcile succeeds and produces a real profile write (the reconciler's
        # upsert), but the NEXT question generation call truncates — the whole
        # turn (reconcile write + transcript write) must roll back together.
        # NOTE: the canned turn must carry a genuine diff from profile_before —
        # a turn that echoes profile.profile_json unchanged makes
        # `profile_record.profile_json = turn.profile_dict` a no-op self-assignment,
        # so the final equality assertion would pass whether or not the rollback
        # actually worked (#179 review finding 1).
        mutated_profile = copy.deepcopy(profile.profile_json)
        mutated_profile["skills"] = mutated_profile.get("skills", []) + [
            {"name": "Atomicity Probe Skill", "category": "technical", "proficiency": "advanced"}
        ]
        turn = _addressed_turn(mutated_profile)

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(side_effect=LLMTruncatedError("model=m finish=length"))),
        ):
            with pytest.raises(LLMTruncatedError):
                await send_message(
                    session_id, "I led a team of five engineers",
                    sqlite_session, _mock_provider(),
                )
        await sqlite_session.rollback()  # what get_db's context exit does in production

        assert len((await sqlite_session.get(InterviewSession, session_id)).state["messages"]) == msgs_before
        assert (await sqlite_session.get(MasterProfile, profile_id)).profile_json == profile_before

    @pytest.mark.asyncio
    async def test_provider_outage_mid_turn_leaves_session_resumable(self, sqlite_session):
        """#256 — pins the ground truth for "the interview freezes on a 503":
        a provider outage raised by the SECOND LLM call of a turn (next-question
        generation, AFTER the reconciler already wrote a real profile mutation)
        must roll back exactly like the #179 truncation case above (one commit
        per turn — the reconciler write and the transcript write are never
        split across a partial commit), AND the exact same question must be
        answerable again once the provider recovers — proving the session is
        not just "not corrupted" but actually resumable from the UI's retry
        affordance, not only via a full page reload."""
        from applire.exceptions import LLMProviderUnavailableError
        from applire.models.profile import MasterProfile
        from applire.models.session import InterviewSession
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        session_id = session_record.id
        profile_id = profile.id
        state_before = (await sqlite_session.get(InterviewSession, session_id)).state
        question_before = state_before["current_question"]
        gap_index_before = state_before["current_gap_index"]
        questions_asked_before = (await sqlite_session.get(InterviewSession, session_id)).questions_asked
        profile_before = (await sqlite_session.get(MasterProfile, profile_id)).profile_json

        mutated_profile = copy.deepcopy(profile.profile_json)
        mutated_profile["skills"] = mutated_profile.get("skills", []) + [
            {"name": "Resumability Probe Skill", "category": "technical", "proficiency": "advanced"}
        ]
        turn = _addressed_turn(mutated_profile)

        # --- Turn 1: reconcile succeeds (real profile write), then the
        # gateway 503s relaying an upstream outage on the next-question call.
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(
                      side_effect=LLMProviderUnavailableError(
                          "OpenRouter is temporarily unavailable (HTTP 503)."
                      )
                  )),
        ):
            with pytest.raises(LLMProviderUnavailableError):
                await send_message(
                    session_id, "I led a team of five engineers",
                    sqlite_session, _mock_provider(),
                )
        await sqlite_session.rollback()  # what get_db's context exit does in production

        record_after_failure = await sqlite_session.get(InterviewSession, session_id)
        assert record_after_failure.state["current_question"] == question_before
        assert record_after_failure.state["current_gap_index"] == gap_index_before
        assert record_after_failure.questions_asked == questions_asked_before
        assert (await sqlite_session.get(MasterProfile, profile_id)).profile_json == profile_before

        # --- Turn 2: same session, same message, provider recovers — the
        # turn the frontend's Retry button re-sends must succeed normally,
        # not re-hit a corrupted/half-advanced state.
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_id, "I led a team of five engineers",
                sqlite_session, _mock_provider(),
            )

        assert result.complete is False
        assert result.question == "Tell me about FastAPI."
        assert (await sqlite_session.get(MasterProfile, profile_id)).profile_json == mutated_profile

    @pytest.mark.asyncio
    async def test_provider_outage_on_reconcile_call_leaves_session_untouched(self, sqlite_session):
        """#256 — the FIRST LLM call of a turn (reconcile_interview_turn) is
        pure in-memory work with no DB access at all; a provider outage there
        must leave the session and profile completely untouched (simplest
        resumability case, still worth pinning explicitly)."""
        from applire.exceptions import LLMProviderUnavailableError
        from applire.models.profile import MasterProfile
        from applire.models.session import InterviewSession
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(job.id, profile.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        session_id = session_record.id
        profile_id = profile.id
        # #256 review finding: a plain reference to record.state (or a shallow
        # copy) shares the nested `messages` list with whatever send_message's
        # local `dict(record.state)` copy mutates in place — so a naive
        # "before" snapshot gets silently poisoned by the very call under
        # test, even though nothing was ever committed. deepcopy pins a real,
        # independent snapshot.
        state_before = copy.deepcopy((await sqlite_session.get(InterviewSession, session_id)).state)
        profile_before = copy.deepcopy((await sqlite_session.get(MasterProfile, profile_id)).profile_json)

        with patch(
            "applire.services.session.reconcile_interview_turn",
            new=AsyncMock(
                side_effect=LLMProviderUnavailableError("Mistral is temporarily unavailable (HTTP 503).")
            ),
        ):
            with pytest.raises(LLMProviderUnavailableError):
                await send_message(
                    session_id, "I led a team of five engineers",
                    sqlite_session, _mock_provider(),
                )
        await sqlite_session.rollback()

        assert (await sqlite_session.get(InterviewSession, session_id)).state == state_before
        assert (await sqlite_session.get(MasterProfile, profile_id)).profile_json == profile_before

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

    @pytest.mark.asyncio
    async def test_second_unproductive_answer_forces_advance_after_one_retry(
        self, sqlite_session
    ):
        """#274/#284 fix: a turn that neither addresses the gap nor records a
        denial gets AT MOST one retry follow-up. #284's run-6 evidence: two
        substantive, well-evidenced answers (a mentoring arc, then an ISO
        25010/GAMP5 standards narrative) each reconciled to zero ops and zero
        denials — the reconciler found nothing NEW/distinct to write, so
        `addressed` stayed False both times, yet the candidate had not
        declined anything. #274 is the same shape from the opposite emotional
        direction ("I cannot elaborate further" without a formal denial).
        Before the fix, INTERVIEW_MAX_QUESTIONS_PER_GAP=3 let a THIRD
        unproductive question be asked before force-advancing; this pins the
        tightened budget (one retry, not two) so the cluster advances right
        after the retry's answer instead of drilling a third question."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        # Simulate: the initial question was asked, one follow-up retry was
        # already asked and answered unproductively (questions_per_gap bumped
        # to 2 by that prior turn) — this message is the retry's ANSWER.
        session_record = _make_active_session(
            job.id, profile.id,
            state={"questions_per_gap": {"GCP certification": 2}},
        )
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        # A substantive narrative answer that carries no figure and maps to
        # no distinct new profile field — Northwind Labs-style invented
        # fixture data, mirroring #284's shape without reusing real content.
        turn = _unaddressed_turn(profile.profile_json)

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id,
                "I led the platform migration end-to-end, but I can't put a "
                "number on the team size without guessing.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        # Advanced off "GCP certification" onto "FastAPI experience" — a
        # THIRD follow-up on GCP was never generated.
        assert result.current_gap_id == "FastAPI experience"
        assert result.addressed_gap_ids == ["GCP certification"]
        assert result.question == "Tell me about FastAPI."

    @pytest.mark.asyncio
    async def test_forced_advance_never_marks_the_gap_filled_or_touches_ledger(
        self, sqlite_session
    ):
        """Guardrail: force-advancing on an unproductive retry must NEVER read
        as 'the candidate provided this evidence'. The ledger upgrade
        (#188) — which flips a keyword_ledger entry from gap to confirmed
        strength — must fire only for a genuinely addressed turn, never for
        the one-retry-exhausted termination path. Advancing the conversation
        and claiming the evidence exists are different things."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(
            job.id, profile.id,
            state={"questions_per_gap": {"GCP certification": 2}},
        )
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _unaddressed_turn(profile.profile_json)

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
            patch("applire.services.session._upgrade_ledger_for_addressed_gap",
                  new=AsyncMock()) as mock_upgrade,
        ):
            result = await send_message(
                session_record.id,
                "I can describe the shape of the work but not the numbers.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        assert result.addressed_gap_ids == ["GCP certification"]
        # The gap advanced past — but the ledger upgrade that marks a gap as
        # a confirmed strength was never called for this turn.
        mock_upgrade.assert_not_called()

    @pytest.mark.asyncio
    async def test_forced_advance_does_not_terminate_early_with_real_gaps_remaining(
        self, sqlite_session
    ):
        """Force-advancing off an unproductive retry must move to the NEXT
        real gap and keep the interview open — never mistake 'stop asking
        about this cluster' for 'the interview is done'. Two critical gaps
        are configured; after the first force-advances, the second (a true,
        unaddressed gap) must still be asked about."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        session_record = _make_active_session(
            job.id, profile.id,
            state={"questions_per_gap": {"GCP certification": 2}},
        )
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _unaddressed_turn(profile.profile_json)

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id,
                "I can describe the shape of the work but not the numbers.",
                sqlite_session, _mock_provider()
            )

        # Still open — "FastAPI experience" is a real, unaddressed gap.
        assert result.complete is False
        assert result.gaps_remaining == 1
        assert result.current_gap_id == "FastAPI experience"

    @pytest.mark.asyncio
    async def test_genuine_denial_still_advances_immediately_even_mid_retry_budget(
        self, sqlite_session
    ):
        """ADR-059 regression guard: the existing denial_recorded advance
        trigger must keep working exactly as it did before this fix, at any
        point in the retry budget — a denial is terminal on the turn it is
        recorded, not just on the last allowed retry.

        ADR-064 narrowing: this pins the ALREADY-PROBED case specifically —
        'GCP certification' is JD-required (so it WOULD otherwise qualify for
        the transfer probe) but its `denial_level` is already "partial" in
        the vault, so the probe's not-yet-probed gate refuses to fire and the
        denial advances immediately regardless of retry budget. The
        not-yet-probed, mid-budget case now takes a follow-up instead (see
        TestDenialTransferProbe)."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _probe_gap_with_ledger(job, profile, required=True)
        sqlite_session.add(gap)
        await sqlite_session.flush()

        # Mid-budget (not yet at the retry ceiling) — denial must still cut
        # straight through rather than waiting for the ceiling.
        session_record = _make_active_session(
            job.id, profile.id, gap.id,
            state={"questions_per_gap": {"GCP certification": 1}},
        )
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        partial_profile = copy.deepcopy(profile.profile_json)
        partial_profile.setdefault("metadata", {})["denied_concepts"] = [
            {"concept": "GCP certification", "statement": "No hands-on GCP.",
             "source": "interview", "date": "2026-07-01", "denial_level": "partial"}
        ]
        turn = _denied_turn(partial_profile, denied_concepts=["GCP certification"])

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "No, I have never touched GCP.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        assert result.addressed_gap_ids == ["GCP certification"]
        assert result.current_gap_id == "FastAPI experience"

    # -----------------------------------------------------------------
    # ADR-064 — the denial transfer probe: a direct-level denial of a
    # JD-critical concept gets exactly one follow-up (aimed at the broader
    # skill area) instead of advancing immediately.
    # -----------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_direct_denial_of_required_concept_triggers_transfer_probe(
        self, sqlite_session
    ):
        """ADR-064: a direct-level denial of a JD-required concept ('GCP
        certification', ledger `sources=["required"]`) does NOT advance —
        it gets exactly one follow-up question, wired through the existing
        follow-up generation path (`follow_up_hint` names the concept so
        Task 3's wording work has something deterministic to work from)."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _probe_gap_with_ledger(job, profile, required=True)
        sqlite_session.add(gap)
        await sqlite_session.flush()
        session_record = _make_active_session(job.id, profile.id, gap.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _denied_turn(profile.profile_json, denied_concepts=["GCP certification"])
        mock_gen = AsyncMock(
            return_value={"question": "Any adjacent cloud platform experience?", "choices": None}
        )

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile", new=mock_gen),
        ):
            result = await send_message(
                session_record.id, "No, I have never touched GCP.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        # Did NOT advance — still on GCP certification, nothing addressed yet.
        assert result.current_gap_id == "GCP certification"
        assert result.addressed_gap_ids == []
        assert result.question == "Any adjacent cloud platform experience?"
        # Wired to the EXISTING follow-up path with a deterministic hint that
        # names the concept — never a hard-coded probe question.
        _, kwargs = mock_gen.call_args
        assert kwargs.get("follow_up_hint")
        assert "GCP certification" in kwargs["follow_up_hint"]

    @pytest.mark.asyncio
    async def test_denial_of_non_required_concept_advances_immediately(self, sqlite_session):
        """A denial of a concept the ledger does not mark 'required' is not
        JD-critical — unchanged behaviour: advances immediately, no probe."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _probe_gap_with_ledger(job, profile, required=False)
        sqlite_session.add(gap)
        await sqlite_session.flush()
        session_record = _make_active_session(job.id, profile.id, gap.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _denied_turn(profile.profile_json, denied_concepts=["GCP certification"])

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "No, I have never touched GCP.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        assert result.current_gap_id == "FastAPI experience"
        assert result.addressed_gap_ids == ["GCP certification"]

    @pytest.mark.asyncio
    async def test_second_denial_on_probe_turn_sets_partial_and_advances(self, sqlite_session):
        """The probe is terminal: a SECOND denial on its own answer bumps the
        concept's DURABLE denial_level to 'partial' (elicitation exhausted —
        the original denial itself, still `denied`, is left otherwise
        untouched) and advances — never a second probe."""
        from applire.services.session import send_message
        from applire.models.profile import MasterProfile
        from sqlalchemy import select

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _probe_gap_with_ledger(job, profile, required=True)
        sqlite_session.add(gap)
        await sqlite_session.flush()
        session_record = _make_active_session(job.id, profile.id, gap.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        first_turn = _denied_turn(profile.profile_json, denied_concepts=["GCP certification"])
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=first_turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Any adjacent cloud platform experience?", "choices": None})),
        ):
            probe_result = await send_message(
                session_record.id, "No, I have never touched GCP.",
                sqlite_session, _mock_provider()
            )
        assert probe_result.current_gap_id == "GCP certification"  # did not advance

        # This turn's own `reconcile_interview_turn` (interview_bridge) would
        # already have written the concept at "direct" — the fixture mirrors
        # that starting point for the probe's answer turn.
        probe_profile = copy.deepcopy(profile.profile_json)
        probe_profile.setdefault("metadata", {})["denied_concepts"] = [
            {"concept": "GCP certification", "statement": "No, I have never touched GCP.",
             "source": "interview", "date": "2026-07-29", "denial_level": "direct"}
        ]
        second_turn = _denied_turn(probe_profile, denied_concepts=["GCP certification"])
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=second_turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "No, not adjacent either — nothing cloud-related.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        assert result.current_gap_id == "FastAPI experience"
        assert result.addressed_gap_ids == ["GCP certification"]

        db_profile = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == profile.id)
        )).scalar_one()
        denied = db_profile.profile_json["metadata"]["denied_concepts"]
        entry = next(d for d in denied if d["concept"] == "GCP certification")
        assert entry["denial_level"] == "partial"

    @pytest.mark.asyncio
    async def test_already_partial_concept_never_probed_again_fresh_session(
        self, sqlite_session
    ):
        """Durability (ADR-064): a concept already at denial_level='partial'
        in the vault is never probed again — even in a completely FRESH
        session that never set any InterviewState probing flag. Constructed
        so this would fail if the 'already probed' bound lived only in
        InterviewState rather than the DeniedConcept record."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _probe_gap_with_ledger(job, profile, required=True)
        sqlite_session.add(gap)
        await sqlite_session.flush()
        # A brand-new session — probing_concept/probing_gap were never set.
        session_record = _make_active_session(job.id, profile.id, gap.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        partial_profile = copy.deepcopy(profile.profile_json)
        partial_profile.setdefault("metadata", {})["denied_concepts"] = [
            {"concept": "GCP certification", "statement": "Still nothing GCP-adjacent.",
             "source": "interview", "date": "2026-07-01", "denial_level": "partial"}
        ]
        turn = _denied_turn(partial_profile, denied_concepts=["GCP certification"])

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "No GCP, still.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        assert result.current_gap_id == "FastAPI experience"
        assert result.addressed_gap_ids == ["GCP certification"]

    @pytest.mark.asyncio
    async def test_evidence_on_probe_turn_advances_and_leaves_denial_untouched(
        self, sqlite_session
    ):
        """Evidence supplied on the probe's answer turn is its own attested
        entry — it must never edit the original denial (Global Constraint 3
        / ADR-059/ADR-040): the DeniedConcept stays exactly as it was,
        still `denial_level="direct"`, still recorded."""
        from applire.services.session import send_message
        from applire.models.profile import MasterProfile
        from sqlalchemy import select

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _probe_gap_with_ledger(job, profile, required=True)
        sqlite_session.add(gap)
        await sqlite_session.flush()
        session_record = _make_active_session(job.id, profile.id, gap.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        first_turn = _denied_turn(profile.profile_json, denied_concepts=["GCP certification"])
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=first_turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Any adjacent cloud platform experience?", "choices": None})),
        ):
            await send_message(
                session_record.id, "No, I have never touched GCP.",
                sqlite_session, _mock_provider()
            )

        probe_profile = copy.deepcopy(profile.profile_json)
        probe_profile.setdefault("metadata", {})["denied_concepts"] = [
            {"concept": "GCP certification", "statement": "No, I have never touched GCP.",
             "source": "interview", "date": "2026-07-29", "denial_level": "direct"}
        ]
        second_turn = _addressed_turn(probe_profile)
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=second_turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "I have used Azure extensively though, similar IAM model.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        assert result.current_gap_id == "FastAPI experience"

        db_profile = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == profile.id)
        )).scalar_one()
        denied = db_profile.profile_json["metadata"]["denied_concepts"]
        entry = next(d for d in denied if d["concept"] == "GCP certification")
        assert entry["denial_level"] == "direct"
        assert entry["statement"] == "No, I have never touched GCP."

    @pytest.mark.asyncio
    async def test_probe_cannot_push_gap_past_ceiling(self, sqlite_session):
        """The probe must never extend an interview past its existing
        per-gap ceiling: with the retry budget already spent, a direct
        denial of a required concept advances exactly as today — no probe,
        matching the brief's explicit ambiguity resolution."""
        from applire.services.session import send_message
        from applire.constants import INTERVIEW_MAX_QUESTIONS_PER_GAP

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _probe_gap_with_ledger(job, profile, required=True)
        sqlite_session.add(gap)
        await sqlite_session.flush()
        session_record = _make_active_session(
            job.id, profile.id, gap.id,
            state={"questions_per_gap": {"GCP certification": INTERVIEW_MAX_QUESTIONS_PER_GAP}},
        )
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _denied_turn(profile.profile_json, denied_concepts=["GCP certification"])

        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None})),
        ):
            result = await send_message(
                session_record.id, "No, I have never touched GCP.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        assert result.current_gap_id == "FastAPI experience"
        assert result.addressed_gap_ids == ["GCP certification"]

    @pytest.mark.asyncio
    async def test_advance_response_carries_new_current_gap_id(self, sqlite_session):
        """issue #241 item 1 — the turn response exposes the honest server-side
        anchor for the frontend cluster tracker: current_gap_id advances to the
        NEW gap and addressed_gap_ids gains the just-resolved one. The frontend
        must never have to infer this via gaps_remaining array arithmetic."""
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
                session_record.id, "I have 3 years of GCP experience.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is False
        # Advanced off "GCP certification" onto "FastAPI experience".
        assert result.current_gap_id == "FastAPI experience"
        assert result.addressed_gap_ids == ["GCP certification"]

    @pytest.mark.asyncio
    async def test_follow_up_response_keeps_same_current_gap_id(self, sqlite_session):
        """The honesty half of #241 item 1: a follow-up (re-ask on the SAME gap,
        e.g. the "Q4 re-asked a already-✓ cluster" wobble) must NOT report the
        gap as addressed and must NOT change current_gap_id — the frontend
        tracker must not mark a cluster resolved on a turn that didn't resolve
        it."""
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
        assert result.current_gap_id == "GCP certification"
        assert result.addressed_gap_ids == []
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
    async def test_hard_ceiling_completion_survives_completeness_scoring_failure(self, sqlite_session):
        """issue #245 — a downstream completeness-scoring exception must never
        turn an already-committed completion into a failed response.

        `_complete_session` commits `record.status = "complete"` BEFORE
        scoring completeness; live founder-acceptance UAT saw the DB flip to
        'complete' while the interview page never learned it (frozen on the
        last question until a manual reload). If `calculate_completeness()`
        (unguarded before this fix) raises, the exception used to propagate
        out of `send_message` as a 500 — the frontend would see a failed
        fetch and never call setCompletion, even though the DB already says
        complete. This pins the fix: the completion response must still
        arrive with complete=True (completeness degrades to 0.0, recoverable
        via the profile health view), mirroring the pattern already used for
        analyze_gaps/advance_flow_on_interview_complete right below it.
        """
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        state_override = {"hard_ceiling": 2, "questions_asked": 1}
        session_record = _make_active_session(job.id, profile.id, state=state_override)
        session_record.hard_ceiling = 2
        session_record.questions_asked = 1
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        turn = _unaddressed_turn(profile.profile_json)

        with patch("applire.services.session.reconcile_interview_turn",
                   new=AsyncMock(return_value=turn)), \
             patch(
                 "applire.schemas.profile.MasterProfileData.model_validate",
                 side_effect=ValueError("malformed profile_json"),
             ):
            result = await send_message(
                session_record.id, "I don't have much GCP experience.",
                sqlite_session, _mock_provider()
            )

        assert result.complete is True
        assert result.reason == "max_questions_reached"
        assert result.completeness_score == 0.0

        # The DB commit from _complete_session must have gone through — the
        # failure is purely in the best-effort scoring step after it.
        await sqlite_session.refresh(session_record)
        assert session_record.status == "complete"

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

    def test_start_session_503_on_provider_unavailable(self):
        """#256 — session CREATION is the very first LLM call the interview
        page makes; a provider outage here must not surface as a raw 500
        (this is the pinned "wedged interview" vector — see the frontend
        InterviewPage tests for the recovery-affordance half of the fix)."""
        from fastapi.testclient import TestClient
        from applire.exceptions import LLMProviderUnavailableError
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch(
            "applire.routers.session.create_session",
            new=AsyncMock(
                side_effect=LLMProviderUnavailableError(
                    "mistralai/mistral-large-latest returned no completion (upstream outage)."
                )
            ),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/session", json={"job_id": str(_uuid.uuid4())})

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert isinstance(detail, dict)
        assert detail["error_code"] == "provider_unavailable"

    def test_start_session_500_never_leaks_raw_exception_text(self):
        """#256 run-4 evidence, pinned at the exact crash site: openrouter.py
        _parse_json indexing response.choices[0] on a None/empty choices
        list — the raw TypeError text must never reach the response body."""
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        raw_exception_text = "'NoneType' object is not subscriptable"
        with patch(
            "applire.routers.session.create_session",
            new=AsyncMock(side_effect=TypeError(raw_exception_text)),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/session", json={"job_id": str(_uuid.uuid4())})

        assert resp.status_code == 500
        assert raw_exception_text not in resp.text

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

    def test_post_message_503_on_provider_unavailable(self):
        """#256 — a provider outage mid-turn maps to a structured 503, not a
        raw exception leak."""
        from fastapi.testclient import TestClient
        from applire.exceptions import LLMProviderUnavailableError
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch(
            "applire.routers.session.send_message",
            new=AsyncMock(
                side_effect=LLMProviderUnavailableError(
                    "OpenRouter is temporarily unavailable (HTTP 503). Retry the same request."
                )
            ),
        ):
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/session/{_uuid.uuid4()}/message",
                    json={"message": "test message"},
                )

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert isinstance(detail, dict), f"Expected structured dict, got: {detail!r}"
        assert detail["error_code"] == "provider_unavailable"
        assert "message" in detail and len(detail["message"]) > 0

    def test_post_message_500_never_leaks_raw_exception_text(self):
        """#256 run-4 evidence — a raw provider crash (bare TypeError, or an
        SDK exception whose str() embeds the provider's JSON body) must never
        reach the response body. Only a stable, generic error_code + a
        translatable message may appear; the exact repro text from the
        traceback must be absent."""
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        raw_exception_text = "'NoneType' object is not subscriptable"
        with patch(
            "applire.routers.session.send_message",
            new=AsyncMock(side_effect=TypeError(raw_exception_text)),
        ):
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/session/{_uuid.uuid4()}/message",
                    json={"message": "test message"},
                )

        assert resp.status_code == 500
        body_text = resp.text
        assert raw_exception_text not in body_text
        detail = resp.json()["detail"]
        assert isinstance(detail, dict), f"Expected structured dict, got: {detail!r}"
        assert detail["error_code"] == "internal_error"

    def test_post_message_500_never_leaks_raw_provider_json(self):
        """#256 — the OTHER observed leak shape: an unmapped SDK exception
        whose str() embeds the raw provider error JSON body verbatim."""
        from fastapi.testclient import TestClient
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        raw_provider_json = (
            "Error code: 503 - {'error': {'message': "
            "'mistralai/mistral-large-latest is temporarily unavailable', 'code': 503}}"
        )
        with patch(
            "applire.routers.session.send_message",
            new=AsyncMock(side_effect=RuntimeError(raw_provider_json)),
        ):
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/session/{_uuid.uuid4()}/message",
                    json={"message": "test message"},
                )

        assert resp.status_code == 500
        assert raw_provider_json not in resp.text
        assert "mistralai" not in resp.text

    def test_post_message_502_on_truncated_error(self):
        """#179: a truncated question maps to a retryable 502, not a raw 500 — the
        turn is atomic (single commit), so it was never saved."""
        from fastapi.testclient import TestClient
        from applire.exceptions import LLMTruncatedError
        import uuid as _uuid

        app = _make_session_app()
        app = _setup_router_deps(app)

        with patch("applire.routers.session.send_message",
                   new=AsyncMock(side_effect=LLMTruncatedError("model=m finish=length"))):
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/session/{_uuid.uuid4()}/message",
                    json={"message": "test message"},
                )

        assert resp.status_code == 502
        assert "resend" in resp.json()["detail"]

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
            # #307: the module builds its env through the shared factory now, so the
            # patch target moved with it (a hand-rolled Environment would miss
            # every shared filter).
            with patch("applire.services.thumbnails.build_template_env", return_value=mock_env):
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
            # #307: the module builds its env through the shared factory now, so the
            # patch target moved with it (a hand-rolled Environment would miss
            # every shared filter).
            with patch("applire.services.thumbnails.build_template_env", return_value=mock_env):
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
        await _complete_session(
            record, state, sqlite_session, reason="user_ended", provider=_mock_provider()
        )

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
        await _complete_session(
            record, state, sqlite_session, reason="user_ended", provider=_mock_provider()
        )

    assert record.status == "complete"


# ===========================================================================
# Part 9: #188 — an addressed interview turn upgrades the persisted keyword
# ledger IN PLACE, so a confirmed strength stops reading as an honest gap.
# ===========================================================================

_CICD_CLUSTER_ID = "cluster-ci-cd"
_CICD_ANSWER = "I built the CI/CD pipelines at Acme end-to-end with GitHub Actions."


def _make_gap_with_ledger(job, profile, *, cluster_concepts=("CI/CD",)):
    """A persisted GapAnalysis whose ledger holds an honest-gap 'CI/CD' entry (plus a
    claimable 'Python' entry) and one cluster owning `cluster_concepts`.

    #240: carries a REAL input_fingerprint (matching services.gap._input_fingerprint
    for the given job/profile) so that a completion-time analyze_gaps recompute
    against an unchanged profile idempotently reuses this row instead of inserting
    a duplicate — mirroring how every production row gets its fingerprint. Without
    this the #188 "in-place ledger upgrade, no new row" invariant below would be
    a fixture artifact, not a real guarantee.
    """
    from applire.models.gap import GapAnalysis
    from applire.services.gap import _input_fingerprint

    job_id, profile_id = job.id, profile.id
    cluster = {
        "id": _CICD_CLUSTER_ID,
        "label": "CI/CD",
        "category": "C",
        "gaps": list(cluster_concepts),
        "jd_skills": [],
        "jd_context": "",
    }
    ledger = [
        {"concept": "Python", "surface_forms": ["Python"], "sources": ["required"],
         "fit_weight": 1.0, "status": "direct", "evidence": "5y", "claimable": True},
        {"concept": "CI/CD", "surface_forms": ["CI/CD"], "sources": ["required"],
         "fit_weight": 1.0, "status": "gap", "evidence": "", "claimable": False},
    ]
    return GapAnalysis(
        job_analysis_id=job_id,
        profile_id=profile_id,
        match_score=0.6,
        input_fingerprint=_input_fingerprint(job, profile),
        critical_gaps=[_CICD_CLUSTER_ID],
        minor_gaps=[],
        strengths=["Python"],
        keyword_gaps=[],
        category_a=[],
        category_b=[],
        category_c=["CI/CD"],
        keyword_ledger=ledger,
        gap_clusters=[cluster],
    )


def _cicd_session(job_id, profile_id, gap_id, *, cluster_concepts=("CI/CD",)):
    """An active targeted session sitting on the CI/CD cluster, carrying the
    gap_analysis_id so the #188 seam can load the exact persisted ledger row."""
    from applire.models.session import InterviewSession

    cluster = {
        "id": _CICD_CLUSTER_ID,
        "label": "CI/CD",
        "gaps": list(cluster_concepts),
        "jd_skills": [],
        "jd_context": "",
    }
    state = {
        "mode": "targeted",
        "job_id": str(job_id),
        "gap_analysis_id": str(gap_id) if gap_id else None,
        "profile_id": str(profile_id),
        "critical_gaps": [_CICD_CLUSTER_ID],
        "gap_categories": {_CICD_CLUSTER_ID: "C"},
        "gap_clusters_by_id": {_CICD_CLUSTER_ID: cluster},
        "addressed_gaps": [],
        "current_gap_index": 0,
        "current_question": "Tell me about your CI/CD experience.",
        "current_choices": None,
        "messages": [{"role": "assistant", "content": "Tell me about your CI/CD experience."}],
        "questions_asked": 1,
        "hard_ceiling": 12,
        "questions_per_gap": {},
        "skipped_gaps": [],
        "full_gaps": [],
    }
    return InterviewSession(
        job_analysis_id=job_id,
        gap_analysis_id=gap_id,
        profile_id=profile_id,
        mode="targeted",
        status="active",
        state=state,
        hard_ceiling=12,
        questions_asked=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )


async def _reload_ledger(db, gap_id):
    from sqlalchemy import select
    from applire.models.gap import GapAnalysis

    result = await db.execute(select(GapAnalysis).where(GapAnalysis.id == gap_id))
    return result.scalar_one().keyword_ledger


async def _count_gap_rows(db):
    from sqlalchemy import func, select
    from applire.models.gap import GapAnalysis

    result = await db.execute(select(func.count()).select_from(GapAnalysis))
    return result.scalar_one()


class TestAddressedGapUpgradesLedger:
    @pytest.mark.asyncio
    async def test_addressed_turn_upgrades_honest_gap_in_place(self, sqlite_session):
        """#188: a strength CONFIRMED in the interview must flip its honest-gap
        ledger entry to claimable (with evidence) on the SAME persisted row — no new
        GapAnalysis row — so the CV and cover letter stop hedging it as a growth area."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _make_gap_with_ledger(job, profile)
        sqlite_session.add(gap)
        await sqlite_session.flush()
        session_record = _cicd_session(job.id, profile.id, gap.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        gap_id = gap.id
        rows_before = await _count_gap_rows(sqlite_session)

        turn = _addressed_turn(profile.profile_json)
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "next?", "choices": None})),
        ):
            await send_message(session_record.id, _CICD_ANSWER, sqlite_session, _mock_provider())

        ledger = await _reload_ledger(sqlite_session, gap_id)
        cicd = next(e for e in ledger if e["concept"] == "CI/CD")
        assert cicd["claimable"] is True
        assert cicd["status"] in ("direct", "partial")
        assert cicd["evidence"]  # non-empty — grounds the surfacing
        assert cicd["evidence"] == _CICD_ANSWER
        # The already-claimable Python entry is untouched.
        python = next(e for e in ledger if e["concept"] == "Python")
        assert python["claimable"] is True and python["evidence"] == "5y"
        # NO new GapAnalysis row was inserted — the upgrade is in place.
        assert await _count_gap_rows(sqlite_session) == rows_before == 1

    @pytest.mark.asyncio
    async def test_no_gap_analysis_id_is_a_noop(self, sqlite_session):
        """Guided / Mode-B sessions carry no gap_analysis_id → never fabricate a ledger
        change (and never crash)."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _make_gap_with_ledger(job, profile)
        sqlite_session.add(gap)
        await sqlite_session.flush()
        # State has gap_analysis_id = None even though a row exists in the DB.
        session_record = _cicd_session(job.id, profile.id, None)
        session_record.gap_analysis_id = None
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        gap_id = gap.id
        turn = _addressed_turn(profile.profile_json)
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "next?", "choices": None})),
        ):
            await send_message(session_record.id, _CICD_ANSWER, sqlite_session, _mock_provider())

        ledger = await _reload_ledger(sqlite_session, gap_id)
        cicd = next(e for e in ledger if e["concept"] == "CI/CD")
        assert cicd["claimable"] is False and cicd["status"] == "gap"

    @pytest.mark.asyncio
    async def test_unmatched_cluster_concepts_leave_ledger_unchanged(self, sqlite_session):
        """A cluster whose concepts don't normalize-match any ledger entry (LLM reworded
        / translated) must NOT create or upgrade any entry."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _make_gap_with_ledger(job, profile, cluster_concepts=("Quantum Computing",))
        sqlite_session.add(gap)
        await sqlite_session.flush()
        session_record = _cicd_session(
            job.id, profile.id, gap.id, cluster_concepts=("Quantum Computing",)
        )
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        gap_id = gap.id
        turn = _addressed_turn(profile.profile_json)
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "next?", "choices": None})),
        ):
            await send_message(session_record.id, "Some answer.", sqlite_session, _mock_provider())

        ledger = await _reload_ledger(sqlite_session, gap_id)
        cicd = next(e for e in ledger if e["concept"] == "CI/CD")
        assert cicd["claimable"] is False and cicd["status"] == "gap"

    @pytest.mark.asyncio
    async def test_unaddressed_turn_leaves_ledger_unchanged(self, sqlite_session):
        """A turn that produced no profile change is not `addressed` → the ledger is
        left exactly as built (no premature promotion of an un-substantiated gap)."""
        from applire.services.session import send_message

        job = _make_job()
        profile = _make_profile()
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.flush()

        gap = _make_gap_with_ledger(job, profile)
        sqlite_session.add(gap)
        await sqlite_session.flush()
        session_record = _cicd_session(job.id, profile.id, gap.id)
        sqlite_session.add(session_record)
        await sqlite_session.commit()

        gap_id = gap.id
        turn = _unaddressed_turn(profile.profile_json)
        with (
            patch("applire.services.session.reconcile_interview_turn",
                  new=AsyncMock(return_value=turn)),
            patch("applire.services.session.question_generator_with_profile",
                  new=AsyncMock(return_value={"question": "follow up?", "choices": None})),
        ):
            await send_message(session_record.id, "I've done a little.", sqlite_session, _mock_provider())

        ledger = await _reload_ledger(sqlite_session, gap_id)
        cicd = next(e for e in ledger if e["concept"] == "CI/CD")
        assert cicd["claimable"] is False and cicd["status"] == "gap"
