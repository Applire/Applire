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

"""E042 Task 1.3 (US238): the bounded measure-and-condense loop inside _update_ats_report.

The Playwright render seam (get_cv_html / _html_to_pdf) and the page-count seam
(extract_text_and_pages) are mocked — never launch a browser in a unit test. The real
condense_to_budget, build_content_snapshot and _audit_cv_text run so the loop's data
transitions are exercised end to end.
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


def _tailored(n_bullets: int) -> dict:
    return {
        "contact": {"name": "Anna Bauer", "email": "anna@example.com"},
        "summary": "Backend engineer.",
        "work_history": [
            {
                "id": "r1",
                "company": "Acme GmbH",
                "role": "Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "bullets": [f"Bullet {i}" for i in range(n_bullets)],
            }
        ],
        "skills": ["Python"],
    }


def _budget(ceiling: int):
    from applire.services.cv_budget import BudgetResult, BulletTier, RoleBudget

    tiers = {
        "top": BulletTier("top", 5, 4),
        "mid": BulletTier("mid", 3, 2),
        "bottom": BulletTier("bottom", 1, 0),
    }
    return BudgetResult(
        roles={"r1": RoleBudget(work_entry_id="r1", tier="mid", max_bullets=ceiling)},
        tiers=tiers, target_pages=2, region="DACH", claimable_forms=(),
    )


@pytest_asyncio.fixture
async def db():
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


async def _seed_cv(db, *, n_bullets, target_pages=2, section_overrides=None):
    from applire.models.job import JobAnalysis
    from applire.models.cv import GeneratedCV
    from applire.services.cv_section_editor import build_content_snapshot
    from applire.schemas.cv import TailoredCVData

    job_id = uuid.uuid4()
    job = JobAnalysis(
        id=job_id, raw_text_hash=str(job_id), raw_text="job",
        role_title="Engineer", required_skills=[], nice_to_have_skills=[],
        keywords=["Python"], seniority_level="mid", company_culture_signals=[],
        language_requirement="de",
    )
    tailored = _tailored(n_bullets)
    cv = GeneratedCV(
        id=uuid.uuid4(), job_analysis_id=job_id, profile_id=uuid.uuid4(),
        tailored_data=tailored, template="classic_german", status="ready",
        content_snapshot=build_content_snapshot(TailoredCVData.model_validate(tailored)),
        section_overrides=section_overrides, ats_report=None, target_pages=target_pages,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([job, cv])
    await db.commit()
    return cv


def _page_check(report_dict):
    return next((c for c in report_dict["checks"] if c["id"] == "page-length"), None)


def _patches(page_counts):
    """Patch the render + page-count seam; extract returns the given counts in order."""
    extract = MagicMock(side_effect=[("text", c) for c in page_counts])
    return extract, [
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")),
        patch("applire.services.ats_audit.extract_text_and_pages", new=extract),
    ]


# --- overrun triggers condense + re-render ---------------------------------

@pytest.mark.asyncio
async def test_overrun_condenses_then_meets_target(db):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import _update_ats_report, CondenseContext

    extract, ps = _patches([3, 2])  # 3 pages → condense → 2 pages
    with ps[0], ps[1], ps[2]:
        await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    assert len(cv.tailored_data["work_history"][0]["bullets"]) == 2, "role trimmed to ceiling"
    assert extract.call_count == 2, "one render before + one after the single condense"
    pc = _page_check(cv.ats_report)
    assert pc["status"] == "pass" and pc["details"] is None


@pytest.mark.asyncio
async def test_snapshot_rebuilt_after_condense(db):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import _update_ats_report, CondenseContext

    _, ps = _patches([3, 2])
    with ps[0], ps[1], ps[2]:
        await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    # The section editor serves content_snapshot — it must reflect the condensed bullets,
    # not the pre-condense five (amendment §2, the silent un-condense trap).
    positions = cv.content_snapshot["positions"]
    assert len(positions[0]["bullets"]) == 2


# --- under target: no condensation -----------------------------------------

@pytest.mark.asyncio
async def test_under_target_skips_condensation(db):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import _update_ats_report, CondenseContext

    extract, ps = _patches([2])  # already at target on first render
    with ps[0], ps[1], ps[2]:
        await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    assert len(cv.tailored_data["work_history"][0]["bullets"]) == 5, "no condense"
    assert extract.call_count == 1


# --- max 2 iterations, then exhausted --------------------------------------

@pytest.mark.asyncio
async def test_max_two_iterations_then_exhausted(db):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import _update_ats_report, CondenseContext

    # Always over target: render(orig)=4, render(after iter1)=4, render(after iter2)=4.
    extract, ps = _patches([4, 4, 4])
    with ps[0], ps[1], ps[2]:
        await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    assert extract.call_count == 3, "orig + after-iter1 + after-iter2 (2 condense passes max)"
    # iter1 ceiling 2 → 2 bullets; iter2 ceiling 1 → 1 bullet.
    assert len(cv.tailored_data["work_history"][0]["bullets"]) == 1
    pc = _page_check(cv.ats_report)
    assert pc["status"] == "fail" and "condensed to the maximum" in pc["details"]


@pytest.mark.asyncio
async def test_no_change_stops_early_and_reports_exhausted(db):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import _update_ats_report, CondenseContext

    # Over max but the ceiling already fits all 5 bullets → nothing to cut → exhausted.
    extract, ps = _patches([4])
    with ps[0], ps[1], ps[2]:
        await _update_ats_report(cv, db, CondenseContext(_budget(5), 2))

    assert extract.call_count == 1
    assert len(cv.tailored_data["work_history"][0]["bullets"]) == 5, "unchanged"
    pc = _page_check(cv.ats_report)
    assert pc["status"] == "fail" and "condensed to the maximum" in pc["details"]


# --- bail rule: section_overrides present ----------------------------------

@pytest.mark.asyncio
async def test_existing_overrides_bail_no_condense(db):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2, section_overrides={"introduction": "Hi"})
    from applire.services.cv import _update_ats_report, CondenseContext

    extract, ps = _patches([4])  # over target, but overrides → single audit only
    with ps[0], ps[1], ps[2]:
        await _update_ats_report(cv, db, CondenseContext(_budget(2), 2))

    assert extract.call_count == 1, "audit once, no condense loop"
    assert len(cv.tailored_data["work_history"][0]["bullets"]) == 5, "tailored_data untouched"


# --- section-editor re-audit path never condenses --------------------------

@pytest.mark.asyncio
async def test_section_editor_path_never_condenses(db):
    cv = await _seed_cv(db, n_bullets=5, target_pages=2)
    from applire.services.cv import _update_ats_report

    extract, ps = _patches([4])  # over target, but ctx=None → audit-only
    with ps[0], ps[1], ps[2]:
        await _update_ats_report(cv, db, None)

    assert extract.call_count == 1
    assert len(cv.tailored_data["work_history"][0]["bullets"]) == 5, "no condensation on re-audit"
    pc = _page_check(cv.ats_report)
    # target resolved from record.target_pages (2); 4 > max(3) → plain fail (not exhausted).
    assert pc["status"] == "fail" and "condensed" not in pc["details"]


@pytest.mark.asyncio
async def test_null_target_row_resolves_target_at_audit(db):
    # Legacy row: target_pages NULL, ctx=None → resolve to the region standard (2).
    cv = await _seed_cv(db, n_bullets=3, target_pages=None)
    from applire.services.cv import _update_ats_report

    extract, ps = _patches([2])
    with ps[0], ps[1], ps[2]:
        # _resolve_audit_target queries UserSettings; no settings row → resolve to the
        # region standard (2).
        await _update_ats_report(cv, db, None)

    pc = _page_check(cv.ats_report)
    assert pc["status"] == "pass" and pc["details"] is None  # 2 <= standard 2, no advisory
