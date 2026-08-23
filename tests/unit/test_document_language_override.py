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

"""E054 / ADR-038 amendment 2026-08-23 — user override over document language.

The override lives on ``applications.language_override`` (per-user entity),
NEVER on the hash-deduplicated ``job_analyses`` row. A new seam
``resolve_document_language(application, job)`` sits ABOVE the unchanged
detection primitive ``resolve_jd_language``. Generated documents pin their
own ``document_language`` at generation (amendment clause 3b) so read paths
never re-resolve against a mutable override.

No Docker, no LLM.
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered (pattern of test_application_source_url.py)."""
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


@pytest_asyncio.fixture
async def user_and_job(db):
    """Insert a stub user and job analysis; return (user, job)."""
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="abc123",
        raw_text=GERMAN_JD,
        role_title="Buchhalterin",
        company_name="Example AG",
        required_skills=["Buchhaltung"],
        nice_to_have_skills=[],
        keywords=["Buchhaltung"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )
    db.add_all([user, job])
    await db.commit()
    return user, job


GERMAN_JD = (
    "Wir suchen eine Buchhalterin für unsere Kanzlei in München. Sie "
    "verantworten die Abschlüsse und führen ein kleines Team."
)


class TestResolveDocumentLanguage:
    """Amendment clause 2 — override first, detection as fallback."""

    def _seam(self):
        from applire.utils.language_detection import resolve_document_language

        return resolve_document_language

    def test_override_beats_stored_jd_language(self):
        seam = self._seam()
        application = SimpleNamespace(language_override="en")
        job = SimpleNamespace(jd_language="de", raw_text=GERMAN_JD)
        assert seam(application, job) == "en"

    def test_null_override_falls_back_to_stored_jd_language(self):
        seam = self._seam()
        application = SimpleNamespace(language_override=None)
        job = SimpleNamespace(jd_language="de", raw_text=GERMAN_JD)
        assert seam(application, job) == "de"

    def test_null_override_and_null_column_fall_back_to_detection(self):
        seam = self._seam()
        application = SimpleNamespace(language_override=None)
        job = SimpleNamespace(jd_language=None, raw_text=GERMAN_JD)
        assert seam(application, job) == "de"

    def test_missing_application_falls_back_to_resolve_jd_language(self):
        # Job-scoped contexts without an Application row (defensive: the seam
        # must never require the join to have succeeded).
        seam = self._seam()
        job = SimpleNamespace(jd_language="en", raw_text="")
        assert seam(None, job) == "en"


class TestModelColumns:
    """Amendment clauses 1 + 3b — storage locations."""

    def test_application_has_language_override_column(self):
        from applire.models.application import Application

        col = Application.__table__.columns["language_override"]
        assert col.nullable is True

    def test_generated_cv_pins_document_language(self):
        from applire.models.cv import GeneratedCV

        col = GeneratedCV.__table__.columns["document_language"]
        assert col.nullable is True

    def test_generated_cover_letter_pins_document_language(self):
        from applire.models.cover_letter import GeneratedCoverLetter

        col = GeneratedCoverLetter.__table__.columns["document_language"]
        assert col.nullable is True

    def test_job_analyses_does_NOT_gain_the_override(self):
        # Clause 1: the hash-deduplicated, user-less row must never carry a
        # user preference — a regression here is a cross-user write in a
        # shared DB.
        from applire.models.job import JobAnalysis

        assert "language_override" not in JobAnalysis.__table__.columns


