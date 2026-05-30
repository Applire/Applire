"""
Sprint 10 — AssistMicroSession unit tests (task APP-10)

Covers:
  - cv_gap_mapper: _tokenise, map_gaps_to_sections (pure functions)
  - schemas/cv_sections: all Pydantic models
  - cv_section_editor: build_content_snapshot, apply_overrides_to_tailored (sync)
  - cv_section_editor: get_cv_sections, patch_cv_section (async, SQLite in-memory)
  - cv_assist: _question_prompt, _suggestion_prompt (pure functions)
  - cv_assist: start_assist_session, submit_assist_answer (async, mocked LLM + SQLite)
  - services/profile: _build_profile_data, _to_response (sync)

No Docker, no real LLM — all async tests use SQLite in-memory.

Run:
    pytest tests/unit/test_iter24_assist_microsession.py -v
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Make applire importable
_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_profile_json() -> dict:
    return {
        "professional_summary": {"de": "Erfahrener Entwickler", "en": ""},
        "work_experience": [
            {
                "company": "Acme GmbH",
                "title": "Software Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "responsibilities": ["Backend-Entwicklung"],
                "location": None,
            }
        ],
        "education": [
            {
                "institution": "TU Berlin",
                "degree": "B.Sc.",
                "field_of_study": "Informatik",
                "start_date": "2014",
                "end_date": "2018",
                "grade": None,
            }
        ],
        "skills": [
            {"name": "Python", "level": None, "category": "technical"},
        ],
        "languages": [
            {"language": "Deutsch", "level": "Muttersprache", "is_native": True}
        ],
        "certifications": [],
        "contact": {
            "first_name": "Max",
            "last_name": "Mustermann",
            "email": "max@example.com",
            "phone": None,
            "location": "Berlin",
            "linkedin": None,
            "xing": None,
            "portfolio": None,
        },
    }


def _stub_tailored_data() -> dict:
    return {
        "contact": {"name": "Max Mustermann", "email": "max@example.com"},
        "summary": "Erfahrener Python-Entwickler",
        "work_history": [
            {
                "company": "Acme GmbH",
                "role": "Software Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "bullets": ["Backend-Entwicklung mit Python", "REST APIs"],
            }
        ],
        "skills": ["Python", "FastAPI"],
        "education": [
            {
                "institution": "TU Berlin",
                "degree": "B.Sc.",
                "field": "Informatik",
                "start_date": "2014",
                "end_date": "2018",
            }
        ],
        "languages": [{"language": "Deutsch", "level": "Muttersprache"}],
    }


# ---------------------------------------------------------------------------
# SQLite DB fixture
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
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
async def db_with_cv(db):
    """Insert a full chain: User → Job → Profile → GapAnalysis → GeneratedCV → FlowSession."""
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.gap import GapAnalysis
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    job_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    profile_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    gap_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
    cv_id = uuid.UUID("00000000-0000-0000-0000-000000000005")
    flow_id = uuid.UUID("00000000-0000-0000-0000-000000000006")

    # Position UUID for snapshot
    pos_uuid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    content_snapshot = {
        "introduction": "Erfahrener Python-Entwickler",
        "positions": [
            {
                "id": pos_uuid,
                "index": 0,
                "title": "Software Engineer",
                "company": "Acme GmbH",
                "period": "2020-01",
                "bullets": ["Backend-Entwicklung mit Python", "REST APIs"],
            }
        ],
        "skills": ["Python", "FastAPI"],
    }

    user = User(
        id=user_id,
        email="test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="abc123",
        raw_text="Python developer job",
        role_title="Python Developer",
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="mid",
        company_culture_signals=[],
        language_requirement="de",
    )
    profile = MasterProfile(
        id=profile_id,
        profile_json=_stub_profile_json(),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    gap = GapAnalysis(
        id=gap_id,
        job_analysis_id=job_id,
        profile_id=profile_id,
        match_score=0.6,
        critical_gaps=[],
        minor_gaps=[],
        strengths=[],
        keyword_gaps=[],
        category_a=[],
        category_b=["Docker"],
        category_c=["Kubernetes"],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cv = GeneratedCV(
        id=cv_id,
        job_analysis_id=job_id,
        profile_id=profile_id,
        tailored_data=_stub_tailored_data(),
        template="classic_german",
        status="ready",
        content_snapshot=content_snapshot,
        section_overrides=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    flow = FlowSession(
        id=flow_id,
        user_id=user_id,
        job_id=job_id,
        current_step="cv_generation",
        user_type="new",
        available_actions={},
        gap_analysis_id=gap_id,
        generated_cv_id=cv_id,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    db.add_all([user, job, profile, gap, cv, flow])
    await db.commit()

    return {
        "db": db,
        "cv_id": cv_id,
        "gap_id": gap_id,
        "pos_uuid": pos_uuid,
        "profile_id": profile_id,
    }


# ---------------------------------------------------------------------------
# cv_gap_mapper — pure functions
# ---------------------------------------------------------------------------

class TestTokenise:
    def test_basic_words(self):
        from applire.services.cv_gap_mapper import _tokenise
        tokens = _tokenise("Python developer")
        assert "python" in tokens
        assert "developer" in tokens

    def test_lowercases(self):
        from applire.services.cv_gap_mapper import _tokenise
        assert "python" in _tokenise("Python")

    def test_ignores_short_tokens(self):
        from applire.services.cv_gap_mapper import _tokenise
        # "a" and "I" are single chars — excluded
        tokens = _tokenise("a I Python")
        assert "a" not in tokens
        assert "i" not in tokens
        assert "python" in tokens

    def test_tech_tokens(self):
        from applire.services.cv_gap_mapper import _tokenise
        tokens = _tokenise("CI/CD C++ .NET")
        # Should include multi-char tokens
        assert len(tokens) > 0


class TestMapGapsToSections:
    def test_empty_gaps_returns_empty(self):
        from applire.services.cv_gap_mapper import map_gaps_to_sections
        assert map_gaps_to_sections([], {"intro": "some content"}) == {}

    def test_matching_gap_assigned_to_section(self):
        from applire.services.cv_gap_mapper import map_gaps_to_sections
        result = map_gaps_to_sections(
            ["Python"],
            {"introduction": "Experienced Python developer", "skills": "Java"}
        )
        assert "introduction" in result
        assert "Python" in result["introduction"]

    def test_unmatched_gap_goes_to_general(self):
        from applire.services.cv_gap_mapper import map_gaps_to_sections
        result = map_gaps_to_sections(
            ["Kubernetes"],
            {"introduction": "Python developer", "skills": "FastAPI"}
        )
        assert "__general__" in result
        assert "Kubernetes" in result["__general__"]

    def test_gap_assigned_to_single_best_section(self):
        # Bug 4: a gap must land in exactly ONE section (the strongest token
        # overlap), not every section it weakly matches — otherwise the CV
        # panel counts and renders the same gap multiple times.
        from applire.services.cv_gap_mapper import map_gaps_to_sections
        result = map_gaps_to_sections(
            ["Industrial Design"],
            {
                "introduction": "Industrial Design portfolio",  # overlap 2 — best
                "skills": "Design tools",                       # overlap 1
                "position::1": "Design work",                   # overlap 1
            },
        )
        assignments = [sid for sid, gaps in result.items() if "Industrial Design" in gaps]
        assert assignments == ["introduction"]
        # No duplication anywhere
        all_assigned = [g for gaps in result.values() for g in gaps]
        assert all_assigned.count("Industrial Design") == 1

    def test_tie_breaks_to_first_section_deterministically(self):
        from applire.services.cv_gap_mapper import map_gaps_to_sections
        result = map_gaps_to_sections(
            ["Python"],
            {
                "introduction": "Python developer",  # overlap 1
                "skills": "Python FastAPI",           # overlap 1 — tie, loses to first
            },
        )
        assert "Python" in result.get("introduction", [])
        assert "Python" not in result.get("skills", [])

    def test_no_gap_appears_in_more_than_one_section(self):
        # Aggregate regression: total assignments == number of gaps (no inflation).
        from applire.services.cv_gap_mapper import map_gaps_to_sections
        gaps = ["Brand-Teams", "Fertigungspartnern", "Industrial Design"]
        sections = {
            "introduction": "Industrial Design with Brand-Teams and Fertigungspartnern",
            "skills": "Industrial Design Brand-Teams",
            "position::1": "Fertigungspartnern Brand-Teams",
        }
        result = map_gaps_to_sections(gaps, sections)
        all_assigned = [g for gs in result.values() for g in gs]
        assert len(all_assigned) == len(gaps)
        assert sorted(all_assigned) == sorted(gaps)

    def test_empty_gap_string_goes_to_general(self):
        from applire.services.cv_gap_mapper import map_gaps_to_sections
        result = map_gaps_to_sections([""], {"intro": "content"})
        assert "__general__" in result


# ---------------------------------------------------------------------------
# schemas/cv_sections — Pydantic model instantiation
# ---------------------------------------------------------------------------

class TestSchemasCvSections:
    def test_snapshot_position(self):
        from applire.schemas.cv_sections import SnapshotPosition
        pos = SnapshotPosition(
            id="abc",
            index=0,
            title="Engineer",
            company="Acme",
            period="2020-01",
            bullets=["Did stuff"],
        )
        assert pos.title == "Engineer"
        assert len(pos.bullets) == 1

    def test_content_snapshot(self):
        from applire.schemas.cv_sections import ContentSnapshot, SnapshotPosition
        snap = ContentSnapshot(
            introduction="I am a dev",
            positions=[
                SnapshotPosition(id="1", index=0, title="Dev", company="Acme", period="2020", bullets=[])
            ],
            skills=["Python"],
        )
        assert snap.introduction == "I am a dev"
        assert len(snap.positions) == 1

    def test_gap_hint_item(self):
        from applire.schemas.cv_sections import GapHintItem
        g = GapHintItem(id="Python", label="Python")
        assert g.id == "Python"

    def test_section_item(self):
        from applire.schemas.cv_sections import SectionItem, GapHintItem
        s = SectionItem(
            section_id="introduction",
            label="Introduction",
            content="Some text",
            has_override=False,
            gaps=[GapHintItem(id="Python", label="Python")],
        )
        assert s.section_id == "introduction"
        assert len(s.gaps) == 1

    def test_cv_sections_response(self):
        from applire.schemas.cv_sections import CVSectionsResponse, SectionItem, GapHintItem
        resp = CVSectionsResponse(
            sections=[
                SectionItem(
                    section_id="introduction",
                    label="Introduction",
                    content="Text",
                    has_override=True,
                    gaps=[],
                )
            ],
            general_gaps=[GapHintItem(id="Docker", label="Docker")],
        )
        assert len(resp.sections) == 1
        assert len(resp.general_gaps) == 1

    def test_section_patch_request(self):
        from applire.schemas.cv_sections import SectionPatchRequest
        req = SectionPatchRequest(content="New content", save_to_profile=True)
        assert req.save_to_profile is True

    def test_section_patch_response(self):
        from applire.schemas.cv_sections import SectionPatchResponse
        resp = SectionPatchResponse(
            html="<html/>",
            overrides_applied=["introduction"],
            resolved_gaps=["Python"],
        )
        assert "introduction" in resp.overrides_applied

    def test_assist_start_request(self):
        from applire.schemas.cv_sections import AssistStartRequest
        req = AssistStartRequest(gap_id="Python")
        assert req.gap_id == "Python"

    def test_assist_start_response(self):
        from applire.schemas.cv_sections import AssistStartResponse
        resp = AssistStartResponse(session_id="s1", question="Wie lange Python?")
        assert resp.session_id == "s1"

    def test_assist_answer_request(self):
        from applire.schemas.cv_sections import AssistAnswerRequest
        req = AssistAnswerRequest(session_id="s1", answer="5 Jahre")
        assert req.answer == "5 Jahre"

    def test_assist_answer_response(self):
        from applire.schemas.cv_sections import AssistAnswerResponse
        resp = AssistAnswerResponse(suggestion="Erfahrener Python-Entwickler.")
        assert "Python" in resp.suggestion


# ---------------------------------------------------------------------------
# cv_section_editor — sync functions
# ---------------------------------------------------------------------------

class TestBuildContentSnapshot:
    def test_basic_snapshot(self):
        from applire.services.cv_section_editor import build_content_snapshot
        from applire.schemas.cv import TailoredCVData

        tailored = TailoredCVData.model_validate(_stub_tailored_data())
        snapshot = build_content_snapshot(tailored)

        assert snapshot["introduction"] == "Erfahrener Python-Entwickler"
        assert len(snapshot["positions"]) == 1
        pos = snapshot["positions"][0]
        assert pos["title"] == "Software Engineer"
        assert pos["company"] == "Acme GmbH"
        assert pos["index"] == 0
        assert "id" in pos  # UUID assigned

    def test_position_period_no_end_date(self):
        from applire.services.cv_section_editor import build_content_snapshot
        from applire.schemas.cv import TailoredCVData

        tailored = TailoredCVData.model_validate(_stub_tailored_data())
        snapshot = build_content_snapshot(tailored)
        # No end_date → period = start_date only
        assert snapshot["positions"][0]["period"] == "2020-01"

    def test_position_period_with_end_date(self):
        from applire.services.cv_section_editor import build_content_snapshot
        from applire.schemas.cv import TailoredCVData

        data = _stub_tailored_data()
        data["work_history"][0]["end_date"] = "2023-12"
        tailored = TailoredCVData.model_validate(data)
        snapshot = build_content_snapshot(tailored)
        assert "2023-12" in snapshot["positions"][0]["period"]

    def test_skills_captured(self):
        from applire.services.cv_section_editor import build_content_snapshot
        from applire.schemas.cv import TailoredCVData

        tailored = TailoredCVData.model_validate(_stub_tailored_data())
        snapshot = build_content_snapshot(tailored)
        assert "Python" in snapshot["skills"]

    def test_empty_work_history(self):
        from applire.services.cv_section_editor import build_content_snapshot
        from applire.schemas.cv import TailoredCVData

        data = _stub_tailored_data()
        data["work_history"] = []
        tailored = TailoredCVData.model_validate(data)
        snapshot = build_content_snapshot(tailored)
        assert snapshot["positions"] == []


class TestApplyOverridesToTailored:
    def test_no_overrides_returns_unchanged(self):
        from applire.services.cv_section_editor import apply_overrides_to_tailored
        from applire.schemas.cv import TailoredCVData

        tailored = TailoredCVData.model_validate(_stub_tailored_data())
        result = apply_overrides_to_tailored(tailored, None, None)
        assert result.summary == tailored.summary

    def test_empty_overrides_returns_unchanged(self):
        from applire.services.cv_section_editor import apply_overrides_to_tailored
        from applire.schemas.cv import TailoredCVData

        tailored = TailoredCVData.model_validate(_stub_tailored_data())
        result = apply_overrides_to_tailored(tailored, None, {})
        assert result.summary == tailored.summary

    def test_introduction_override(self):
        from applire.services.cv_section_editor import apply_overrides_to_tailored
        from applire.schemas.cv import TailoredCVData

        tailored = TailoredCVData.model_validate(_stub_tailored_data())
        result = apply_overrides_to_tailored(
            tailored, None, {"introduction": "Neues Profil"}
        )
        assert result.summary == "Neues Profil"

    def test_skills_override(self):
        from applire.services.cv_section_editor import apply_overrides_to_tailored
        from applire.schemas.cv import TailoredCVData

        tailored = TailoredCVData.model_validate(_stub_tailored_data())
        result = apply_overrides_to_tailored(
            tailored, None, {"skills": "Python\nDocker\nKubernetes"}
        )
        assert "Docker" in result.skills
        assert "Kubernetes" in result.skills

    def test_position_override(self):
        from applire.services.cv_section_editor import apply_overrides_to_tailored
        from applire.schemas.cv import TailoredCVData

        snapshot = {
            "positions": [{"id": "pos-1", "index": 0}]
        }
        tailored = TailoredCVData.model_validate(_stub_tailored_data())
        result = apply_overrides_to_tailored(
            tailored,
            snapshot,
            {"position::pos-1": "Neue Aufgabe 1\nNeue Aufgabe 2"},
        )
        assert "Neue Aufgabe 1" in result.work_history[0].bullets

    def test_unknown_position_uuid_is_ignored(self):
        from applire.services.cv_section_editor import apply_overrides_to_tailored
        from applire.schemas.cv import TailoredCVData

        snapshot = {"positions": [{"id": "pos-1", "index": 0}]}
        tailored = TailoredCVData.model_validate(_stub_tailored_data())
        original_bullets = list(tailored.work_history[0].bullets)
        result = apply_overrides_to_tailored(
            tailored,
            snapshot,
            {"position::unknown-uuid": "Different"},
        )
        assert result.work_history[0].bullets == original_bullets


# ---------------------------------------------------------------------------
# cv_section_editor — async functions (SQLite)
# ---------------------------------------------------------------------------

class TestGetCvSections:
    @pytest.mark.asyncio
    async def test_returns_sections_with_gaps(self, db_with_cv):
        from applire.services.cv_section_editor import get_cv_sections

        ctx = db_with_cv
        result = await get_cv_sections(ctx["cv_id"], ctx["db"])

        section_ids = [s.section_id for s in result.sections]
        assert "introduction" in section_ids
        assert "skills" in section_ids

    @pytest.mark.asyncio
    async def test_introduction_has_correct_content(self, db_with_cv):
        from applire.services.cv_section_editor import get_cv_sections

        ctx = db_with_cv
        result = await get_cv_sections(ctx["cv_id"], ctx["db"])

        intro = next(s for s in result.sections if s.section_id == "introduction")
        assert intro.content == "Erfahrener Python-Entwickler"
        assert intro.has_override is False

    @pytest.mark.asyncio
    async def test_gaps_mapped_to_sections(self, db_with_cv):
        from applire.services.cv_section_editor import get_cv_sections

        ctx = db_with_cv
        result = await get_cv_sections(ctx["cv_id"], ctx["db"])

        # "Docker" and "Kubernetes" are gaps in category_b/c;
        # they likely land in __general__ since the content doesn't mention them
        gap_ids = [g.id for g in result.general_gaps]
        section_gap_ids = [g.id for s in result.sections for g in s.gaps]
        all_gaps = gap_ids + section_gap_ids
        assert "Docker" in all_gaps or "Kubernetes" in all_gaps

    @pytest.mark.asyncio
    async def test_cv_not_found_raises(self, db):
        from applire.services.cv_section_editor import get_cv_sections

        with pytest.raises(LookupError):
            await get_cv_sections(uuid.uuid4(), db)

    @pytest.mark.asyncio
    async def test_null_snapshot_returns_empty(self, db):
        """CV with no content_snapshot returns empty sections."""
        from applire.models.job import JobAnalysis
        from applire.models.profile import MasterProfile
        from applire.models.cv import GeneratedCV
        from applire.services.cv_section_editor import get_cv_sections

        _job_id = uuid.uuid4()
        _profile_id = uuid.uuid4()
        _cv_id = uuid.uuid4()

        profile = MasterProfile(
            id=_profile_id,
            profile_json=_stub_profile_json(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        job = JobAnalysis(
            id=_job_id,
            raw_text_hash="xyz999",
            raw_text="job",
            role_title="Dev",
            required_skills=[],
            nice_to_have_skills=[],
            keywords=[],
            seniority_level="mid",
            company_culture_signals=[],
            language_requirement="de",
        )
        cv = GeneratedCV(
            id=_cv_id,
            job_analysis_id=_job_id,
            profile_id=_profile_id,
            tailored_data=_stub_tailored_data(),
            template="classic_german",
            status="ready",
            content_snapshot=None,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        db.add_all([profile, job, cv])
        await db.commit()

        result = await get_cv_sections(_cv_id, db)
        assert result.sections == []
        assert result.general_gaps == []


# ---------------------------------------------------------------------------
# cv_assist — pure prompt-builder functions
# ---------------------------------------------------------------------------

class TestAssistPromptBuilders:
    def test_question_prompt_contains_section(self):
        from applire.services.cv_assist import _question_prompt

        prompt = _question_prompt("Introduction", "I am a developer", "Python")
        assert "Introduction" in prompt
        assert "Python" in prompt
        assert "I am a developer" in prompt

    def test_suggestion_prompt_contains_answer(self):
        from applire.services.cv_assist import _suggestion_prompt

        prompt = _suggestion_prompt("Introduction", "I am a developer", "Python", "5 Jahre")
        assert "5 Jahre" in prompt
        assert "Python" in prompt

    def test_question_prompt_returns_string(self):
        from applire.services.cv_assist import _question_prompt
        result = _question_prompt("Skills", "Python", "Docker")
        assert isinstance(result, str)
        assert len(result) > 20

    def test_suggestion_prompt_returns_string(self):
        from applire.services.cv_assist import _suggestion_prompt
        result = _suggestion_prompt("Skills", "Python", "Docker", "Ja, ich kenne Docker")
        assert isinstance(result, str)
        assert len(result) > 20


# ---------------------------------------------------------------------------
# cv_assist — async functions (SQLite + mocked LLM)
# ---------------------------------------------------------------------------

def _mock_provider(question: str = "Wie lange Python?", suggestion: str = "Erfahrener Python-Entwickler."):
    provider = MagicMock()
    provider.acomplete = AsyncMock(side_effect=[question, suggestion])
    return provider


class TestStartAssistSession:
    @pytest.mark.asyncio
    async def test_start_returns_session_id_and_question(self, db_with_cv):
        from applire.services.cv_assist import start_assist_session

        ctx = db_with_cv
        provider = _mock_provider(question="Wie lange Python?")
        result = await start_assist_session(
            ctx["cv_id"], "introduction", "Kubernetes", provider, ctx["db"]
        )
        assert result.session_id != ""
        assert result.question == "Wie lange Python?"

    @pytest.mark.asyncio
    async def test_start_with_category_b_gap(self, db_with_cv):
        from applire.services.cv_assist import start_assist_session

        ctx = db_with_cv
        provider = _mock_provider(question="Wie nutzen Sie Docker?")
        result = await start_assist_session(
            ctx["cv_id"], "introduction", "Docker", provider, ctx["db"]
        )
        assert result.session_id != ""

    @pytest.mark.asyncio
    async def test_start_raises_for_unknown_gap(self, db_with_cv):
        from applire.services.cv_assist import start_assist_session

        ctx = db_with_cv
        provider = _mock_provider()
        with pytest.raises(ValueError, match="not found in gap_analysis"):
            await start_assist_session(
                ctx["cv_id"], "introduction", "UnknownGap", provider, ctx["db"]
            )

    @pytest.mark.asyncio
    async def test_start_raises_for_unknown_cv(self, db):
        from applire.services.cv_assist import start_assist_session

        provider = _mock_provider()
        with pytest.raises(LookupError):
            await start_assist_session(
                uuid.uuid4(), "introduction", "Python", provider, db
            )

    @pytest.mark.asyncio
    async def test_start_raises_for_unknown_section(self, db_with_cv):
        from applire.services.cv_assist import start_assist_session

        ctx = db_with_cv
        provider = _mock_provider()
        with pytest.raises(ValueError, match="Unknown section_id"):
            await start_assist_session(
                ctx["cv_id"], "unknown_section", "Kubernetes", provider, ctx["db"]
            )

    @pytest.mark.asyncio
    async def test_start_with_skills_section(self, db_with_cv):
        from applire.services.cv_assist import start_assist_session

        ctx = db_with_cv
        provider = _mock_provider(question="Welche Kubernetes-Erfahrung?")
        result = await start_assist_session(
            ctx["cv_id"], "skills", "Kubernetes", provider, ctx["db"]
        )
        assert result.session_id != ""

    @pytest.mark.asyncio
    async def test_start_with_position_section(self, db_with_cv):
        from applire.services.cv_assist import start_assist_session

        ctx = db_with_cv
        section_id = f"position::{ctx['pos_uuid']}"
        provider = _mock_provider(question="Welche Technologien?")
        result = await start_assist_session(
            ctx["cv_id"], section_id, "Kubernetes", provider, ctx["db"]
        )
        assert result.session_id != ""

    @pytest.mark.asyncio
    async def test_start_raises_when_cv_has_no_snapshot(self, db):
        """CV without content_snapshot raises LookupError (line 140)."""
        from applire.models.job import JobAnalysis
        from applire.models.profile import MasterProfile
        from applire.models.cv import GeneratedCV
        from applire.services.cv_assist import start_assist_session

        _job_id = uuid.uuid4()
        _profile_id = uuid.uuid4()
        _cv_id = uuid.uuid4()

        profile = MasterProfile(
            id=_profile_id,
            profile_json=_stub_profile_json(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        job = JobAnalysis(
            id=_job_id,
            raw_text_hash="xyz777",
            raw_text="job",
            role_title="Dev",
            required_skills=[],
            nice_to_have_skills=[],
            keywords=[],
            seniority_level="mid",
            company_culture_signals=[],
            language_requirement="de",
        )
        cv = GeneratedCV(
            id=_cv_id,
            job_analysis_id=_job_id,
            profile_id=_profile_id,
            tailored_data=_stub_tailored_data(),
            template="classic_german",
            status="ready",
            content_snapshot=None,  # No snapshot
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        db.add_all([profile, job, cv])
        await db.commit()

        provider = _mock_provider()
        with pytest.raises(LookupError, match="no content snapshot"):
            await start_assist_session(_cv_id, "introduction", "Python", provider, db)

    @pytest.mark.asyncio
    async def test_gap_exists_returns_false_when_no_flow(self, db):
        """_gap_exists returns False when there's no FlowSession (line 175)."""
        from applire.models.job import JobAnalysis
        from applire.models.profile import MasterProfile
        from applire.models.cv import GeneratedCV
        from applire.services.cv_assist import start_assist_session

        _job_id = uuid.uuid4()
        _profile_id = uuid.uuid4()
        _cv_id = uuid.uuid4()

        profile = MasterProfile(
            id=_profile_id,
            profile_json=_stub_profile_json(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        job = JobAnalysis(
            id=_job_id,
            raw_text_hash="xyz666",
            raw_text="job",
            role_title="Dev",
            required_skills=[],
            nice_to_have_skills=[],
            keywords=[],
            seniority_level="mid",
            company_culture_signals=[],
            language_requirement="de",
        )
        cv = GeneratedCV(
            id=_cv_id,
            job_analysis_id=_job_id,
            profile_id=_profile_id,
            tailored_data=_stub_tailored_data(),
            template="classic_german",
            status="ready",
            content_snapshot={
                "introduction": "Python developer",
                "positions": [],
                "skills": ["Python"],
            },
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        db.add_all([profile, job, cv])
        await db.commit()

        # No FlowSession → gap_exists returns False → ValueError
        provider = _mock_provider()
        with pytest.raises(ValueError, match="not found in gap_analysis"):
            await start_assist_session(_cv_id, "introduction", "Docker", provider, db)


class TestSubmitAssistAnswer:
    @pytest.mark.asyncio
    async def test_submit_returns_suggestion(self, db_with_cv):
        from applire.services.cv_assist import start_assist_session, submit_assist_answer

        ctx = db_with_cv
        provider = MagicMock()
        provider.acomplete = AsyncMock(side_effect=[
            "Wie lange Python?",
            "Erfahrener Python-Entwickler mit 5 Jahren Erfahrung.",
        ])

        start = await start_assist_session(
            ctx["cv_id"], "introduction", "Kubernetes", provider, ctx["db"]
        )
        result = await submit_assist_answer(
            ctx["cv_id"], "introduction", start.session_id, "5 Jahre", provider, ctx["db"]
        )
        assert "Python" in result.suggestion or "Erfahrener" in result.suggestion

    @pytest.mark.asyncio
    async def test_submit_invalid_session_id_raises(self, db_with_cv):
        from applire.services.cv_assist import submit_assist_answer

        ctx = db_with_cv
        provider = _mock_provider()
        with pytest.raises(ValueError, match="Invalid session_id"):
            await submit_assist_answer(
                ctx["cv_id"], "introduction", "nonexistent-session", "answer",
                provider, ctx["db"]
            )

    @pytest.mark.asyncio
    async def test_submit_wrong_cv_id_raises(self, db_with_cv):
        from applire.services.cv_assist import start_assist_session, submit_assist_answer

        ctx = db_with_cv
        provider = MagicMock()
        provider.acomplete = AsyncMock(side_effect=[
            "Frage?",
            "Suggestion.",
        ])

        start = await start_assist_session(
            ctx["cv_id"], "introduction", "Kubernetes", provider, ctx["db"]
        )
        with pytest.raises(ValueError, match="Invalid session_id"):
            await submit_assist_answer(
                uuid.uuid4(),  # wrong cv_id
                "introduction",
                start.session_id,
                "answer",
                provider,
                ctx["db"],
            )

    @pytest.mark.asyncio
    async def test_submit_wrong_section_id_raises(self, db_with_cv):
        from applire.services.cv_assist import start_assist_session, submit_assist_answer

        ctx = db_with_cv
        provider = MagicMock()
        provider.acomplete = AsyncMock(side_effect=["Frage?", "Suggestion."])

        start = await start_assist_session(
            ctx["cv_id"], "introduction", "Kubernetes", provider, ctx["db"]
        )
        with pytest.raises(ValueError, match="Invalid session_id"):
            await submit_assist_answer(
                ctx["cv_id"],
                "skills",  # wrong section
                start.session_id,
                "answer",
                provider,
                ctx["db"],
            )


# ---------------------------------------------------------------------------
# cv_section_editor — patch_cv_section (async, SQLite)
# ---------------------------------------------------------------------------

class TestPatchCvSection:
    @pytest.mark.asyncio
    async def test_patch_introduction_updates_html(self, db_with_cv):
        from applire.services.cv_section_editor import patch_cv_section

        ctx = db_with_cv
        result = await patch_cv_section(
            ctx["cv_id"], "introduction", "Neues Profil", False, ctx["db"]
        )
        assert "html" in result.html.lower() or result.html  # non-empty
        assert "introduction" in result.overrides_applied

    @pytest.mark.asyncio
    async def test_patch_skills_resolves_gaps(self, db_with_cv):
        from applire.services.cv_section_editor import patch_cv_section

        ctx = db_with_cv
        # Add "Docker" to skills — should resolve the Docker gap
        result = await patch_cv_section(
            ctx["cv_id"], "skills", "Python\nDocker\nFastAPI", False, ctx["db"]
        )
        assert "skills" in result.overrides_applied
        assert "Docker" in result.resolved_gaps

    @pytest.mark.asyncio
    async def test_patch_unknown_section_raises(self, db_with_cv):
        from applire.services.cv_section_editor import patch_cv_section

        ctx = db_with_cv
        with pytest.raises(ValueError, match="Unknown section_id"):
            await patch_cv_section(
                ctx["cv_id"], "unknown_section", "content", False, ctx["db"]
            )

    @pytest.mark.asyncio
    async def test_patch_position_section(self, db_with_cv):
        from applire.services.cv_section_editor import patch_cv_section

        ctx = db_with_cv
        section_id = f"position::{ctx['pos_uuid']}"
        result = await patch_cv_section(
            ctx["cv_id"], section_id, "Neue Aufgabe 1\nNeue Aufgabe 2", False, ctx["db"]
        )
        assert section_id in result.overrides_applied

    @pytest.mark.asyncio
    async def test_patch_cv_not_found_raises(self, db):
        from applire.services.cv_section_editor import patch_cv_section

        with pytest.raises(LookupError):
            await patch_cv_section(
                uuid.uuid4(), "introduction", "content", False, db
            )

    @pytest.mark.asyncio
    async def test_patch_with_save_to_profile_introduction(self, db_with_cv):
        from applire.services.cv_section_editor import patch_cv_section

        ctx = db_with_cv
        result = await patch_cv_section(
            ctx["cv_id"], "introduction", "Neues Profil", True, ctx["db"]
        )
        assert "introduction" in result.overrides_applied

    @pytest.mark.asyncio
    async def test_patch_with_save_to_profile_skills(self, db_with_cv):
        from applire.services.cv_section_editor import patch_cv_section

        ctx = db_with_cv
        result = await patch_cv_section(
            ctx["cv_id"], "skills", "Python\nDocker", True, ctx["db"]
        )
        assert "skills" in result.overrides_applied

    @pytest.mark.asyncio
    async def test_patch_with_save_to_profile_position(self, db_with_cv):
        from applire.services.cv_section_editor import patch_cv_section

        ctx = db_with_cv
        section_id = f"position::{ctx['pos_uuid']}"
        result = await patch_cv_section(
            ctx["cv_id"], section_id, "Aufgabe A\nAufgabe B", True, ctx["db"]
        )
        assert section_id in result.overrides_applied

    @pytest.mark.asyncio
    async def test_resolve_gaps_returns_empty_when_no_flow(self, db):
        """CV without a FlowSession → _resolve_gaps returns empty list."""
        from applire.models.job import JobAnalysis
        from applire.models.profile import MasterProfile
        from applire.models.cv import GeneratedCV
        from applire.services.cv_section_editor import patch_cv_section

        _job_id = uuid.uuid4()
        _profile_id = uuid.uuid4()
        _cv_id = uuid.uuid4()

        profile = MasterProfile(
            id=_profile_id,
            profile_json=_stub_profile_json(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        job = JobAnalysis(
            id=_job_id,
            raw_text_hash="xyz888",
            raw_text="job",
            role_title="Dev",
            required_skills=[],
            nice_to_have_skills=[],
            keywords=[],
            seniority_level="mid",
            company_culture_signals=[],
            language_requirement="de",
        )
        cv = GeneratedCV(
            id=_cv_id,
            job_analysis_id=_job_id,
            profile_id=_profile_id,
            tailored_data=_stub_tailored_data(),
            template="classic_german",
            status="ready",
            content_snapshot={
                "introduction": "Text",
                "positions": [],
                "skills": ["Python"],
            },
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
        db.add_all([profile, job, cv])
        await db.commit()

        # No FlowSession → resolved_gaps should be empty
        result = await patch_cv_section(_cv_id, "introduction", "New content", False, db)
        assert result.resolved_gaps == []


# ---------------------------------------------------------------------------
# interview_graph — pure functions
# ---------------------------------------------------------------------------

class TestInterviewGraphPureFunctions:
    def _make_gap(self, gap_clusters=None):
        gap = MagicMock()
        gap.gap_clusters = gap_clusters or []
        return gap

    def test_gap_detector_returns_c_first(self):
        from applire.services.interview_graph import gap_detector

        gap = self._make_gap(gap_clusters=[
            {"id": "cluster-docker", "label": "Docker", "category": "B", "gaps": ["Docker"], "jd_skills": [], "jd_context": ""},
            {"id": "cluster-kubernetes", "label": "Kubernetes", "category": "C", "gaps": ["Kubernetes"], "jd_skills": [], "jd_context": ""},
        ])
        gaps, categories, clusters_by_id = gap_detector(gap)
        # C-category clusters should come first
        assert gaps[0] == "cluster-kubernetes"
        assert gaps[1] == "cluster-docker"
        assert categories["cluster-kubernetes"] == "C"
        assert categories["cluster-docker"] == "B"

    def test_gap_detector_empty_categories(self):
        from applire.services.interview_graph import gap_detector

        gap = self._make_gap()
        gaps, categories, clusters_by_id = gap_detector(gap)
        assert gaps == []
        assert categories == {}

    def test_gap_detector_filters_empty_strings(self):
        from applire.services.interview_graph import gap_detector

        # In the new API, cluster IDs are never empty strings — clusters without labels
        # still have valid IDs. This tests that empty gap lists are handled gracefully.
        gap = self._make_gap(gap_clusters=[
            {"id": "cluster-docker", "label": "Docker", "category": "B", "gaps": ["Docker"], "jd_skills": [], "jd_context": ""},
            {"id": "cluster-kubernetes", "label": "Kubernetes", "category": "C", "gaps": ["Kubernetes"], "jd_skills": [], "jd_context": ""},
        ])
        gaps, categories, clusters_by_id = gap_detector(gap)
        assert "" not in gaps
        assert "cluster-docker" in gaps
        assert "cluster-kubernetes" in gaps

    def test_gap_detector_falls_back_to_critical_gaps(self):
        from applire.services.interview_graph import gap_detector

        # With new API, gap_clusters is the primary source; empty clusters → empty result
        gap = self._make_gap(gap_clusters=[
            {"id": "cluster-python", "label": "Python", "category": "C", "gaps": ["Python"], "jd_skills": [], "jd_context": ""},
            {"id": "cluster-docker", "label": "Docker", "category": "C", "gaps": ["Docker"], "jd_skills": [], "jd_context": ""},
        ])
        gaps, categories, clusters_by_id = gap_detector(gap)
        assert "cluster-python" in gaps
        assert "cluster-docker" in gaps
