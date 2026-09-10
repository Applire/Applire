# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Adversarial pass, 2026-09-09 — US313 / ADR-086's per-application aggregation
was silently incomplete on this branch.

O1's report (``Documents/Runs/Nougat/build-1/o1/report.md`` §6.6/§6.7) shipped
patches wiring ``llm_usage_context(..., application_id=application_id)`` into
both ``services/cv.py::_render_cv_background`` and
``services/cover_letter.py::_render_cover_letter_background``, to be applied by
the main session at integration (ruling O1-own-1). What actually landed on the
integrated branch was a DEGRADED version of the CV half (no ``application_id``,
no ``stage``) and NOTHING for the cover-letter half — every cover-letter
``llm_usage`` row carried no document or application attribution at all, and
every CV row's ``application_id`` was always ``None`` regardless of whether an
``Application`` existed. ``usage_report._grouped`` filters
``column.is_not(None)``, so the panel's per-application breakdown silently
excluded every dollar a candidate actually spent.

Two seams per document type, mirroring the codebase's "one named test per call
site" convention (memory ``feedback_seam_test_per_call_site``):

* the CALLER (``generate_cv`` / ``generate_cover_letter``) resolves the
  application and threads it into BOTH the inline and the
  ``BackgroundTasks.add_task`` call sites;
* the RENDERER (``_render_cv_background`` / ``_render_cover_letter_background``)
  forwards its ``application_id`` parameter into ``llm_usage_context`` — the one
  line that actually attributes every LLM call made inside the ``with`` block.
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# Same stub id services/color_detection.py and main.py's lifespan use for the
# single-operator Community instance (ADR-022).
_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