class TestPatchLanguageOverride:
    """Amendment clause 5 — PATCH /api/applications/{id}, CLEARABLE class."""

    async def _create(self, db, user, job):
        from applire.schemas.application import CreateApplicationRequest
        from applire.services.application import create_application

        return await create_application(
            user.id, CreateApplicationRequest(job_analysis_id=job.id), db
        )

    @pytest.mark.asyncio
    async def test_patch_sets_language_override(self, db, user_and_job):
        from applire.schemas.application import PatchApplicationRequest
        from applire.services.application import patch_application

        user, job = user_and_job
        created = await self._create(db, user, job)
        resp = await patch_application(
            created.id,
            user.id,
            PatchApplicationRequest(language_override="en"),
            db,
        )
        assert resp.language_override == "en"

    @pytest.mark.asyncio
    async def test_patch_explicit_null_clears_the_override(self, db, user_and_job):
        # Clause 5: language_override belongs to the CLEARABLE class — an
        # explicit null in the body returns the application to automatic
        # detection. (The never-clearable class would silently ignore it.)
        from applire.schemas.application import PatchApplicationRequest
        from applire.services.application import patch_application

        user, job = user_and_job
        created = await self._create(db, user, job)
        await patch_application(
            created.id, user.id, PatchApplicationRequest(language_override="en"), db
        )
        resp = await patch_application(
            created.id,
            user.id,
            PatchApplicationRequest.model_validate({"language_override": None}),
            db,
        )
        assert resp.language_override is None

    @pytest.mark.asyncio
    async def test_patch_rejects_unsupported_language(self, db, user_and_job):
        # Clause 8: DE/EN only.
        from applire.schemas.application import PatchApplicationRequest

        with pytest.raises(Exception):
            PatchApplicationRequest(language_override="fr")

    @pytest.mark.asyncio
    async def test_omitting_the_field_leaves_the_override_untouched(
        self, db, user_and_job
    ):
        from applire.schemas.application import PatchApplicationRequest
        from applire.services.application import patch_application

        user, job = user_and_job
        created = await self._create(db, user, job)
        await patch_application(
            created.id, user.id, PatchApplicationRequest(language_override="en"), db
        )
        resp = await patch_application(
            created.id, user.id, PatchApplicationRequest(notes="unrelated"), db
        )
        assert resp.language_override == "en"


def _minimal_tailored() -> dict:
    return {
        "contact": {
            "name": "Max",
            "email": None,
            "phone": None,
            "location": None,
            "linkedin": None,
        },
        "summary": "Buchhalterin mit Teamverantwortung.",
        "work_history": [
            {
                "company": "Example AG",
                "role": "Buchhalterin",
                "start_date": "2020-01",
                "end_date": None,
                "bullets": ["Verantwortete die Abschlüsse."],
            }
        ],
        "skills": ["Buchhaltung"],
        "education": [],
        "languages": [],
    }


class TestGenerationPinning:
    """Amendment clause 3 — generate_cv resolves ONCE and pins on the record."""

    async def _generate(self, db, job, monkeypatch):
        from unittest.mock import AsyncMock

        import applire.services.cv as cv_module
        from applire.models.profile import MasterProfile, authorized_profile_write

        with authorized_profile_write():
            profile = MasterProfile(profile_json={"skills": [], "metadata": {}})
        db.add(profile)
        await db.commit()

        monkeypatch.setattr(cv_module, "_render_cv_background", AsyncMock())
        resp = await cv_module.generate_cv(
            job.id, db, provider=AsyncMock(), background_tasks=None
        )
        from applire.models.cv import GeneratedCV

        return await db.get(GeneratedCV, resp.cv_id)

    @pytest.mark.asyncio
    async def test_generate_cv_pins_the_override_language(
        self, db, user_and_job, monkeypatch
    ):
        from applire.models.application import Application

        user, job = user_and_job
        db.add(
            Application(
                user_id=user.id, job_analysis_id=job.id, language_override="en"
            )
        )
        await db.commit()

        record = await self._generate(db, job, monkeypatch)
        assert record.document_language == "en"

    @pytest.mark.asyncio
    async def test_generate_cv_pins_detection_without_an_override(
        self, db, user_and_job, monkeypatch
    ):
        user, job = user_and_job
        record = await self._generate(db, job, monkeypatch)
        assert record.document_language == "de"


