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

"""Issue #118 — deterministic reverse-chronological work order in the tailored CV.

UAT (Chocolate pre-release) found two CONCURRENT open-ended ("present") positions
rendered oldest-first: the shared sort keyed on END date only, so both ongoing
roles tied at 9999-12 and the incidental input order survived all the way into
``tailored_data`` / ``content_snapshot``. The LLM's ordering is advisory —
``_enforce_work_order`` re-sorts the validated ``TailoredCVData`` at the single
site where ``tailored_data`` and ``content_snapshot`` are established.
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# Helper level: _enforce_work_order on TailoredCVData
# ---------------------------------------------------------------------------


def _tailored_two_concurrent_positions() -> dict:
    """Two overlapping open-ended positions, deliberately OLDEST-START FIRST."""
    return {
        "contact": {"name": "Max Mustermann", "email": "max@example.com"},
        "summary": "Lead Data Engineer bei Alpha Analytics AG mit paralleler Beratungstätigkeit.",
        "work_history": [
            {
                "company": "Beta Consulting GmbH",
                "role": "Senior Consultant",
                "start_date": "2024-12",
                "end_date": None,
                "bullets": ["Beratung von Mittelständlern zu Datenstrategie."],
            },
            {
                "company": "Alpha Analytics AG",
                "role": "Lead Data Engineer",
                "start_date": "2026-03",
                "end_date": None,
                "bullets": ["Aufbau der zentralen Datenplattform."],
            },
        ],
        "skills": ["Python"],
        "education": [
            {
                "institution": "TU Berlin",
                "degree": "M.Sc.",
                "field": "Informatik",
                "start_date": "2014-10",
                "end_date": "2017-09",
            }
        ],
        "languages": [{"language": "Deutsch", "level": "Muttersprache"}],
    }


def test_enforce_work_order_sorts_concurrent_open_ended_positions_newest_first():
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import _enforce_work_order

    tailored = TailoredCVData.model_validate(_tailored_two_concurrent_positions())
    ordered = _enforce_work_order(tailored)
    assert [w.company for w in ordered.work_history] == [
        "Alpha Analytics AG",
        "Beta Consulting GmbH",
    ]
    # Entry payloads travel with their position (bullets stay attached).
    assert ordered.work_history[0].bullets == ["Aufbau der zentralen Datenplattform."]


def test_enforce_work_order_missing_start_sorts_last_and_is_stable():
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import _enforce_work_order

    data = _tailored_two_concurrent_positions()
    data["work_history"].append(
        {
            "company": "Gamma KG",
            "role": "Praktikant",
            "start_date": "",
            "end_date": "2010-08",
            "bullets": [],
        }
    )
    ordered = _enforce_work_order(TailoredCVData.model_validate(data))
    assert [w.company for w in ordered.work_history] == [
        "Alpha Analytics AG",
        "Beta Consulting GmbH",
        "Gamma KG",
    ]


# ---------------------------------------------------------------------------
# Pipeline level: _render_cv_background persists the enforced order in BOTH
# tailored_data and content_snapshot (the single enforcement site).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered (mirrors test_ats_report_persistence)."""
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
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def db_with_cv(db):
    from applire.models.user import User
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.cv import GeneratedCV

    user_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    job_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    profile_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    cv_id = uuid.UUID("00000000-0000-0000-0000-000000000005")

    user = User(
        id=user_id,
        email="test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="abc123",
        raw_text="Data engineer job",
        role_title="Data Engineer",
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="de",
    )
    profile = MasterProfile(
        id=profile_id,
        profile_json={"work_experience": [], "contact": {}},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cv = GeneratedCV(
        id=cv_id,
        job_analysis_id=job_id,
        profile_id=profile_id,
        tailored_data=None,
        template="classic_german",
        status="pending",
        content_snapshot=None,
        section_overrides=None,
        ats_report=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([user, job, profile, cv])
    await db.commit()
    return {"db": db, "cv_id": cv_id, "job_id": job_id, "profile_id": profile_id}


@pytest.mark.asyncio
async def test_generation_persists_reverse_chronological_order(db_with_cv):
    """#118 regression (pipeline wiring): even when the LLM returns the two
    concurrent open-ended positions oldest-first, the persisted tailored_data
    AND the content_snapshot are newest-start-first."""
    from applire.models.cv import GeneratedCV

    ctx = db_with_cv
    session = ctx["db"]

    tailored_raw = _tailored_two_concurrent_positions()

    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = tailored_raw

    async def fake_review(**kwargs):
        return kwargs["draft"]

    with patch("applire.services.cv.AsyncSessionLocal") as mock_session_local, \
         patch("applire.services.cv.get_provider", return_value=mock_provider), \
         patch("applire.services.cv.review_and_refine", side_effect=fake_review), \
         patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", 0), \
         patch("applire.services.cv._review_cv_language", new=AsyncMock(side_effect=lambda t, *a, **k: t)), \
         patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF-fake")):
        mock_session_local.return_value.__aenter__.return_value = session
        from applire.services.cv import _render_cv_background
        await _render_cv_background(ctx["cv_id"], ctx["job_id"], ctx["profile_id"], "classic_german")

    record = await session.get(GeneratedCV, ctx["cv_id"])
    assert record.status == "ready", f"generation failed: {record.error_message!r}"
    companies = [w["company"] for w in record.tailored_data["work_history"]]
    assert companies == ["Alpha Analytics AG", "Beta Consulting GmbH"]
    snapshot_companies = [p["company"] for p in record.content_snapshot["positions"]]
    assert snapshot_companies == ["Alpha Analytics AG", "Beta Consulting GmbH"]
