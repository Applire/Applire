# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""E12-788:L21 (founder 2026-10-22) — the SERVED workflow status.

`applications.workflow_status` is stored from the flow step by
`sync_workflow_status`; the agent's à-la-carte path (`analyze_gaps`,
`generate_cv`, `generate_cover_letter` without `advance_flow`) never moves the
flow, so a finished CV and letter sat under "analyzing" for a whole run (Kaile
probe 2026-09-20). The read model now serves the higher of the stored value and
what the artifacts prove, plus two additive facts: `cover_letter_ready` and
`workflow_running`. The stored column is never written by the read. No Docker,
no LLM.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.posting_links import link_posting
from applire.models.application import Application, WorkflowStatus
from applire.schemas.application import CreateApplicationRequest, PatchApplicationRequest
from applire.services.application import (
    create_application,
    get_application,
    list_applications,
    patch_application,
    _served_workflow_status,
)

_USER = uuid.UUID("00000000-0000-0000-0000-000000000001")
_NOW = datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.gap_job  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
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


def _job():
    from applire.models.job import JobAnalysis

    return JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"hash-{uuid.uuid4()}",
        raw_text="Sample JD",
        role_title="Leiter Operations",
        company_name="Rheinwerk GmbH",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
    )


async def _app(db, *, start_workflow: bool = False):
    job = _job()
    db.add(job)
    await link_posting(db, job, _USER)
    await db.commit()
    return await create_application(
        _USER, CreateApplicationRequest(job_analysis_id=job.id, start_workflow=start_workflow), db
    )


def _cv(job_id, status="ready", user_id=_USER):
    from applire.models.cv import GeneratedCV

    return GeneratedCV(
        id=uuid.uuid4(),
        job_analysis_id=job_id,
        profile_id=uuid.uuid4(),
        user_id=user_id,
        tailored_data={},
        template="classic_german",
        status=status,
        created_at=_NOW,
        expires_at=_NOW + timedelta(days=90),
    )


def _letter(job_id, status="ready"):
    from applire.models.cover_letter import GeneratedCoverLetter

    return GeneratedCoverLetter(
        id=uuid.uuid4(),
        job_analysis_id=job_id,
        profile_id=uuid.uuid4(),
        user_id=_USER,
        status=status,
        expires_at=_NOW + timedelta(days=90),
    )


def _gap(job_id):
    from applire.models.gap import GapAnalysis

    return GapAnalysis(
        id=uuid.uuid4(),
        job_analysis_id=job_id,
        profile_id=uuid.uuid4(),
        user_id=_USER,
        match_score=60,
    )


def _gap_job(job_id, status="processing"):
    from applire.models.gap_job import GapAnalysisJob

    return GapAnalysisJob(id=uuid.uuid4(), job_analysis_id=job_id, user_id=_USER, status=status)


# ---------------------------------------------------------------------------
# The ladder (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored, kwargs, expected",
    [
        ("analyzing", {"cv_statuses": {"ready"}}, "completed"),
        ("analyzing", {"cv_statuses": {"pending"}}, "cv_generating"),
        ("analyzing", {"cv_statuses": {"generating", "ready"}}, "completed"),
        ("none", {"has_gap_analysis": True}, "analyzing"),
        ("analyzing", {"has_interview": True}, "interviewing"),
        # A failed / expired CV proves nothing.
        ("analyzing", {"cv_statuses": {"failed", "expired"}}, "analyzing"),
        # Never lowers the stored value.
        ("completed", {}, "completed"),
        ("interviewing", {"has_gap_analysis": True}, "interviewing"),
        ("none", {}, "none"),
    ],
)
def test_served_status_ladder(stored, kwargs, expected):
    assert _served_workflow_status(stored, **kwargs) == expected


