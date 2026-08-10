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

"""E039 / US221 — Stale-CV indicator (journey Branch H).

An application's CV is stale when its newest READY generated CV predates the
newest Master-Profile enrichment record: the profile grew after the CV was
tailored, so the compounding profile has value the document doesn't show yet.

The hint is a read-model enrichment on ApplicationResponse (list + get + patch)
with an explained delta ("your profile gained X") aggregated from the
enrichment trail. Nudge, never a gate: dismissal persists per application
(stale_cv_dismissed_at) and re-arms when a NEWER enrichment lands. Re-tailoring
goes through the existing generation pipeline — a new ready CV simply postdates
the enrichment head and the hint disappears; a pinned submitted version (US219)
is never touched. No Docker, no LLM.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.application import (
    CreateApplicationRequest,
    PatchApplicationRequest,
)
from applire.services.application import (
    create_application,
    get_application,
    list_applications,
    patch_application,
)

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

_NOW = datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
    from applire.db.session import Base  # noqa: F401
    import applire.models.user        # noqa: F401
    import applire.models.job         # noqa: F401
    import applire.models.profile     # noqa: F401
    import applire.models.gap         # noqa: F401
    import applire.models.cv          # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session     # noqa: F401
    import applire.models.flow        # noqa: F401
    import applire.models.uploads     # noqa: F401
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


def _make_job(**overrides):
    from applire.models.job import JobAnalysis

    defaults = dict(
        id=uuid.uuid4(),
        raw_text_hash=f"hash-{uuid.uuid4()}",
        raw_text="Sample JD",
        role_title="Data Analyst",
        company_name="DataCraft GmbH",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
    )
    defaults.update(overrides)
    return JobAnalysis(**defaults)


def _make_cv(job_id: uuid.UUID, *, created_at: datetime, **overrides):
    from applire.models.cv import GeneratedCV

    defaults = dict(
        id=uuid.uuid4(),
        job_analysis_id=job_id,
        profile_id=uuid.uuid4(),
        tailored_data={},
        template="classic_german",
        status="ready",
        created_at=created_at,
        expires_at=created_at + timedelta(days=90),
    )
    defaults.update(overrides)
    return GeneratedCV(**defaults)


def _enrichment_record(timestamp: str, changes: list[dict]) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "timestamp": timestamp,
        "source": "interview",
        "changes": changes,
    }


def _change(section: str, action: str = "added") -> dict:
    return {"section": section, "field": "x", "action": action}


def _make_profile(enrichment_records: list[dict]):
    from tests.support.profile_factory import make_master_profile

    return make_master_profile(
        id=uuid.uuid4(),
        profile_json={
            "personal_info": {"full_name": "Emma Beispiel"},
            "metadata": {"enrichment_history": enrichment_records},
        },
    )


def _iso(dt: datetime) -> str:
    return dt.isoformat()


async def _make_application(db, job=None):
    job = job or _make_job()
    db.add(job)
    await db.commit()
    return await create_application(
        _STUB_USER_ID, CreateApplicationRequest(job_analysis_id=job.id), db
    )


# ---------------------------------------------------------------------------
# Staleness signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_generated_cv_no_hint(db):
    """An application without a ready CV can't be stale — nothing to re-tailor."""
    db.add(_make_profile([_enrichment_record(_iso(_NOW), [_change("skills")])]))
    app = await _make_application(db)
    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is None


@pytest.mark.asyncio
async def test_cv_newer_than_enrichment_no_hint(db):
    """CV generated AFTER the last enrichment — profile hasn't grown since."""
    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=5)), [_change("skills")]),
    ]))
    app = await _make_application(db)
    db.add(_make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=1)))
    await db.commit()

    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is None


