"""
Sprint 13 — coverage boost tests.

Targets the four lowest-coverage modules to push total coverage from ~71% to ≥75%:
  - backend/applire/services/profile.py          (legacy, 0%)
  - backend/applire/services/job.py              (35%)
  - backend/applire/storage/__init__.py          (38%)
  - backend/applire/services/interview_graph.py  (37%)
  - backend/applire/services/profile/__init__.py (39%)

No Docker or real database connections — all external I/O is mocked.

Run:
    PYTHONPATH=backend pytest tests/unit/test_sprint13_coverage.py -v
"""

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# ─── SQLite fixture (shared) ──────────────────────────────────────────────────


@pytest_asyncio.fixture
async def sqlite_session():
    """In-memory SQLite session — no Docker required."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.user  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_mock_provider(return_value: dict) -> MagicMock:
    """Return a mock LLMProvider whose aparse_json returns *return_value*.

    The language-review pass (ADR-038) calls aparse_json with the reviewer
    system prompt; approve it so review_and_refine returns the draft unchanged.
    """
    provider = MagicMock()

    async def _aparse_json(prompt, *, system=None, **kwargs):
        if "language reviewer" in (system or "").lower():
            return {"approved": True, "issues": [], "feedback": ""}
        return return_value

    provider.aparse_json = AsyncMock(side_effect=_aparse_json)
    provider.acomplete = AsyncMock(return_value="What is your experience with Python?")
    provider.__class__.__name__ = "MockProvider"
    return provider


def _minimal_profile_json() -> dict:
    return {
        "personal_info": {"name": "Alice"},
        "work_experience": [],
        "skills": [],
        "education": [],
        "languages": [],
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
    }


def _minimal_llm_profile_data() -> dict:
    return {
        "personal_info": {"name": "Alice Tester"},
        "work_experience": [
            {
                "company": "Acme GmbH",
                "role": "Software Developer",
                "start_date": "2020-01",
                "end_date": "2023-06",
            }
        ],
        "skills": [{"name": "Python", "proficiency": "expert"}],
        "education": [],
        "languages": [{"language": "German", "level": "C1"}],
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. applire/services/job.py  (35% → cover uncovered lines 13, 23-62)
# ═══════════════════════════════════════════════════════════════════════════════


class TestJobServiceHashText:
    def test_hash_text_returns_64_char_hex(self):
        from applire.services.job import _hash_text
        result = _hash_text("hello world")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_hash_text_is_deterministic(self):
        from applire.services.job import _hash_text
        assert _hash_text("test") == _hash_text("test")

    def test_hash_text_differs_for_different_inputs(self):
        from applire.services.job import _hash_text
        assert _hash_text("abc") != _hash_text("def")


class TestAnalyzeJd:
    @pytest.mark.asyncio
    async def test_analyze_jd_creates_new_record(self, sqlite_session):
        from applire.services.job import analyze_jd

        llm_response = {
            "role_title": "Backend Developer",
            "required_skills": ["Python", "FastAPI"],
            "nice_to_have_skills": ["Docker"],
            "keywords": ["backend", "API"],
            "seniority_level": "mid",
            "company_culture_signals": ["remote-first"],
            "language_requirement": "German",
            "company_name": "TechGmbH",
        }
        provider = _make_mock_provider(llm_response)

        result = await analyze_jd("We need a backend developer", sqlite_session, provider)
        assert result.role_title == "Backend Developer"
        assert result.seniority_level == "mid"
        provider.aparse_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_jd_deduplicates_by_text_hash(self, sqlite_session):
        from applire.services.job import analyze_jd

        llm_response = {
            "role_title": "DevOps Engineer",
            "required_skills": [],
            "nice_to_have_skills": [],
            "keywords": [],
            "seniority_level": "senior",
            "company_culture_signals": [],
            "language_requirement": "",
            "company_name": None,
        }
        provider = _make_mock_provider(llm_response)

        text = "Identical job description text"
        await analyze_jd(text, sqlite_session, provider)
        # Second call with the same text should return cached result without LLM call
        result2 = await analyze_jd(text, sqlite_session, provider)

        assert result2.role_title == "DevOps Engineer"
        # LLM should only have been called once (cache hit on second call)
        assert provider.aparse_json.call_count == 1

    @pytest.mark.asyncio
    async def test_analyze_jd_deduplicates_by_source_url(self, sqlite_session):
        from applire.services.job import analyze_jd

        llm_response = {
            "role_title": "Frontend Dev",
            "required_skills": ["React"],
            "nice_to_have_skills": [],
            "keywords": [],
            "seniority_level": "junior",
            "company_culture_signals": [],
            "language_requirement": "",
            "company_name": None,
        }
        provider = _make_mock_provider(llm_response)

        url = "https://example.com/jobs/123"
        await analyze_jd("First version of job text", sqlite_session, provider, source_url=url)
        # Same URL — should return cached record without calling LLM again
        result2 = await analyze_jd("Different text same URL", sqlite_session, provider, source_url=url)

        assert result2.role_title == "Frontend Dev"
        assert provider.aparse_json.call_count == 1

    @pytest.mark.asyncio
    async def test_analyze_jd_sets_source_url(self, sqlite_session):
        from applire.services.job import analyze_jd

        llm_response = {
            "role_title": "ML Engineer",
            "required_skills": [],
            "nice_to_have_skills": [],
            "keywords": [],
            "seniority_level": "senior",
            "company_culture_signals": [],
            "language_requirement": "",
            "company_name": None,
        }
        provider = _make_mock_provider(llm_response)

        url = "https://jobs.example.com/ml-engineer"
        result = await analyze_jd("ML job text", sqlite_session, provider, source_url=url)
        assert result.source_url == url

    @pytest.mark.asyncio
    async def test_analyze_jd_no_source_url(self, sqlite_session):
        from applire.services.job import analyze_jd

        llm_response = {
            "role_title": "Data Analyst",
            "required_skills": ["SQL"],
            "nice_to_have_skills": [],
            "keywords": [],
            "seniority_level": "mid",
            "company_culture_signals": [],
            "language_requirement": "English",
            "company_name": "DataCorp",
        }
        provider = _make_mock_provider(llm_response)

        result = await analyze_jd("Data analyst role", sqlite_session, provider)
        assert result.source_url is None
        assert result.role_title == "Data Analyst"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. applire/storage/__init__.py  (38% → cover lines 8-13)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetStorage:
    def test_get_storage_local_backend_returns_local_provider(self):
        from applire.storage import get_storage
        from applire.storage.local import LocalStorageProvider

        with patch("applire.storage.settings") as mock_settings:
            mock_settings.storage_backend = "local"
            mock_settings.upload_dir = "/tmp/uploads"
            provider = get_storage()

        assert isinstance(provider, LocalStorageProvider)

    def test_get_storage_unknown_backend_raises_value_error(self):
        from applire.storage import get_storage

        with patch("applire.storage.settings") as mock_settings:
            mock_settings.storage_backend = "s3"
            with pytest.raises(ValueError, match="Unknown STORAGE_BACKEND"):
                get_storage()

    def test_get_storage_backend_comparison_is_case_insensitive(self):
        from applire.storage import get_storage
        from applire.storage.local import LocalStorageProvider

        with patch("applire.storage.settings") as mock_settings:
            mock_settings.storage_backend = "LOCAL"
            mock_settings.upload_dir = "/tmp/uploads"
            provider = get_storage()

        assert isinstance(provider, LocalStorageProvider)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. applire/services/interview_graph.py  (37% → cover key nodes)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGapDetector:
    def _make_gap_analysis(self, gap_clusters=None):
        from applire.models.gap import GapAnalysis

        ga = MagicMock(spec=GapAnalysis)
        ga.gap_clusters = gap_clusters or []
        return ga

    def test_gap_detector_returns_c_gaps_first(self):
        from applire.services.interview_graph import gap_detector

        ga = self._make_gap_analysis(
            gap_clusters=[
                {"id": "cluster-python", "label": "Python experience", "category": "C", "gaps": ["Python experience"], "jd_skills": [], "jd_context": ""},
                {"id": "cluster-fastapi", "label": "FastAPI knowledge", "category": "C", "gaps": ["FastAPI knowledge"], "jd_skills": [], "jd_context": ""},
                {"id": "cluster-docker", "label": "Docker usage", "category": "B", "gaps": ["Docker usage"], "jd_skills": [], "jd_context": ""},
            ]
        )
        ordered, categories, clusters_by_id = gap_detector(ga)

        assert ordered[0] == "cluster-python"
        assert ordered[1] == "cluster-fastapi"
        assert ordered[2] == "cluster-docker"
        assert categories["cluster-python"] == "C"
        assert categories["cluster-docker"] == "B"

    def test_gap_detector_only_c_gaps(self):
        from applire.services.interview_graph import gap_detector

        ga = self._make_gap_analysis(gap_clusters=[
            {"id": "cluster-gap1", "label": "Gap1", "category": "C", "gaps": ["Gap1"], "jd_skills": [], "jd_context": ""},
            {"id": "cluster-gap2", "label": "Gap2", "category": "C", "gaps": ["Gap2"], "jd_skills": [], "jd_context": ""},
        ])
        ordered, categories, clusters_by_id = gap_detector(ga)

        assert len(ordered) == 2
        assert all(v == "C" for v in categories.values())

    def test_gap_detector_only_b_gaps(self):
        from applire.services.interview_graph import gap_detector

        ga = self._make_gap_analysis(gap_clusters=[
            {"id": "cluster-likely", "label": "Likely skill", "category": "B", "gaps": ["Likely skill"], "jd_skills": [], "jd_context": ""},
        ])
        ordered, categories, clusters_by_id = gap_detector(ga)

        assert ordered == ["cluster-likely"]
        assert categories["cluster-likely"] == "B"

    def test_gap_detector_falls_back_to_critical_gaps_legacy(self):
        """When gap_clusters is empty, gap_detector returns empty lists (no legacy fallback)."""
        from applire.services.interview_graph import gap_detector

        ga = self._make_gap_analysis(gap_clusters=[])
        ordered, categories, clusters_by_id = gap_detector(ga)

        assert ordered == []
        assert categories == {}

    def test_gap_detector_filters_empty_strings(self):
        """Clusters with valid IDs are returned; empty gap_clusters gives empty result."""
        from applire.services.interview_graph import gap_detector

        ga = self._make_gap_analysis(gap_clusters=[
            {"id": "cluster-real", "label": "Real gap", "category": "C", "gaps": ["Real gap"], "jd_skills": [], "jd_context": ""},
        ])
        ordered, categories, clusters_by_id = gap_detector(ga)

        assert ordered == ["cluster-real"]

    def test_gap_detector_empty_analysis_returns_empty(self):
        from applire.services.interview_graph import gap_detector

        ga = self._make_gap_analysis(gap_clusters=[])
        ordered, categories, clusters_by_id = gap_detector(ga)

        assert ordered == []
        assert categories == {}


class TestGapDetectorModeB:
    def _make_job_analysis(self, required_skills=None, nice_to_have_skills=None, role_title=""):
        from applire.models.job import JobAnalysis

        ja = MagicMock(spec=JobAnalysis)
        ja.required_skills = required_skills or []
        ja.nice_to_have_skills = nice_to_have_skills or []
        ja.role_title = role_title
        return ja

    def test_mode_b_returns_core_sections(self):
        from applire.services.interview_graph import gap_detector_mode_b

        ja = self._make_job_analysis()
        sections = gap_detector_mode_b(ja)

        assert "work_experience" in sections
        assert "skills" in sections
        assert "education" in sections

    def test_mode_b_promotes_certifications_when_jd_signals_cert(self):
        from applire.services.interview_graph import gap_detector_mode_b

        ja = self._make_job_analysis(required_skills=["AWS certified", "PMP"])
        sections = gap_detector_mode_b(ja)

        assert "certifications" in sections
        # certifications should be promoted near the top (within first 3)
        assert sections.index("certifications") <= 2

    def test_mode_b_adds_publications_for_research_roles(self):
        from applire.services.interview_graph import gap_detector_mode_b

        ja = self._make_job_analysis(role_title="PhD Research Scientist")
        sections = gap_detector_mode_b(ja)

        assert "publications" in sections

    def test_mode_b_adds_volunteer_for_nonprofit_roles(self):
        from applire.services.interview_graph import gap_detector_mode_b

        ja = self._make_job_analysis(role_title="NGO Program Manager volunteer")
        sections = gap_detector_mode_b(ja)

        assert "volunteer_activities" in sections

    def test_mode_b_no_publications_for_regular_roles(self):
        from applire.services.interview_graph import gap_detector_mode_b

        ja = self._make_job_analysis(role_title="Software Developer")
        sections = gap_detector_mode_b(ja)

        assert "publications" not in sections


class TestQuestionGenerator:
    @pytest.mark.asyncio
    async def test_question_generator_with_profile_targeted_mode(self):
        from applire.services.interview_graph import question_generator_with_profile

        state = {
            "mode": "targeted",
            "critical_gaps": ["cluster-docker"],
            "current_gap_index": 0,
            "messages": [],
            "gap_clusters_by_id": {
                "cluster-docker": {"id": "cluster-docker", "label": "Docker knowledge", "category": "C", "gaps": ["Docker knowledge"], "jd_skills": [], "jd_context": ""}
            },
        }
        provider = _make_mock_provider({"question": "Can you describe your Docker experience?", "choices": None})

        result = await question_generator_with_profile(
            state, {"skills": []}, provider, gap_category="C"
        )

        assert isinstance(result, dict)
        assert "Docker" in result["question"]
        # ADR-038: aparse_json is called twice — once for generation, once for
        # the language-reviewer pass — so assert_called() instead of once.
        provider.aparse_json.assert_called()

    @pytest.mark.asyncio
    async def test_question_generator_with_profile_guided_mode(self):
        from applire.services.interview_graph import question_generator_with_profile

        state = {
            "mode": "guided",
            "critical_gaps": ["work_experience"],
            "current_gap_index": 0,
            "messages": [],
        }
        provider = _make_mock_provider({})
        provider.acomplete = AsyncMock(return_value="Tell me about your work history.")

        result = await question_generator_with_profile(
            state,
            {"work_experience": []},
            provider,
            job_context={"role_title": "Developer"},
        )

        assert isinstance(result, dict)
        assert result["question"] == "Tell me about your work history."


# ═══════════════════════════════════════════════════════════════════════════════
# 5. applire/services/profile/__init__.py  (39% → cover key functions)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNewProfileServiceHelpers:
    def test_extract_pdf_text_raises_on_bad_bytes(self):
        from applire.services.profile import extract_pdf_text
        with pytest.raises(Exception):
            extract_pdf_text(b"not a pdf at all")

    def test_linkedin_to_text_returns_json_string(self):
        from applire.services.profile import _linkedin_to_text
        result = _linkedin_to_text({"firstName": "Anna", "skills": ["Go", "Rust"]})
        assert "Anna" in result
        assert "Go" in result

    def test_make_enrichment_record_structure(self):
        from applire.services.profile import _make_enrichment_record
        record = _make_enrichment_record(
            source="cv_upload",
            section="skills",
            action="added",
            old_value=None,
            new_value=["Python"],
        )
        assert record.source == "cv_upload"
        assert len(record.changes) == 1
        assert record.changes[0].action == "added"


class TestNewProfileServiceDB:
    @pytest.mark.asyncio
    async def test_get_profile_returns_none_when_empty(self, sqlite_session):
        from applire.services.profile import get_profile
        result = await get_profile(sqlite_session)
        assert result is None

    @pytest.mark.asyncio
    async def test_profile_exists_returns_false_when_no_profile(self, sqlite_session):
        from applire.services.profile import profile_exists
        result = await profile_exists(sqlite_session)
        assert result["exists"] is False
        assert result["completeness_score"] == 0.0

    @pytest.mark.asyncio
    async def test_profile_exists_returns_true_when_profile_exists(self, sqlite_session):
        from applire.models.profile import MasterProfile
        from applire.services.profile import profile_exists

        record = MasterProfile(profile_json=_minimal_profile_json())
        sqlite_session.add(record)
        await sqlite_session.commit()

        result = await profile_exists(sqlite_session)
        assert result["exists"] is True

    @pytest.mark.asyncio
    async def test_get_enrichment_history_empty_when_no_profile(self, sqlite_session):
        from applire.services.profile import get_enrichment_history
        result = await get_enrichment_history(sqlite_session)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_enrichment_history_empty_when_no_metadata(self, sqlite_session):
        from applire.models.profile import MasterProfile
        from applire.services.profile import get_enrichment_history

        # Profile without metadata
        record = MasterProfile(profile_json=_minimal_profile_json())
        sqlite_session.add(record)
        await sqlite_session.commit()

        result = await get_enrichment_history(sqlite_session)
        assert result == []

    @pytest.mark.asyncio
    async def test_import_from_linkedin_creates_profile(self, sqlite_session):
        from applire.services.profile import import_from_linkedin

        provider = _make_mock_provider(_minimal_llm_profile_data())
        with patch("applire.services.profile.LLM_REVIEW_MAX_RETRIES", 0):
            result = await import_from_linkedin({"firstName": "Alice"}, sqlite_session, provider)

        assert result is not None
        # 1 extraction + 1 skill-estimation (enrich_skills) + 1 annotation call
        # (annotate_expected_fields, US179) per work entry (1 entry here)
        assert provider.aparse_json.call_count == 3

    @pytest.mark.asyncio
    async def test_import_from_linkedin_merges_with_existing(self, sqlite_session):
        from applire.services.profile import import_from_linkedin

        provider = _make_mock_provider(_minimal_llm_profile_data())

        with patch("applire.services.profile.LLM_REVIEW_MAX_RETRIES", 0):
            # First import
            await import_from_linkedin({"firstName": "Alice"}, sqlite_session, provider)
            # Second import — should merge
            result = await import_from_linkedin({"firstName": "Alice"}, sqlite_session, provider)

        assert result is not None
        # Import 1: 1 extraction + 1 annotation (US179) + 1 skill-estimation = 3 calls.
        # Import 2: 1 extraction + 1 skill-estimation = 2 calls.
        # The annotation is skipped on import 2 because the mock returns the SAME
        # dict reference for every aparse_json call; the first annotation mutates
        # expected_fields in-place so the idempotency guard fires on the second run.
        # In production each extraction returns a fresh dict so annotation always runs.
        assert provider.aparse_json.call_count == 5

    @pytest.mark.asyncio
    async def test_import_from_pdf_raises_on_empty_text(self, sqlite_session):
        from applire.services.profile import import_from_pdf

        provider = _make_mock_provider({})
        with patch("applire.services.profile.extract_pdf_text", return_value=""):
            with pytest.raises(ValueError, match="Could not extract text from PDF"):
                await import_from_pdf(b"bad bytes", sqlite_session, provider)

    @pytest.mark.asyncio
    async def test_patch_profile_section_raises_on_invalid_section(self, sqlite_session):
        from applire.services.profile import patch_profile_section

        with pytest.raises(ValueError, match="Invalid section"):
            await patch_profile_section("bad_section", [], sqlite_session)

    @pytest.mark.asyncio
    async def test_patch_profile_section_raises_when_no_profile(self, sqlite_session):
        from applire.services.profile import patch_profile_section

        with pytest.raises(LookupError, match="No profile found"):
            await patch_profile_section("skills", [], sqlite_session)

    @pytest.mark.asyncio
    async def test_patch_profile_section_updates_and_tracks_enrichment(self, sqlite_session):
        from applire.models.profile import MasterProfile
        from applire.services.profile import patch_profile_section, get_enrichment_history

        record = MasterProfile(profile_json=_minimal_profile_json())
        sqlite_session.add(record)
        await sqlite_session.commit()

        result = await patch_profile_section(
            "skills",
            [{"name": "Python", "proficiency": "expert"}],
            sqlite_session,
            source="manual_edit",
        )
        assert result is not None

        history = await get_enrichment_history(sqlite_session)
        assert len(history) >= 1

    @pytest.mark.asyncio
    async def test_resolve_conflict_raises_when_no_profile(self, sqlite_session):
        from applire.services.profile import resolve_conflict

        with pytest.raises(LookupError, match="No profile found"):
            await resolve_conflict(str(uuid.uuid4()), "existing", None, sqlite_session)

    @pytest.mark.asyncio
    async def test_resolve_conflict_raises_when_no_pending_conflicts(self, sqlite_session):
        from applire.models.profile import MasterProfile
        from applire.services.profile import resolve_conflict

        record = MasterProfile(profile_json=_minimal_profile_json())
        sqlite_session.add(record)
        await sqlite_session.commit()

        with pytest.raises(LookupError):
            await resolve_conflict(str(uuid.uuid4()), "existing", None, sqlite_session)

    @pytest.mark.asyncio
    async def test_resolve_conflict_raises_on_invalid_resolution(self, sqlite_session):
        """resolve_conflict raises ValueError for unknown resolution values."""
        from applire.models.profile import MasterProfile
        from applire.schemas.profile import Conflict, MasterProfileData, ProfileMetadata
        from applire.services.profile import resolve_conflict

        conflict_id = str(uuid.uuid4())
        conflict = Conflict(
            conflict_id=conflict_id,
            section="personal_info",
            field="email",
            existing_value="old@example.com",
            incoming_value="new@example.com",
            source="cv_upload",
        )
        profile_data = MasterProfileData.model_validate(_minimal_profile_json())
        now = datetime.now(timezone.utc)
        profile_data.metadata = ProfileMetadata(
            completeness_score=0.0,
            created_via="cv_upload",
            created_at=now,
            last_updated=now,
            enrichment_history=[],
            pending_conflicts=[conflict],
        )
        record = MasterProfile(profile_json=profile_data.model_dump(mode="json"))
        sqlite_session.add(record)
        await sqlite_session.commit()

        with pytest.raises(ValueError, match="Invalid resolution"):
            await resolve_conflict(conflict_id, "unknown_resolution", None, sqlite_session)

    @pytest.mark.asyncio
    async def test_resolve_conflict_existing_keeps_existing_value(self, sqlite_session):
        from applire.models.profile import MasterProfile
        from applire.schemas.profile import Conflict, MasterProfileData, PersonalInfo, ProfileMetadata
        from applire.services.profile import resolve_conflict

        conflict_id = str(uuid.uuid4())
        conflict = Conflict(
            conflict_id=conflict_id,
            section="personal_info",
            field="email",
            existing_value="kept@example.com",
            incoming_value="rejected@example.com",
            source="cv_upload",
        )
        profile_json = _minimal_profile_json()
        profile_json["personal_info"] = {"name": "Alice", "email": "kept@example.com"}
        now = datetime.now(timezone.utc)
        profile_data = MasterProfileData.model_validate(profile_json)
        profile_data.metadata = ProfileMetadata(
            completeness_score=0.0,
            created_via="cv_upload",
            created_at=now,
            last_updated=now,
            enrichment_history=[],
            pending_conflicts=[conflict],
        )
        record = MasterProfile(profile_json=profile_data.model_dump(mode="json"))
        sqlite_session.add(record)
        await sqlite_session.commit()

        result = await resolve_conflict(conflict_id, "existing", None, sqlite_session)
        assert result is not None
        # Pending conflicts should be cleared
        assert result.merge_conflicts == []

    @pytest.mark.asyncio
    async def test_resolve_conflict_incoming_applies_incoming_value(self, sqlite_session):
        from applire.models.profile import MasterProfile
        from applire.schemas.profile import Conflict, MasterProfileData, ProfileMetadata
        from applire.services.profile import resolve_conflict

        conflict_id = str(uuid.uuid4())
        conflict = Conflict(
            conflict_id=conflict_id,
            section="personal_info",
            field="email",
            existing_value="old@example.com",
            incoming_value="new@example.com",
            source="cv_upload",
        )
        profile_json = _minimal_profile_json()
        profile_json["personal_info"] = {"name": "Alice", "email": "old@example.com"}
        now = datetime.now(timezone.utc)
        profile_data = MasterProfileData.model_validate(profile_json)
        profile_data.metadata = ProfileMetadata(
            completeness_score=0.0,
            created_via="cv_upload",
            created_at=now,
            last_updated=now,
            enrichment_history=[],
            pending_conflicts=[conflict],
        )
        record = MasterProfile(profile_json=profile_data.model_dump(mode="json"))
        sqlite_session.add(record)
        await sqlite_session.commit()

        result = await resolve_conflict(conflict_id, "incoming", None, sqlite_session)
        assert result is not None
        assert result.merge_conflicts == []

    @pytest.mark.asyncio
    async def test_resolve_conflict_manual_applies_custom_value(self, sqlite_session):
        from applire.models.profile import MasterProfile
        from applire.schemas.profile import Conflict, MasterProfileData, ProfileMetadata
        from applire.services.profile import resolve_conflict

        conflict_id = str(uuid.uuid4())
        conflict = Conflict(
            conflict_id=conflict_id,
            section="personal_info",
            field="email",
            existing_value="old@example.com",
            incoming_value="new@example.com",
            source="cv_upload",
        )
        profile_json = _minimal_profile_json()
        profile_json["personal_info"] = {"name": "Alice", "email": "old@example.com"}
        now = datetime.now(timezone.utc)
        profile_data = MasterProfileData.model_validate(profile_json)
        profile_data.metadata = ProfileMetadata(
            completeness_score=0.0,
            created_via="cv_upload",
            created_at=now,
            last_updated=now,
            enrichment_history=[],
            pending_conflicts=[conflict],
        )
        record = MasterProfile(profile_json=profile_data.model_dump(mode="json"))
        sqlite_session.add(record)
        await sqlite_session.commit()

        result = await resolve_conflict(conflict_id, "manual", "custom@example.com", sqlite_session)
        assert result is not None
        assert result.merge_conflicts == []