# ---------------------------------------------------------------------------
# The à-la-carte run the probe saw, through get / list / patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finished_cv_and_letter_lift_a_flow_that_never_advanced(db):
    """The probe's shape: a flow started (stored "analyzing", step jd_analysis)
    and never advanced, while a gap analysis, a ready CV and a ready letter
    exist. The served status is "completed"; the stored column is untouched;
    the flow's resume pointer is not rewritten."""
    app = await _app(db, start_workflow=True)
    assert app.workflow_status == WorkflowStatus.analyzing
    db.add_all([_gap(app.job_analysis_id), _cv(app.job_analysis_id), _letter(app.job_analysis_id)])
    await db.commit()

    got = await get_application(app.id, _USER, db)
    assert got.workflow_status == WorkflowStatus.completed
    assert got.cover_letter_ready is True
    assert got.workflow_running is False
    assert got.flow_current_step == "jd_analysis"

    stored = await db.get(Application, app.id)
    await db.refresh(stored)
    assert stored.workflow_status == "analyzing"


@pytest.mark.asyncio
async def test_list_serves_the_same_status_and_filters_on_it(db):
    lifted = await _app(db, start_workflow=True)
    plain = await _app(db, start_workflow=True)
    db.add(_cv(lifted.job_analysis_id))
    await db.commit()

    listed = await list_applications(_USER, db)
    by_id = {i.id: i for i in listed.items}
    assert by_id[lifted.id].workflow_status == WorkflowStatus.completed
    assert by_id[plain.id].workflow_status == WorkflowStatus.analyzing

    completed = await list_applications(_USER, db, workflow_status=WorkflowStatus.completed)
    assert [i.id for i in completed.items] == [lifted.id]
    assert completed.total == 1


@pytest.mark.asyncio
async def test_patch_response_serves_the_derived_status(db):
    """The dossier replaces its state with the PATCH response — it must not
    flicker back to the stored value."""
    app = await _app(db, start_workflow=True)
    db.add(_cv(app.job_analysis_id))
    await db.commit()
    patched = await patch_application(app.id, _USER, PatchApplicationRequest(notes="x"), db)
    assert patched.workflow_status == WorkflowStatus.completed


@pytest.mark.asyncio
async def test_running_generation_is_told_apart_from_a_finished_step(db):
    """`workflow_running` is true only while Applire produces something — a CV
    or letter in pending/generating, a gap job in pending/processing. A
    finished gap analysis waiting for the user is NOT running (the dossier's
    "Analyzing" spinner on a finished analysis, audit felix-020)."""
    waiting = await _app(db, start_workflow=True)
    db.add(_gap(waiting.job_analysis_id))
    await db.commit()
    got = await get_application(waiting.id, _USER, db)
    assert got.workflow_status == WorkflowStatus.analyzing
    assert got.workflow_running is False

    for maker in (
        lambda j: _cv(j, status="pending"),
        lambda j: _cv(j, status="generating"),
        lambda j: _letter(j, status="generating"),
        lambda j: _gap_job(j, status="pending"),
        lambda j: _gap_job(j, status="processing"),
    ):
        app = await _app(db, start_workflow=True)
        db.add(maker(app.job_analysis_id))
        await db.commit()
        assert (await get_application(app.id, _USER, db)).workflow_running is True

    done = await _app(db, start_workflow=True)
    db.add_all([_gap_job(done.job_analysis_id, status="ready"), _cv(done.job_analysis_id, status="failed")])
    await db.commit()
    assert (await get_application(done.id, _USER, db)).workflow_running is False


@pytest.mark.asyncio
async def test_soft_deleted_artifacts_prove_nothing(db):
    app = await _app(db, start_workflow=True)
    cv = _cv(app.job_analysis_id)
    letter = _letter(app.job_analysis_id)
    cv.deleted_at = _NOW
    letter.deleted_at = _NOW
    db.add_all([cv, letter])
    await db.commit()
    got = await get_application(app.id, _USER, db)
    assert got.workflow_status == WorkflowStatus.analyzing
    assert got.cover_letter_ready is False


