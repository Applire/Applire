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
``tailored_data`` / ``content_snapshot``.

E049 / ADR-067: ``_enforce_work_order`` is DELETED, not relocated — the writer no
longer emits work entries at all (it returns id-keyed prose), so there is nothing
left for a post-hoc re-sort to correct. Document order is now structural: the
per-entry sort this module used to re-derive lives in
``applire.services.profile.merge._sort_work_by_date`` (already fully covered,
INCLUDING the concurrent-open-ended-positions case, by
``backend/tests/unit/test_sort_work_by_date.py``), and ``assemble_tailored_cv``
makes that sorted vault order the document order by construction (pinned by
``test_cv_assembly.py::test_document_order_is_the_vault_order_not_the_prose_order``).
The two unit-level ``_enforce_work_order`` tests that used to live here are
DELETED rather than rewritten — they would only duplicate that existing coverage.

What still has value, and is kept below: the PIPELINE-level regression, driving
the real ``_render_cv_background`` entrypoint end to end, that the persisted
``tailored_data`` AND ``content_snapshot`` carry the vault's sorted order even
when the writer's prose names the two concurrent positions in the opposite order.
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def _profile_two_concurrent_positions() -> dict:
    """Two overlapping open-ended positions, deliberately OLDEST-START FIRST in the
    vault's raw (pre-sort) order — mirrors the #118 UAT shape."""
    return {
        "personal_info": {"name": "Max Mustermann", "email": "max@example.com"},
        "work_experience": [
            {
                "id": "beta",
                "company": "Beta Consulting GmbH",
                "role": "Senior Consultant",
                "start_date": "2024-12",
                "end_date": None,
            },
            {
                "id": "alpha",
                "company": "Alpha Analytics AG",
                "role": "Lead Data Engineer",
                "start_date": "2026-03",
                "end_date": None,
            },
        ],
        # Hazelnut build 3 (786 polish (c)): the Ausbildung in the audit's vault
        # order — 2010, 2004, 2013 — plus an undated row, so the pipeline seam
        # test below can assert the delivered order is reverse-chronological.
        "education": [
            {"id": "e-meister", "institution": "IHK", "degree": "Industriemeister Metall (IHK)", "end_date": "2010"},
            {"id": "e-undated", "institution": "VHS", "degree": "Kurs ohne Datum"},
            {"id": "e-ausbildung", "institution": "Rasselstein", "degree": "Ausbildung Industriemechaniker", "end_date": "2004"},
            {"id": "e-refa", "institution": "REFA", "degree": "REFA-Grundschein", "start_date": "2013-03"},
        ],
        "languages": [],
    }


def _prose_two_concurrent_positions() -> dict:
    """The writer's PROSE draft, deliberately naming the positions in the SAME
    (oldest-start-first) order as the raw vault input — order must come from the
    vault's sort, never from the order the writer happens to emit ids in."""
    return {
        "summary": "Lead Data Engineer bei Alpha Analytics AG mit paralleler Beratungstätigkeit.",
        "work": [
            {"id": "beta", "bullets": ["Beratung von Mittelständlern zu Datenstrategie."]},
            {"id": "alpha", "bullets": ["Aufbau der zentralen Datenplattform."]},
        ],
        "skills": ["Python"],
    }


# ---------------------------------------------------------------------------
# Pipeline level: _render_cv_background persists the vault's sorted order in BOTH
# tailored_data and content_snapshot (the single assembly site, ADR-066).
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
    profile = make_master_profile(
        id=profile_id,
        profile_json=_profile_two_concurrent_positions(),
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
    """#118 regression (pipeline wiring), E049/ADR-067 shape: even when the
    writer's PROSE names the two concurrent open-ended positions oldest-first,
    the persisted tailored_data AND the content_snapshot are newest-start-first —
    because ``assemble_tailored_cv`` joins prose onto the ALREADY-SORTED vault
    work list (``_sort_work_by_date``), not onto the writer's own id order."""
    from applire.models.cv import GeneratedCV

    ctx = db_with_cv
    session = ctx["db"]

    prose_draft = _prose_two_concurrent_positions()

    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = prose_draft

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


@pytest.mark.asyncio
async def test_generation_persists_reverse_chronological_education(db_with_cv):
    """Hazelnut build 3 (786 polish (c), ADR-094 cl. 2 amended 2026-10-22): the
    delivered Ausbildung is newest first (end date, else start date), undated rows
    last — driven through the real ``_render_cv_background`` so the seam (the
    generation copy's sort beside the #118 work sort) is what is pinned, not only
    the helper. Vault order 2010, —, 2004, 2013 → 2013, 2010, 2004, —."""
    from applire.models.cv import GeneratedCV

    ctx = db_with_cv
    session = ctx["db"]
    mock_provider = AsyncMock()
    mock_provider.aparse_json.return_value = _prose_two_concurrent_positions()

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
    degrees = [e["degree"] for e in record.tailored_data["education"]]
    assert degrees == [
        "REFA-Grundschein",
        "Industriemeister Metall (IHK)",
        "Ausbildung Industriemechaniker",
        "Kurs ohne Datum",
    ]


def test_sort_education_by_date_is_stable_and_keeps_every_row():
    """The helper: end date wins over start date, month precision counts, equal
    keys keep vault order, undated and non-dict rows go last, nothing is dropped."""
    from applire.services.cv import sort_education_by_date

    rows = [
        {"degree": "A", "end_date": "2010"},
        {"degree": "B", "start_date": "2019", "end_date": "2012-09"},
        {"degree": "C"},
        {"degree": "D", "end_date": "2012-09"},
        {"degree": "E", "start_date": "2024-10", "end_date": None},
    ]
    assert [r["degree"] for r in sort_education_by_date(rows)] == ["E", "B", "D", "A", "C"]
    assert sort_education_by_date([]) == []