@pytest.mark.asyncio
async def test_enrichment_after_cv_sets_hint_with_gained_delta(db):
    """Profile grew after the CV: hint carries the newest ready CV's identity,
    the enrichment head timestamp, and the per-section gained summary sourced
    from records NEWER than the CV only."""
    cv_at = _NOW - timedelta(days=10)
    db.add(_make_profile([
        # Before the CV — must NOT count toward the gained delta.
        _enrichment_record(_iso(_NOW - timedelta(days=20)), [_change("education")]),
        _enrichment_record(
            _iso(_NOW - timedelta(days=3)),
            [_change("skills"), _change("skills"), _change("work_experience")],
        ),
        _enrichment_record(_iso(_NOW - timedelta(days=1)), [_change("skills")]),
    ]))
    app = await _make_application(db)
    cv = _make_cv(app.job_analysis_id, created_at=cv_at, template="modern")
    db.add(cv)
    await db.commit()

    result = await get_application(app.id, _STUB_USER_ID, db)
    hint = result.stale_cv
    assert hint is not None
    assert hint.latest_cv_id == cv.id
    assert hint.latest_cv_template == "modern"
    assert hint.profile_enriched_at == _NOW - timedelta(days=1)
    # Aggregated per section, ordered by count desc then section asc.
    assert [(g.section, g.count) for g in hint.gained] == [
        ("skills", 3),
        ("work_experience", 1),
    ]


@pytest.mark.asyncio
async def test_only_ready_cvs_count(db):
    """A newer pending/failed generation is not a version — the newest READY
    CV decides staleness; a job with no ready CV at all has no hint."""
    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=2)), [_change("skills")]),
    ]))
    app = await _make_application(db)
    db.add(_make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=5)))
    db.add(_make_cv(app.job_analysis_id, created_at=_NOW, status="pending"))
    db.add(_make_cv(app.job_analysis_id, created_at=_NOW, status="failed"))
    await db.commit()

    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is not None  # ready CV (5d old) predates enrichment

    # A second application whose job only has a pending CV → no hint.
    app2 = await _make_application(db)
    db.add(_make_cv(app2.job_analysis_id, created_at=_NOW - timedelta(days=5), status="pending"))
    await db.commit()
    result2 = await get_application(app2.id, _STUB_USER_ID, db)
    assert result2.stale_cv is None


@pytest.mark.asyncio
async def test_retailor_clears_hint_and_keeps_pin(db):
    """Re-tailoring (a new ready CV via the existing pipeline) clears the hint;
    the pinned submitted version is never replaced (FMEA JF-E-P5.1)."""
    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=3)), [_change("skills")]),
    ]))
    app = await _make_application(db)
    v1 = _make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=10))
    db.add(v1)
    await db.commit()
    await patch_application(
        app.id, _STUB_USER_ID, PatchApplicationRequest(submitted_cv_id=v1.id), db
    )
    assert (await get_application(app.id, _STUB_USER_ID, db)).stale_cv is not None

    # One-click re-tailor lands a NEW version through the same pipeline.
    db.add(_make_cv(app.job_analysis_id, created_at=_NOW))
    await db.commit()

    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is None
    assert result.submitted_cv_id == v1.id  # pin untouched


@pytest.mark.asyncio
async def test_stale_cv_carries_pinned_cvs_target_pages(db):
    """E042/US236 (ADR-051 amendment §4): the read model exposes the newest
    ready CV's persisted target_pages so the frontend re-tailor call can
    forward it (Task 1.4, not this task)."""
    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=1)), [_change("skills")]),
    ]))
    app = await _make_application(db)
    cv = _make_cv(
        app.job_analysis_id, created_at=_NOW - timedelta(days=10), target_pages=3
    )
    db.add(cv)
    await db.commit()

    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is not None
    assert result.stale_cv.target_pages == 3


@pytest.mark.asyncio
async def test_stale_cv_target_pages_is_none_for_legacy_cv(db):
    """A legacy/pre-E042 CV row has NULL target_pages — the read model must
    surface None rather than erroring."""
    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=1)), [_change("skills")]),
    ]))
    app = await _make_application(db)
    cv = _make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=10))
    db.add(cv)
    await db.commit()

    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is not None
    assert result.stale_cv.target_pages is None


@pytest.mark.asyncio
async def test_terminal_status_no_hint(db):
    """Rejected/hired applications get no re-tailor nudge — nothing to send."""
    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=1)), [_change("skills")]),
    ]))
    for status in ("rejected", "hired"):
        app = await _make_application(db)
        db.add(_make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=5)))
        await db.commit()
        await patch_application(
            app.id, _STUB_USER_ID, PatchApplicationRequest(user_status=status), db
        )
        result = await get_application(app.id, _STUB_USER_ID, db)
        assert result.stale_cv is None, f"user_status={status} must not nudge"