GERMAN_JD = (
    "Wir suchen eine Buchhalterin für unsere Kanzlei in München. Sie "
    "verantworten die Abschlüsse und führen ein kleines Team."
)


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with every model the two generate_* paths touch."""
    from applire.db.session import Base

    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    """User + job + profile + an Application row for that (user, job) pair."""
    from applire.models.application import Application
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile, authorized_profile_write
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
    with authorized_profile_write():
        profile = MasterProfile(
            profile_json={"personal_info": {"name": "Max"}, "skills": [], "metadata": {}}
        )
    application = Application(user_id=user.id, job_analysis_id=job.id)
    db.add_all([user, job, profile, application])
    await db.commit()
    return user, job, profile, application


# ── the caller resolves and threads application_id ─────────────────────────


class TestGenerateCVThreadsApplicationId:
    @pytest.mark.asyncio
    async def test_inline_path_passes_the_applications_id(self, db, seeded, monkeypatch):
        """Agent channel (``background_tasks=None``): a direct await."""
        import applire.services.cv as cv_module

        user, job, profile, application = seeded
        mock_render = AsyncMock()
        monkeypatch.setattr(cv_module, "_render_cv_background", mock_render)

        await cv_module.generate_cv(job.id, db, provider=AsyncMock())

        mock_render.assert_awaited_once()
        _args, kwargs = mock_render.await_args
        call_args = mock_render.await_args.args
        # Positional call: (record.id, job_id, profile.id, template, application_id)
        assert call_args[-1] == application.id, (
            f"expected application_id={application.id} threaded through, got "
            f"positional args {call_args}"
        )

    @pytest.mark.asyncio
    async def test_background_tasks_path_passes_the_applications_id(
        self, db, seeded, monkeypatch
    ):
        """REST path: FastAPI ``BackgroundTasks.add_task``."""
        import applire.services.cv as cv_module

        user, job, profile, application = seeded
        mock_render = AsyncMock()
        monkeypatch.setattr(cv_module, "_render_cv_background", mock_render)

        added = []
        bg = MagicMock()
        bg.add_task.side_effect = lambda fn, *a, **kw: added.append((fn, a, kw))

        await cv_module.generate_cv(job.id, db, provider=AsyncMock(), background_tasks=bg)

        assert len(added) == 1
        _fn, args, _kwargs = added[0]
        assert args[-1] == application.id

    @pytest.mark.asyncio
    async def test_no_application_row_threads_none_not_a_crash(self, db, monkeypatch):
        """No Application exists yet (e.g. a document generated before one is
        created) — must degrade to document-only attribution, never raise."""
        import applire.services.cv as cv_module
        from applire.models.job import JobAnalysis
        from applire.models.profile import MasterProfile, authorized_profile_write
        from applire.models.user import User

        user = User(id=_STUB_USER_ID, email="local@applire.community")
        job = JobAnalysis(
            id=uuid.uuid4(), raw_text_hash="x", raw_text=GERMAN_JD,
            role_title="R", company_name="C", required_skills=[],
            nice_to_have_skills=[], keywords=[], seniority_level="",
            company_culture_signals=[], language_requirement="DE",
        )
        with authorized_profile_write():
            profile = MasterProfile(profile_json={"personal_info": {}, "skills": [], "metadata": {}})
        db.add_all([user, job, profile])
        await db.commit()

        mock_render = AsyncMock()
        monkeypatch.setattr(cv_module, "_render_cv_background", mock_render)

        await cv_module.generate_cv(job.id, db, provider=AsyncMock())

        mock_render.assert_awaited_once()
        assert mock_render.await_args.args[-1] is None


class TestGenerateCoverLetterThreadsApplicationId:
    @pytest.mark.asyncio
    async def test_inline_path_passes_the_applications_id(self, db, seeded, monkeypatch):
        import applire.services.cover_letter as cl_module
        from applire.models.flow import FlowSession
        from applire.schemas.cover_letter import CoverLetterGenerateRequest

        user, job, profile, application = seeded
        db.add(FlowSession(user_id=user.id, job_id=job.id, current_step="cv_generation"))
        await db.commit()

        mock_render = AsyncMock()
        monkeypatch.setattr(cl_module, "_render_cover_letter_background", mock_render)

        await cl_module.generate_cover_letter(
            CoverLetterGenerateRequest(job_id=job.id), db, provider=AsyncMock()
        )

        mock_render.assert_awaited_once()
        kwargs = mock_render.await_args.kwargs
        assert kwargs.get("application_id") == application.id, (
            f"expected application_id={application.id} threaded through, got "
            f"kwargs {kwargs}"
        )

    @pytest.mark.asyncio
    async def test_background_tasks_path_passes_the_applications_id(
        self, db, seeded, monkeypatch
    ):
        import applire.services.cover_letter as cl_module
        from applire.models.flow import FlowSession
        from applire.schemas.cover_letter import CoverLetterGenerateRequest

        user, job, profile, application = seeded
        db.add(FlowSession(user_id=user.id, job_id=job.id, current_step="cv_generation"))
        await db.commit()

        mock_render = AsyncMock()
        monkeypatch.setattr(cl_module, "_render_cover_letter_background", mock_render)

        added = []
        bg = MagicMock()
        bg.add_task.side_effect = lambda fn, *a, **kw: added.append((fn, a, kw))

        await cl_module.generate_cover_letter(
            CoverLetterGenerateRequest(job_id=job.id), db, provider=AsyncMock(),
            background_tasks=bg,
        )

        assert len(added) == 1
        _fn, _args, kwargs = added[0]
        assert kwargs.get("application_id") == application.id


# ── the renderer forwards application_id into llm_usage_context ────────────


class TestRendererForwardsApplicationIdIntoTheUsageContext:
    """The ``with llm_usage_context(...)`` line is the outermost thing in each
    renderer, entered before any DB query — so a record that resolves to
    "not found" lets the function return cleanly right after, and the spy has
    already captured the call. No LLM, no Playwright, no deep mocking needed.
    """

    @pytest.mark.asyncio
    async def test_render_cv_background_forwards_it(self):
        import applire.services.cv as cv_module

        cv_id, job_id, profile_id, app_id = (
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        )

        mock_db = AsyncMock()
        mock_db.get.return_value = None  # GeneratedCV not found -> clean early return

        calls = []

        class _Spy:
            def __init__(self, **kwargs):
                calls.append(kwargs)

            def __enter__(self):
                return None

            def __exit__(self, *exc):
                return False

        with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
             patch("applire.providers.llm.usage.llm_usage_context", _Spy):
            mock_session_local.return_value.__aenter__.return_value = mock_db
            await cv_module._render_cv_background(
                cv_id, job_id, profile_id, "classic_german", app_id
            )

        assert calls == [
            {
                "stage": "cv",
                "document_kind": "cv",
                "document_id": cv_id,
                "application_id": app_id,
            }
        ]

    @pytest.mark.asyncio
    async def test_render_cover_letter_background_forwards_it(self):
        import applire.services.cover_letter as cl_module

        cl_id, cv_id, job_id, app_id = (
            uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        )

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # cover letter not found
        mock_db.execute.return_value = mock_result

        calls = []

        class _Spy:
            def __init__(self, **kwargs):
                calls.append(kwargs)

            def __enter__(self):
                return None

            def __exit__(self, *exc):
                return False

        with patch("applire.services.cover_letter.AsyncSessionLocal") as mock_session_local, \
             patch("applire.providers.llm.usage.llm_usage_context", _Spy):
            mock_session_local.return_value.__aenter__.return_value = mock_db
            await cl_module._render_cover_letter_background(
                cl_id=cl_id, cv_id=cv_id, job_id=job_id, application_id=app_id
            )

        assert calls == [
            {
                "stage": "cover_letter",
                "document_kind": "cover_letter",
                "document_id": cl_id,
                "application_id": app_id,
            }
        ]