class TestReadPathUsesPinnedLanguage:
    """Amendment clause 3b — read paths use the STORED value, never re-resolve.

    A record pinned 'en' over a German job must render English chrome even
    though a fresh resolve would say 'de' — and vice versa: flipping the
    application's override AFTER generation must not repaint the document.
    """

    async def _make_ready_record(self, db, job, document_language):
        from applire.models.cv import GeneratedCV

        record = GeneratedCV(
            job_analysis_id=job.id,
            profile_id=uuid.uuid4(),
            tailored_data=_minimal_tailored(),
            template="classic_german",
            status="ready",
            document_language=document_language,
        )
        db.add(record)
        await db.commit()
        return record

    @pytest.mark.asyncio
    async def test_get_cv_html_renders_the_pinned_language(self, db, user_and_job):
        from applire.services.cv import get_cv_html

        user, job = user_and_job
        record = await self._make_ready_record(db, job, "en")
        html = await get_cv_html(record.id, db)
        assert "Experience" in html
        assert "Berufserfahrung" not in html

    @pytest.mark.asyncio
    async def test_get_cv_html_null_pin_falls_back_to_the_seam(
        self, db, user_and_job
    ):
        # Pre-migration row: NULL document_language resolves via the seam —
        # the jd_language migration-0032 pattern.
        from applire.services.cv import get_cv_html

        user, job = user_and_job
        record = await self._make_ready_record(db, job, None)
        html = await get_cv_html(record.id, db)
        assert "Berufserfahrung" in html


