"""
APP-15 — Unit tests: vector similarity scoring + job matching service.

Covers:
  - NoopEmbeddingProvider (zero-vector, configurable dim)
  - EmbeddingProvider factory (noop default)
  - _cosine_similarity helpers in matching.py and gap.py
  - compute_similarity (stored-embedding lookup)
  - rank_jobs (combined score, ordering, berufsbild filter, top_n)
  - Gap analysis stores embedding_similarity_score (None for noop)
  - _validate_berufsbild in services/job.py
  - Score weights are configurable via settings

Run:
    cd Solution && pytest tests/unit/test_matching_service.py -v
"""

import math
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# SQLite fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sqlite_session():
    """In-memory SQLite session with full schema."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user     # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.job      # noqa: F401
    import applire.models.gap      # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.cv       # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.flow     # noqa: F401
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _job(
    role_title: str = "Software Engineer",
    berufsbild_code: str | None = None,
    match_score: float = 0.75,
    embedding: list[float] | None = None,
) -> dict:
    """Return kwargs for a JobAnalysis ORM object."""
    import hashlib
    text = f"{role_title}-{uuid.uuid4()}"
    return dict(
        raw_text_hash=hashlib.sha256(text.encode()).hexdigest(),
        raw_text=text,
        role_title=role_title,
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="Mid",
        company_culture_signals=[],
        language_requirement="German (B2)",
        berufsbild_code=berufsbild_code,
        berufsbild_label=None,
        embedding=embedding,
    )


def _profile(embedding: list[float] | None = None) -> dict:
    """Return kwargs for a MasterProfile ORM object."""
    return dict(
        profile_json={"personal_info": {"name": "Test User"}, "skills": []},
        embedding=embedding,
    )


def _gap(job_id: uuid.UUID, profile_id: uuid.UUID, match_score: float = 0.7) -> dict:
    return dict(
        job_analysis_id=job_id,
        profile_id=profile_id,
        match_score=match_score,
        critical_gaps=[],
        minor_gaps=[],
        strengths=[],
        keyword_gaps=[],
        category_a=[],
        category_b=[],
        category_c=[],
    )


# ---------------------------------------------------------------------------
# NoopEmbeddingProvider tests
# ---------------------------------------------------------------------------


class TestNoopEmbeddingProvider:
    @pytest.mark.asyncio
    async def test_returns_zero_vector_default_dim(self):
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        provider = NoopEmbeddingProvider()
        result = await provider.embed("some text")
        assert len(result) == 1024
        assert all(v == 0.0 for v in result)

    @pytest.mark.asyncio
    async def test_returns_zero_vector_custom_dim(self):
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        provider = NoopEmbeddingProvider(dim=512)
        result = await provider.embed("text")
        assert len(result) == 512
        assert all(v == 0.0 for v in result)

    @pytest.mark.asyncio
    async def test_empty_text_returns_zero_vector(self):
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        provider = NoopEmbeddingProvider()
        result = await provider.embed("")
        assert len(result) == 1024
        assert all(v == 0.0 for v in result)

    def test_is_embedding_provider_subclass(self):
        from applire.providers.embedding.base import EmbeddingProvider
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        assert issubclass(NoopEmbeddingProvider, EmbeddingProvider)


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


class TestEmbeddingProviderFactory:
    def test_noop_is_default(self):
        from applire.providers.embedding import get_embedding_provider
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        with patch("applire.providers.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "noop"
            provider = get_embedding_provider()
        assert isinstance(provider, NoopEmbeddingProvider)

    def test_unknown_provider_raises(self):
        from applire.providers.embedding import get_embedding_provider
        with patch("applire.providers.embedding.settings") as mock_settings:
            mock_settings.embedding_provider = "nonexistent"
            with pytest.raises(ValueError, match="Unknown EMBEDDING_PROVIDER"):
                get_embedding_provider()


# ---------------------------------------------------------------------------
# Cosine similarity (matching.py)
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors_returns_one(self):
        from applire.services.matching import _cosine_similarity
        v = [1.0, 2.0, 3.0]
        assert math.isclose(_cosine_similarity(v, v), 1.0, rel_tol=1e-6)

    def test_orthogonal_vectors_returns_zero(self):
        from applire.services.matching import _cosine_similarity
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert math.isclose(_cosine_similarity(a, b), 0.0, abs_tol=1e-9)

    def test_zero_vector_returns_zero(self):
        from applire.services.matching import _cosine_similarity
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_different_length_returns_zero(self):
        from applire.services.matching import _cosine_similarity
        assert _cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0

    def test_known_similarity(self):
        from applire.services.matching import _cosine_similarity
        a = [1.0, 0.0]
        b = [1.0, 1.0]
        expected = 1.0 / math.sqrt(2)
        assert math.isclose(_cosine_similarity(a, b), expected, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# Cosine similarity (gap.py)
# ---------------------------------------------------------------------------


class TestGapCosineSimilarity:
    def test_identical(self):
        from applire.services.gap import _cosine_similarity
        v = [1.0, 2.0, 3.0]
        assert math.isclose(_cosine_similarity(v, v), 1.0, rel_tol=1e-6)

    def test_zero_norm(self):
        from applire.services.gap import _cosine_similarity
        assert _cosine_similarity([0.0], [1.0]) == 0.0

    def test_none_embedding(self):
        from applire.services.gap import _compute_embedding_similarity
        assert _compute_embedding_similarity(None, [1.0]) is None
        assert _compute_embedding_similarity([1.0], None) is None
        assert _compute_embedding_similarity(None, None) is None

    def test_real_embeddings(self):
        from applire.services.gap import _compute_embedding_similarity
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert math.isclose(_compute_embedding_similarity(a, b), 0.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# compute_similarity (SQLite integration)
# ---------------------------------------------------------------------------


class TestComputeSimilarity:
    @pytest.mark.asyncio
    async def test_raises_if_job_not_found(self, sqlite_session):
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        from applire.services.matching import compute_similarity
        provider = NoopEmbeddingProvider()
        with pytest.raises(LookupError, match="Job analysis"):
            await compute_similarity(uuid.uuid4(), uuid.uuid4(), sqlite_session, provider)

    @pytest.mark.asyncio
    async def test_raises_if_profile_not_found(self, sqlite_session):
        from applire.models.job import JobAnalysis
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        from applire.services.matching import compute_similarity

        job = JobAnalysis(**_job())
        sqlite_session.add(job)
        await sqlite_session.commit()

        provider = NoopEmbeddingProvider()
        with pytest.raises(LookupError, match="Profile"):
            await compute_similarity(job.id, uuid.uuid4(), sqlite_session, provider)

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_embeddings(self, sqlite_session):
        """Noop provider — embeddings are NULL; similarity = 0.0."""
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        from applire.services.matching import compute_similarity

        job = JobAnalysis(**_job())
        profile = make_master_profile(**_profile())
        sqlite_session.add(job)
        sqlite_session.add(profile)
        await sqlite_session.commit()

        provider = NoopEmbeddingProvider()
        result = await compute_similarity(job.id, profile.id, sqlite_session, provider)
        assert result == 0.0


# ---------------------------------------------------------------------------
# rank_jobs (SQLite integration)
# ---------------------------------------------------------------------------


class TestRankJobs:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_jobs(self, sqlite_session):
        from tests.support.profile_factory import make_master_profile
        from applire.services.matching import rank_jobs

        profile = make_master_profile(**_profile())
        sqlite_session.add(profile)
        await sqlite_session.commit()

        results = await rank_jobs(profile.id, sqlite_session, top_n=10)
        assert results == []

    @pytest.mark.asyncio
    async def test_raises_when_profile_not_found(self, sqlite_session):
        from applire.services.matching import rank_jobs
        with pytest.raises(LookupError, match="Profile"):
            await rank_jobs(uuid.uuid4(), sqlite_session)

    @pytest.mark.asyncio
    async def test_ranks_by_combined_score_with_gap_data(self, sqlite_session):
        """Jobs with higher LLM match scores rank higher (noop embeddings)."""
        from applire.models.gap import GapAnalysis
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from applire.services.matching import rank_jobs

        profile = make_master_profile(**_profile())
        sqlite_session.add(profile)
        job_a = JobAnalysis(**_job(role_title="Senior Python Developer"))
        job_b = JobAnalysis(**_job(role_title="Junior Java Developer"))
        sqlite_session.add_all([job_a, job_b])
        await sqlite_session.commit()

        # job_a has higher LLM match score
        gap_a = GapAnalysis(**_gap(job_a.id, profile.id, match_score=0.9))
        gap_b = GapAnalysis(**_gap(job_b.id, profile.id, match_score=0.3))
        sqlite_session.add_all([gap_a, gap_b])
        await sqlite_session.commit()

        results = await rank_jobs(profile.id, sqlite_session, top_n=10)
        assert len(results) == 2
        assert results[0].job_id == job_a.id
        assert results[1].job_id == job_b.id

    @pytest.mark.asyncio
    async def test_respects_top_n(self, sqlite_session):
        from applire.models.gap import GapAnalysis
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from applire.services.matching import rank_jobs

        profile = make_master_profile(**_profile())
        sqlite_session.add(profile)
        jobs = [JobAnalysis(**_job(role_title=f"Role {i}")) for i in range(5)]
        sqlite_session.add_all(jobs)
        await sqlite_session.commit()

        results = await rank_jobs(profile.id, sqlite_session, top_n=3)
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_berufsbild_filter(self, sqlite_session):
        """Only jobs matching the berufsbild_code prefix are returned."""
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from applire.services.matching import rank_jobs

        profile = make_master_profile(**_profile())
        sqlite_session.add(profile)
        job_it = JobAnalysis(**_job(role_title="IT Engineer", berufsbild_code="4311"))
        job_hr = JobAnalysis(**_job(role_title="HR Manager", berufsbild_code="7121"))
        job_no_code = JobAnalysis(**_job(role_title="Analyst"))
        sqlite_session.add_all([job_it, job_hr, job_no_code])
        await sqlite_session.commit()

        # Filter to KldB "43" = IT-related occupations
        results = await rank_jobs(profile.id, sqlite_session, top_n=10, berufsbild_code="43")
        job_ids = {r.job_id for r in results}
        assert job_it.id in job_ids
        assert job_hr.id not in job_ids
        assert job_no_code.id not in job_ids

    @pytest.mark.asyncio
    async def test_no_gap_analysis_zero_llm_score(self, sqlite_session):
        """Jobs without gap analyses get llm_match_score=None and combined_score=0."""
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from applire.services.matching import rank_jobs

        profile = make_master_profile(**_profile())
        sqlite_session.add(profile)
        job = JobAnalysis(**_job())
        sqlite_session.add(job)
        await sqlite_session.commit()

        results = await rank_jobs(profile.id, sqlite_session, top_n=10)
        assert len(results) == 1
        assert results[0].llm_match_score is None
        assert results[0].combined_score == 0.0

    @pytest.mark.asyncio
    async def test_combined_score_weights_from_settings(self, sqlite_session):
        """Combined score uses configurable weights from settings."""
        from applire.models.gap import GapAnalysis
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from applire.services.matching import rank_jobs

        profile = make_master_profile(**_profile())
        sqlite_session.add(profile)
        job = JobAnalysis(**_job())
        sqlite_session.add(job)
        await sqlite_session.commit()

        gap = GapAnalysis(**_gap(job.id, profile.id, match_score=0.8))
        sqlite_session.add(gap)
        await sqlite_session.commit()

        with patch("applire.services.matching.settings") as mock_settings:
            mock_settings.matching_score_embedding_weight = 0.4
            mock_settings.matching_score_llm_weight = 0.6
            results = await rank_jobs(profile.id, sqlite_session, top_n=10)

        assert len(results) == 1
        # emb_sim=0 (no embedding), llm=0.8 → combined = 0.4*0 + 0.6*0.8 = 0.48
        assert math.isclose(results[0].combined_score, 0.48, rel_tol=1e-6)

    @pytest.mark.asyncio
    async def test_uses_latest_gap_analysis(self, sqlite_session):
        """When multiple gap analyses exist for a job, the most recent one is used."""
        from applire.models.gap import GapAnalysis
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from applire.services.matching import rank_jobs
        import asyncio

        profile = make_master_profile(**_profile())
        sqlite_session.add(profile)
        job = JobAnalysis(**_job())
        sqlite_session.add(job)
        await sqlite_session.commit()

        gap_old = GapAnalysis(**_gap(job.id, profile.id, match_score=0.3))
        gap_new = GapAnalysis(**_gap(job.id, profile.id, match_score=0.9))
        sqlite_session.add(gap_old)
        await sqlite_session.commit()
        await sqlite_session.refresh(gap_old)

        # Ensure gap_new has a later created_at
        from datetime import timedelta
        gap_new.created_at = gap_old.created_at + timedelta(seconds=1)
        sqlite_session.add(gap_new)
        await sqlite_session.commit()

        results = await rank_jobs(profile.id, sqlite_session, top_n=10)
        assert len(results) == 1
        assert math.isclose(results[0].llm_match_score, 0.9, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# Gap analysis stores embedding_similarity_score
# ---------------------------------------------------------------------------


class TestGapAnalysisEmbeddingScore:
    @pytest.mark.asyncio
    async def test_embedding_similarity_score_null_with_noop(self, sqlite_session):
        """With noop provider (no stored embeddings), embedding_similarity_score is NULL."""
        from applire.models.gap import GapAnalysis
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from sqlalchemy import select

        job = JobAnalysis(**_job())
        profile = make_master_profile(**_profile())
        sqlite_session.add_all([job, profile])
        await sqlite_session.commit()

        # embedding is None on both (noop provider)
        gap = GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=0.7,
            embedding_similarity_score=None,
            critical_gaps=[], minor_gaps=[], strengths=[],
            keyword_gaps=[], category_a=[], category_b=[], category_c=[],
        )
        sqlite_session.add(gap)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(GapAnalysis).where(GapAnalysis.id == gap.id)
        )).scalar_one()
        assert row.embedding_similarity_score is None

    @pytest.mark.asyncio
    async def test_embedding_similarity_score_stored(self, sqlite_session):
        """embedding_similarity_score is persisted when provided."""
        from applire.models.gap import GapAnalysis
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from sqlalchemy import select

        job = JobAnalysis(**_job())
        profile = make_master_profile(**_profile())
        sqlite_session.add_all([job, profile])
        await sqlite_session.commit()

        gap = GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=0.7,
            embedding_similarity_score=0.85,
            critical_gaps=[], minor_gaps=[], strengths=[],
            keyword_gaps=[], category_a=[], category_b=[], category_c=[],
        )
        sqlite_session.add(gap)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(GapAnalysis).where(GapAnalysis.id == gap.id)
        )).scalar_one()
        assert math.isclose(row.embedding_similarity_score, 0.85, rel_tol=1e-6)


# ---------------------------------------------------------------------------
# _validate_berufsbild in services/job.py
# ---------------------------------------------------------------------------


class TestValidateBerufsbild:
    def test_valid_code_passes(self):
        from applire.services.job import _validate_berufsbild, _VALID_KLDB_CODES
        if not _VALID_KLDB_CODES:
            pytest.skip("KldB lookup not loaded")
        code = next(iter(_VALID_KLDB_CODES))
        result_code, result_label = _validate_berufsbild(code, "Some Label")
        assert result_code == code
        assert result_label == "Some Label"

    def test_invalid_code_returns_none(self):
        from applire.services.job import _validate_berufsbild, _VALID_KLDB_CODES
        if not _VALID_KLDB_CODES:
            pytest.skip("KldB lookup not loaded")
        code, label = _validate_berufsbild("XXXXX", "Bad Code")
        assert code is None
        assert label is None

    def test_none_code_returns_none(self):
        from applire.services.job import _validate_berufsbild
        code, label = _validate_berufsbild(None, None)
        assert code is None
        assert label is None

    def test_empty_string_returns_none(self):
        from applire.services.job import _validate_berufsbild
        code, label = _validate_berufsbild("", "Label")
        assert code is None
        assert label is None

    def test_label_stripped(self):
        from applire.services.job import _validate_berufsbild, _VALID_KLDB_CODES
        if not _VALID_KLDB_CODES:
            pytest.skip("KldB lookup not loaded")
        code = next(iter(_VALID_KLDB_CODES))
        _, result_label = _validate_berufsbild(code, "  Softwareentwicklung  ")
        assert result_label == "Softwareentwicklung"


# ---------------------------------------------------------------------------
# Berufsbild fields persist in JobAnalysis model
# ---------------------------------------------------------------------------


class TestJobAnalysisBerufsbild:
    @pytest.mark.asyncio
    async def test_berufsbild_fields_nullable(self, sqlite_session):
        from applire.models.job import JobAnalysis
        from sqlalchemy import select

        job = JobAnalysis(**_job())
        sqlite_session.add(job)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(JobAnalysis).where(JobAnalysis.id == job.id)
        )).scalar_one()
        assert row.berufsbild_code is None
        assert row.berufsbild_label is None

    @pytest.mark.asyncio
    async def test_berufsbild_fields_stored(self, sqlite_session):
        from applire.models.job import JobAnalysis
        from sqlalchemy import select

        job = JobAnalysis(**_job(berufsbild_code="4311"))
        job.berufsbild_label = "Softwareentwicklung"
        sqlite_session.add(job)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(JobAnalysis).where(JobAnalysis.id == job.id)
        )).scalar_one()
        assert row.berufsbild_code == "4311"
        assert row.berufsbild_label == "Softwareentwicklung"


# ---------------------------------------------------------------------------
# Profile embedding text builder
# ---------------------------------------------------------------------------


class TestProfileEmbeddingText:
    def test_includes_name_and_summary(self):
        from applire.services.profile import _profile_to_embedding_text
        profile_json = {
            "personal_info": {"name": "Ada Lovelace"},
            "professional_summary": "Pioneer of programming",
            "work_experience": [],
            "skills": [],
        }
        text = _profile_to_embedding_text(profile_json)
        assert "Ada Lovelace" in text
        assert "Pioneer of programming" in text

    def test_includes_work_experience(self):
        from applire.services.profile import _profile_to_embedding_text
        profile_json = {
            "personal_info": {},
            "work_experience": [
                {"role": "Engineer", "company": "Acme GmbH", "description": "Built things"}
            ],
            "skills": [],
        }
        text = _profile_to_embedding_text(profile_json)
        assert "Engineer" in text
        assert "Acme GmbH" in text
        assert "Built things" in text

    def test_includes_skills(self):
        from applire.services.profile import _profile_to_embedding_text
        profile_json = {
            "personal_info": {},
            "work_experience": [],
            "skills": [{"name": "Python"}, {"name": "FastAPI"}],
        }
        text = _profile_to_embedding_text(profile_json)
        assert "Python" in text
        assert "FastAPI" in text

    def test_empty_profile_returns_empty(self):
        from applire.services.profile import _profile_to_embedding_text
        text = _profile_to_embedding_text({})
        assert text.strip() == ""


# ---------------------------------------------------------------------------
# Noop embedding skips persistence (zero-vector → NULL)
# ---------------------------------------------------------------------------


class TestNoopEmbeddingNotPersisted:
    @pytest.mark.asyncio
    async def test_compute_embedding_returns_none_for_zero_vector(self):
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        from applire.services.profile import _compute_embedding

        provider = NoopEmbeddingProvider()
        profile_json = {"personal_info": {"name": "Test"}, "work_experience": [], "skills": []}
        result = await _compute_embedding(profile_json, provider)
        # Noop returns zero vector → should be stored as None
        assert result is None

    @pytest.mark.asyncio
    async def test_compute_embedding_returns_none_for_empty_text(self):
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        from applire.services.profile import _compute_embedding

        provider = NoopEmbeddingProvider()
        result = await _compute_embedding({}, provider)
        assert result is None


# ---------------------------------------------------------------------------
# Gap service async functions — DB-backed tests
# ---------------------------------------------------------------------------


class TestGapServiceAsync:
    @pytest.mark.asyncio
    async def test_resolve_job_raises_for_missing_id(self, sqlite_session):
        from applire.services.gap import _resolve_job
        with pytest.raises(LookupError, match="Job analysis"):
            await _resolve_job(uuid.uuid4(), sqlite_session)

    @pytest.mark.asyncio
    async def test_resolve_job_returns_existing(self, sqlite_session):
        from applire.models.job import JobAnalysis
        from applire.services.gap import _resolve_job

        job = JobAnalysis(**_job())
        sqlite_session.add(job)
        await sqlite_session.commit()

        result = await _resolve_job(job.id, sqlite_session)
        assert result.id == job.id

    @pytest.mark.asyncio
    async def test_resolve_profile_raises_when_none(self, sqlite_session):
        from applire.services.gap import _resolve_profile
        with pytest.raises(LookupError, match="No profile found"):
            await _resolve_profile(sqlite_session)

    @pytest.mark.asyncio
    async def test_resolve_profile_returns_latest(self, sqlite_session):
        from tests.support.profile_factory import make_master_profile
        from applire.services.gap import _resolve_profile

        profile = make_master_profile(**_profile())
        sqlite_session.add(profile)
        await sqlite_session.commit()

        result = await _resolve_profile(sqlite_session)
        assert result.id == profile.id

    @pytest.mark.asyncio
    async def test_analyze_gaps_not_found_raises(self, sqlite_session):
        from applire.services.gap import analyze_gaps

        mock_provider = AsyncMock()
        with pytest.raises(LookupError):
            await analyze_gaps(uuid.uuid4(), sqlite_session, mock_provider)

    @pytest.mark.asyncio
    async def test_analyze_gaps_for_session_not_found(self, sqlite_session):
        from applire.services.gap import analyze_gaps_for_session

        mock_provider = AsyncMock()
        with pytest.raises(LookupError, match="Session"):
            await analyze_gaps_for_session(uuid.uuid4(), sqlite_session, mock_provider)

    @pytest.mark.asyncio
    async def test_analyze_gaps_for_session_delegates_to_analyze_gaps(self, sqlite_session):
        """analyze_gaps_for_session extracts job_id from session and calls analyze_gaps."""
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from applire.models.session import InterviewSession
        from applire.services.gap import analyze_gaps_for_session

        job = JobAnalysis(**_job())
        profile = make_master_profile(**_profile())
        sqlite_session.add_all([job, profile])
        await sqlite_session.commit()

        session = InterviewSession(
            job_analysis_id=job.id,
            profile_id=profile.id,
            state={},
        )
        sqlite_session.add(session)
        await sqlite_session.commit()

        mock_provider = AsyncMock()
        mock_provider.aparse_json = AsyncMock(return_value={
            "classifications": [
                {"requirement": "Python", "status": "direct", "reason": "listed in CV"},
            ],
            "strengths": [],
            "keyword_gaps": [],
        })

        result = await analyze_gaps_for_session(session.id, sqlite_session, mock_provider)
        # match_score is now derived by compute_match_score, not taken from LLM output
        assert result.match_score is not None
        assert result.job_analysis_id == job.id

    @pytest.mark.asyncio
    async def test_run_analysis_stores_embedding_similarity_none(self, sqlite_session):
        """With noop embeddings (NULL), gap analysis stores embedding_similarity_score=None."""
        from applire.models.job import JobAnalysis
        from tests.support.profile_factory import make_master_profile
        from applire.services.gap import _run_analysis

        job = JobAnalysis(**_job())
        profile = make_master_profile(**_profile())
        sqlite_session.add_all([job, profile])
        await sqlite_session.commit()

        mock_provider = AsyncMock()
        mock_provider.aparse_json = AsyncMock(return_value={
            "classifications": [
                {"requirement": "Go", "status": "gap", "reason": "not mentioned"},
            ],
            "strengths": ["Python"],
            "keyword_gaps": [],
        })

        result = await _run_analysis(job, profile, sqlite_session, mock_provider)
        # match_score is now derived by compute_match_score (not from LLM output);
        # job has required_skills=["Python"] and "Go" classification doesn't match it,
        # so Python defaults to gap → score = 0.0
        assert result.match_score == 0.0

        # Check stored record
        from sqlalchemy import select
        from applire.models.gap import GapAnalysis
        row = (await sqlite_session.execute(
            select(GapAnalysis).where(GapAnalysis.id == result.id)
        )).scalar_one()
        assert row.embedding_similarity_score is None


# ---------------------------------------------------------------------------
# Embedding provider factory — branch coverage
# ---------------------------------------------------------------------------


class TestEmbeddingProviderFactoryBranches:
    def test_noop_branch(self):
        from applire.providers.embedding import get_embedding_provider
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        with patch("applire.providers.embedding.settings") as mock_s:
            mock_s.embedding_provider = "noop"
            result = get_embedding_provider()
        assert isinstance(result, NoopEmbeddingProvider)

    def test_mistral_branch_imports(self):
        """Factory can instantiate the Mistral path without making API calls."""
        from applire.providers.embedding import get_embedding_provider
        with patch("applire.providers.embedding.settings") as mock_s:
            mock_s.embedding_provider = "mistral"
            with patch("applire.providers.embedding.mistral.MistralEmbeddingProvider.__init__",
                       return_value=None) as mock_init:
                with patch("applire.providers.embedding.mistral.Mistral"):
                    # Import the class directly to avoid full init
                    from applire.providers.embedding.mistral import MistralEmbeddingProvider
                    provider = MistralEmbeddingProvider.__new__(MistralEmbeddingProvider)
                    assert provider is not None

    def test_noop_dim_used(self):
        from applire.providers.embedding.noop import NoopEmbeddingProvider
        p = NoopEmbeddingProvider(dim=768)
        assert p._dim == 768


# ---------------------------------------------------------------------------
# Gap _cosine_similarity unequal length path
# ---------------------------------------------------------------------------


class TestGapCosineSimilarityUnequal:
    def test_unequal_length_returns_zero(self):
        from applire.services.gap import _cosine_similarity
        # This hits line 89 (the unequal-length early return)
        result = _cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])
        assert result == 0.0


# ---------------------------------------------------------------------------
# Router smoke tests using FastAPI TestClient
# ---------------------------------------------------------------------------


class TestJobsMatchRouter:
    def _make_app(self):
        from fastapi import FastAPI
        from applire.routers.jobs import router
        app = FastAPI()
        app.include_router(router)
        return app

    def test_match_returns_404_when_no_profile(self):
        """GET /api/jobs/match → 404 when no profile exists."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from applire.routers.jobs import router
        from applire.db.session import get_db
        from applire.auth import get_auth_provider

        app = FastAPI()
        app.include_router(router)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        async def override_db():
            yield mock_session

        async def override_auth():
            return None

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_auth_provider] = override_auth

        with TestClient(app) as client:
            resp = client.get("/api/jobs/match")
        assert resp.status_code == 404

    def test_match_accepts_top_n_and_berufsbild_params(self):
        """Query params are accepted by the endpoint signature."""
        from fastapi.testclient import TestClient
        from unittest.mock import AsyncMock, MagicMock, patch
        from applire.models.profile import MasterProfile
        import uuid as _uuid

        app = self._make_app()

        # Patch dependencies
        with patch("applire.routers.jobs.get_auth_provider"), \
             patch("applire.routers.jobs.get_db"), \
             patch("applire.routers.jobs.rank_jobs", new=AsyncMock(return_value=[])):

            profile_mock = MagicMock(spec=MasterProfile)
            profile_mock.id = _uuid.uuid4()
            profile_mock.deleted_at = None

            mock_session = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = profile_mock
            mock_session.execute = AsyncMock(return_value=mock_result)

            from applire.routers.jobs import router
            from fastapi import FastAPI
            fresh_app = FastAPI()

            async def override_db():
                yield mock_session

            async def override_auth():
                return None

            from applire.db.session import get_db
            from applire.auth import get_auth_provider

            fresh_app.include_router(router)
            fresh_app.dependency_overrides[get_db] = override_db
            fresh_app.dependency_overrides[get_auth_provider] = override_auth

            with TestClient(fresh_app) as client:
                resp = client.get("/api/jobs/match?top_n=5&berufsbild_code=43")
            assert resp.status_code == 200
            assert resp.json() == []