@pytest.mark.asyncio
async def test_another_users_documents_for_the_same_posting_prove_nothing(db, monkeypatch):
    """ADR-092 cl. 3: the posting is shared. Another owner's ready CV and
    letter for the SAME posting must not lift this owner's status. The ORM
    loader criteria are defence in depth, never credited — switched off here
    so the enrichment's own owner key has to hold."""
    from applire import ownership
    from applire.models.user import User

    monkeypatch.setattr(ownership, "GUARD_ENABLED", False)
    other = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
    db.add(User(id=other, email="other@test.de"))
    app = await _app(db, start_workflow=True)
    letter = _letter(app.job_analysis_id)
    letter.user_id = other
    db.add_all([_cv(app.job_analysis_id, user_id=other), letter])
    await db.commit()

    got = await get_application(app.id, _USER, db)
    assert got.workflow_status == WorkflowStatus.analyzing
    assert got.cover_letter_ready is False


@pytest.mark.asyncio
async def test_letter_ready_does_not_claim_a_cv(db):
    """"completed" reads "CV ready" in the UI — a letter alone does not lift it,
    it only sets `cover_letter_ready`."""
    app = await _app(db, start_workflow=True)
    db.add(_letter(app.job_analysis_id))
    await db.commit()
    got = await get_application(app.id, _USER, db)
    assert got.workflow_status == WorkflowStatus.analyzing
    assert got.cover_letter_ready is True


# ---------------------------------------------------------------------------
# The agent door — the same derivation through `mcp.call_tool` (ADR-058)
# ---------------------------------------------------------------------------


def _db_cm(session):
    from unittest.mock import AsyncMock, MagicMock

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _call(name, args, db):
    import json
    from unittest.mock import patch

    from applire.mcp.server import mcp

    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)):
        out = await mcp.call_tool(name, args)
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_agent_door_get_and_list_application_serve_the_derived_status(db):
    """The probe's door: `get_application` / `list_applications` after an
    à-la-carte run (no `advance_flow`) report "completed" with the letter
    ready — the fields a human glancing at the agent's result reads."""
    app = await _app(db, start_workflow=True)
    db.add_all([_gap(app.job_analysis_id), _cv(app.job_analysis_id), _letter(app.job_analysis_id)])
    await db.commit()

    got = await _call("get_application", {"application_id": str(app.id)}, db)
    assert got["workflow_status"] == "completed"
    assert got["cover_letter_ready"] is True
    assert got["workflow_running"] is False

    listed = await _call("list_applications", {}, db)
    rows = listed if isinstance(listed, list) else listed.get("items", [listed])
    row = next(r for r in rows if r["id"] == str(app.id))
    assert row["workflow_status"] == "completed"


@pytest.mark.asyncio
async def test_agent_door_pins_both_additive_fields_in_both_states(db):
    """Ruling F2-1 / E12-788:L21 (main 2026-10-22): `cover_letter_ready` and
    `workflow_running` are pinned through the MCP `get_application` seam in
    BOTH states — the contract snapshot records input schemas only (ADR-095
    cl. 6), so this seam test is what holds the result shape."""
    idle = await _app(db, start_workflow=True)
    got = await _call("get_application", {"application_id": str(idle.id)}, db)
    assert got["cover_letter_ready"] is False
    assert got["workflow_running"] is False
    assert got["workflow_status"] == "analyzing"

    busy = await _app(db, start_workflow=True)
    db.add_all([_cv(busy.job_analysis_id, status="generating"), _letter(busy.job_analysis_id)])
    await db.commit()
    got = await _call("get_application", {"application_id": str(busy.id)}, db)
    assert got["workflow_running"] is True
    assert got["cover_letter_ready"] is True
    assert got["workflow_status"] == "cv_generating"
    # The flow's resume pointer is NOT lifted (ruling: served status only).
    assert got["flow_current_step"] == "jd_analysis"