class TestBackgroundRenderThreadsPinnedLanguage:
    """Amendment clause 3a — the run uses the record's pinned language, ONCE.

    The record is pinned 'en' while the job says 'de': every language input of
    the run (writer output_language, the ADR-038 language pass) must be 'en'.
    A fresh per-pass resolve would say 'de' — that divergence is the TOCTOU
    failure this test pins.
    """

    @pytest.mark.asyncio
    async def test_writer_and_language_pass_get_the_pinned_language(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        mock_cv = MagicMock()
        mock_cv.status = "pending"
        mock_cv.target_pages = 2
        mock_cv.document_language = "en"  # pinned at generate_cv time
        mock_cv.job_analysis_id = job_id
        mock_cv.profile_id = profile_id

        mock_job = MagicMock()
        mock_job.role_title = "Buchhalterin"
        mock_job.required_skills = []
        mock_job.nice_to_have_skills = []
        mock_job.keywords = []
        mock_job.seniority_level = ""
        mock_job.company_culture_signals = []
        mock_job.language_requirement = "DE"
        mock_job.jd_language = "de"  # a fresh resolve would say 'de'
        mock_job.raw_text = GERMAN_JD

        mock_profile = MagicMock()
        mock_profile.profile_json = {
            "work_experience": [], "projects": [], "skills": [],
            "education": [], "languages": [],
            "personal_info": {"name": "Max", "email": None}, "metadata": {},
        }

        mock_gap = MagicMock()
        mock_gap.keyword_gaps = []
        mock_gap.critical_gaps = []
        mock_gap.keyword_ledger = []

        mock_db = AsyncMock()
        mock_db.get.side_effect = lambda model, id_: {
            cv_id: mock_cv, job_id: mock_job, profile_id: mock_profile,
        }[id_]
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_gap
        mock_db.execute.return_value = mock_result

        fallback_kwargs: dict = {}
        language_pass_langs: list = []

        async def fake_fallback(*args, **kwargs):
            fallback_kwargs.update(kwargs)
            return {
                "contact": {"name": "Max", "email": None, "phone": None,
                            "location": None, "linkedin": None},
                "summary": "Accountant.",
                "work_history": [], "skills": [], "education": [], "languages": [],
            }

        async def fake_language_pass(draft, language, *args, **kwargs):
            language_pass_langs.append(language)
            return draft

        with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
             patch("applire.services.cv.get_provider", return_value=AsyncMock()), \
             patch("applire.services.cv._tailor_cv_with_fallback", side_effect=fake_fallback), \
             patch("applire.services.cv.review_and_refine",
                   new=AsyncMock(side_effect=lambda **kw: kw["draft"])), \
             patch("applire.services.cv._review_cv_language",
                   new=AsyncMock(side_effect=fake_language_pass)), \
             patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
             patch("applire.services.cv_section_editor.build_content_snapshot",
                   return_value={}):
            mock_session_local.return_value.__aenter__.return_value = mock_db
            from applire.services.cv import _render_cv_background

            await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

        assert fallback_kwargs.get("output_language") == "en"
        assert language_pass_langs and all(l == "en" for l in language_pass_langs)


class TestCoverLetterPinning:
    """Amendment clause 3 — letter twin of the CV pinning tests."""

    async def _seed_profile(self, db):
        from applire.models.profile import MasterProfile, authorized_profile_write

        with authorized_profile_write():
            profile = MasterProfile(
                profile_json={
                    "personal_info": {"name": "Max"},
                    "skills": [],
                    "metadata": {},
                }
            )
        db.add(profile)
        await db.commit()
        return profile

    @pytest.mark.asyncio
    async def test_generate_cover_letter_pins_the_override_language(
        self, db, user_and_job, monkeypatch
    ):
        from unittest.mock import AsyncMock

        import applire.services.cover_letter as cl_module
        from applire.models.application import Application
        from applire.models.cover_letter import GeneratedCoverLetter
        from applire.models.flow import FlowSession
        from applire.schemas.cover_letter import CoverLetterGenerateRequest

        user, job = user_and_job
        await self._seed_profile(db)
        db.add(
            Application(
                user_id=user.id, job_analysis_id=job.id, language_override="en"
            )
        )
        db.add(FlowSession(user_id=user.id, job_id=job.id, current_step="cv_generation"))
        await db.commit()

        monkeypatch.setattr(
            cl_module, "_render_cover_letter_background", AsyncMock()
        )
        resp = await cl_module.generate_cover_letter(
            CoverLetterGenerateRequest(job_id=job.id),
            db,
            provider=AsyncMock(),
            background_tasks=None,
        )
        record = await db.get(GeneratedCoverLetter, resp.cover_letter_id)
        assert record.document_language == "en"

    @pytest.mark.asyncio
    async def test_pdf_filename_follows_the_pinned_language(self, db, user_and_job):
        # Pinned 'en' over a German job: the filename suffix must be
        # "Cover-Letter", not "Anschreiben" — the record wins over detection.
        from applire.models.cover_letter import GeneratedCoverLetter
        from applire.services.cover_letter import get_cover_letter_pdf_filename

        user, job = user_and_job
        profile = await self._seed_profile(db)
        cl = GeneratedCoverLetter(
            job_analysis_id=job.id,
            profile_id=profile.id,
            letter_data={},
            pre_gen_inputs={},
            status="ready",
            document_language="en",
        )
        db.add(cl)
        await db.commit()

        filename = await get_cover_letter_pdf_filename(cl.id, db)
        assert "Cover-Letter" in filename
        assert "Anschreiben" not in filename


class TestAgentSurface:
    """Amendment clause 5 — MCP parity + analyze_jd names the language."""

    def test_job_analysis_response_exposes_jd_language(self, user_and_job):
        # analyze_jd's result (REST and MCP both serialize this schema) gains
        # the detected document language — today only language_requirement is
        # exposed, which is the wrong routing signal (2026-06-10 amendment).
        from applire.schemas.job import JobAnalysisResponse

        assert "jd_language" in JobAnalysisResponse.model_fields

    @pytest.mark.asyncio
    async def test_mcp_update_application_sets_the_override(self, db, user_and_job):
        from applire.schemas.application import CreateApplicationRequest
        from applire.services.application import create_application

        user, job = user_and_job
        created = await create_application(
            user.id, CreateApplicationRequest(job_analysis_id=job.id), db
        )
        result = await _call_mcp_update_application(
            db, str(created.id), language_override="en"
        )
        assert result.get("language_override") == "en"

    @pytest.mark.asyncio
    async def test_mcp_auto_sentinel_clears_the_override(self, db, user_and_job):
        # The MCP tool builds its request skipping None, so explicit null is
        # unreachable on this channel — 'auto' restores parity (clause 5).
        from applire.schemas.application import CreateApplicationRequest
        from applire.services.application import create_application

        user, job = user_and_job
        created = await create_application(
            user.id, CreateApplicationRequest(job_analysis_id=job.id), db
        )
        await _call_mcp_update_application(db, str(created.id), language_override="en")
        result = await _call_mcp_update_application(
            db, str(created.id), language_override="auto"
        )
        assert result.get("language_override") is None


async def _call_mcp_update_application(db, application_id: str, **kwargs):
    """Drive the MCP update_application tool against the real test session."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from applire.mcp.server import update_application

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("applire.mcp.server.get_db", return_value=cm):
        return await update_application(application_id=application_id, **kwargs)