@pytest.mark.asyncio
async def test_soft_deleted_cv_ignored(db):
    """A deleted newest CV doesn't count — staleness follows the live artifact."""
    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=3)), [_change("skills")]),
    ]))
    app = await _make_application(db)
    old = _make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=10))
    fresh_deleted = _make_cv(app.job_analysis_id, created_at=_NOW, deleted_at=_NOW)
    db.add_all([old, fresh_deleted])
    await db.commit()

    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is not None
    assert result.stale_cv.latest_cv_id == old.id


@pytest.mark.asyncio
async def test_no_profile_no_hint(db):
    app = await _make_application(db)
    db.add(_make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=5)))
    await db.commit()
    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is None


@pytest.mark.asyncio
async def test_list_carries_hint(db):
    """The dashboard list is enriched too — one profile read for the page."""
    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=1)), [_change("skills")]),
    ]))
    stale_app = await _make_application(db)
    db.add(_make_cv(stale_app.job_analysis_id, created_at=_NOW - timedelta(days=5)))
    fresh_app = await _make_application(db)
    db.add(_make_cv(fresh_app.job_analysis_id, created_at=_NOW))
    await db.commit()

    result = await list_applications(_STUB_USER_ID, db)
    by_id = {item.id: item for item in result.items}
    assert by_id[stale_app.id].stale_cv is not None
    assert by_id[fresh_app.id].stale_cv is None


@pytest.mark.asyncio
async def test_naive_enrichment_timestamp_handled(db):
    """Trail timestamps without a tz suffix are treated as UTC, not a crash."""
    naive = (_NOW - timedelta(days=1)).replace(tzinfo=None)
    db.add(_make_profile([
        _enrichment_record(naive.isoformat(), [_change("skills")]),
    ]))
    app = await _make_application(db)
    db.add(_make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=5)))
    await db.commit()

    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is not None


# ---------------------------------------------------------------------------
# Dismissal — persisted, re-arms on newer enrichment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dismiss_persists(db):
    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=1)), [_change("skills")]),
    ]))
    app = await _make_application(db)
    db.add(_make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=5)))
    await db.commit()

    patched = await patch_application(
        app.id, _STUB_USER_ID, PatchApplicationRequest(dismiss_stale_cv=True), db
    )
    assert patched.stale_cv is None

    # Persisted: a later read is still quiet.
    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is None


@pytest.mark.asyncio
async def test_new_enrichment_rearms_after_dismiss(db):
    """A dismissal covers the enrichments known at dismiss time — when the
    profile grows AGAIN, the indicator comes back (Branch H: the compounding
    profile stays visible)."""
    from applire.models.profile import MasterProfile
    from sqlalchemy import select

    from tests.support.profile_factory import set_profile_json

    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=1)), [_change("skills")]),
    ]))
    app = await _make_application(db)
    db.add(_make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=5)))
    await db.commit()

    await patch_application(
        app.id, _STUB_USER_ID, PatchApplicationRequest(dismiss_stale_cv=True), db
    )
    assert (await get_application(app.id, _STUB_USER_ID, db)).stale_cv is None

    # The profile grows again — strictly after the dismissal.
    profile = (await db.execute(select(MasterProfile))).scalar_one()
    history = list(profile.profile_json["metadata"]["enrichment_history"])
    history.append(
        _enrichment_record(
            _iso(datetime.now(timezone.utc) + timedelta(seconds=1)),
            [_change("work_experience")],
        )
    )
    set_profile_json(profile, {
        **profile.profile_json,
        "metadata": {"enrichment_history": history},
    })
    await db.commit()

    result = await get_application(app.id, _STUB_USER_ID, db)
    assert result.stale_cv is not None
    assert [(g.section, g.count) for g in result.stale_cv.gained] == [
        ("skills", 1),
        ("work_experience", 1),
    ]


@pytest.mark.asyncio
async def test_dismiss_false_is_a_noop(db):
    db.add(_make_profile([
        _enrichment_record(_iso(_NOW - timedelta(days=1)), [_change("skills")]),
    ]))
    app = await _make_application(db)
    db.add(_make_cv(app.job_analysis_id, created_at=_NOW - timedelta(days=5)))
    await db.commit()

    patched = await patch_application(
        app.id,
        _STUB_USER_ID,
        PatchApplicationRequest(dismiss_stale_cv=False, notes="keep"),
        db,
    )
    assert patched.stale_cv is not None
