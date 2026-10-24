# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""R2-13 (P-1, Hazelnut build 4) — a failed reconcile is "not saved", never "changed nothing".

Founder UAT 2026-10-23 (``gpt-6-luna`` on Requesty): the reconcile reply carried
text after its JSON object, the provider's strict parse failed, the engine
swallowed it into an empty ``ReconcileResult()``, and ``session.py``'s
"the answer changed nothing → follow up once" branch asked a follow-up. The
candidate moved on, and the answer (a new role) was gone.

The contract (``applire.exceptions.ReconcileFailedError``, commit 275499eb):
the engine raises it after its one retry. This module pins the INTERVIEW half
(WP-I) against a stub ENGINE that raises the contract error. The real bridge,
the real turn handler, and the real doors run; only ``reconcile()`` is doubled:

* the service turn raises, generates NO follow-up, and persists nothing (session
  row, transcript, counters, vault), even when the same db session commits
  afterwards (the agent door reuses its session);
* the HTTP interview door answers 503 ``answer_not_saved`` / ``turn_state:
  not_saved``;
* the Mode C door (``/api/profile/enrich/{id}/respond``) does the same;
* the Gap-Click micro-session (a recorded cluster) does the same;
* the CONTROL: an engine that returns an empty result still takes the
  follow-up branch. "Changed nothing" and "not saved" are different turns.

Run:
    LLM_PROVIDER=mistral DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend \\
      python3 -m pytest tests/unit/test_r2_13_interview_not_saved.py -q
"""

import copy
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile, retire_live_profiles  # noqa: E402

_ANSWER = "Since March I am also Team Lead Payments at Acme, next to my engineer role."
_QUESTION = "Tell me about your GCP experience."
_ENGINE = "applire.services.profile.reconcile.interview_bridge.reconcile"


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
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
        yield session, factory
    await engine.dispose()


def _failing_engine():
    from applire.exceptions import ReconcileFailedError

    return AsyncMock(side_effect=ReconcileFailedError("reconcile failed after retry"))


def _empty_engine():
    from applire.services.profile.reconcile.engine import ReconcileResult

    return AsyncMock(return_value=ReconcileResult())


def _provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="unused")
    provider.aparse_json = AsyncMock(return_value={"question": "unused", "choices": None})
    provider.__class__.__name__ = "MockProvider"
    return provider


def _make_job():
    from applire.models.job import JobAnalysis

    return JobAnalysis(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Platform Engineer. Requirements: GCP, FastAPI.",
        role_title="Platform Engineer",
        required_skills=["GCP", "FastAPI"],
        nice_to_have_skills=[], keywords=[], seniority_level="Senior",
        company_culture_signals=[], language_requirement="English", jd_language="en",
    )


_PROFILE_JSON = {
    "personal_info": {"name": "Mara Test", "email": "mara@example.de"},
    "skills": [{"name": "Python", "category": "technical"}],
    "work_experience": [{"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}],
}


async def _seed(db):
    from applire.models.session import InterviewSession

    await retire_live_profiles(db)
    job = _make_job()
    profile = make_master_profile(profile_json=copy.deepcopy(_PROFILE_JSON))
    db.add_all([job, profile])
    await db.flush()
    state = {
        "mode": "targeted", "job_id": str(job.id), "gap_analysis_id": None,
        "profile_id": str(profile.id),
        "critical_gaps": ["GCP certification", "FastAPI experience"],
        "gap_categories": {"GCP certification": "C", "FastAPI experience": "C"},
        "gap_clusters_by_id": {
            g: {"id": g, "label": g, "gaps": [g], "jd_skills": [], "jd_context": ""}
            for g in ("GCP certification", "FastAPI experience")
        },
        "addressed_gaps": [], "current_gap_index": 0,
        "current_question": _QUESTION,
        "current_choices": ["I hold the GCP Associate certificate."],
        "messages": [{"role": "assistant", "content": _QUESTION}],
        "questions_asked": 1, "hard_ceiling": 12,
        "questions_per_gap": {}, "skipped_gaps": [], "full_gaps": [],
    }
    record = InterviewSession(
        job_analysis_id=job.id, gap_analysis_id=None, profile_id=profile.id,
        mode="targeted", status="active", state=state, hard_ceiling=12,
        questions_asked=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(record)
    await db.commit()
    return record, profile


async def _fresh(factory, model, row_id):
    """Read the row back through a NEW session: what is persisted, not what is cached."""
    async with factory() as s:
        return (await s.execute(select(model).where(model.id == row_id))).scalar_one()


# ---------------------------------------------------------------------------
# 1. The service turn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_reconcile_raises_and_asks_no_follow_up(db):
    from applire.exceptions import ReconcileFailedError
    from applire.services.session import send_message

    session, _factory = db
    record, _profile = await _seed(session)
    writer = AsyncMock(return_value={"question": "A narrower follow-up?", "choices": None})
    cluster_writer = AsyncMock(return_value={"question": "Next topic?", "choices": None})

    with (
        patch(_ENGINE, new=_failing_engine()),
        patch("applire.services.session.question_generator_with_profile", new=writer),
        patch("applire.services.session._cluster_question", new=cluster_writer),
    ):
        with pytest.raises(ReconcileFailedError):
            await send_message(record.id, _ANSWER, session, _provider())

    writer.assert_not_awaited()
    cluster_writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_reconcile_persists_nothing_even_if_the_door_commits_later(db):
    """The agent door keeps one db session across calls: a commit AFTER the failed
    turn must not persist the optimistic transcript append (shared list)."""
    from applire.exceptions import ReconcileFailedError
    from applire.models.profile import MasterProfile
    from applire.models.session import InterviewSession
    from applire.services.session import send_message

    session, factory = db
    record, profile = await _seed(session)
    vault_before = copy.deepcopy((await _fresh(factory, MasterProfile, profile.id)).profile_json)

    with (
        patch(_ENGINE, new=_failing_engine()),
        patch("applire.services.session.question_generator_with_profile", new=AsyncMock()),
    ):
        with pytest.raises(ReconcileFailedError):
            await send_message(record.id, _ANSWER, session, _provider())
    await session.commit()  # the door's own later commit, worst case

    row = await _fresh(factory, InterviewSession, record.id)
    assert row.status == "active"
    assert row.questions_asked == 1
    assert row.state["questions_asked"] == 1
    assert row.state["messages"] == [{"role": "assistant", "content": _QUESTION}], (
        "the answer the vault never saw is not in the transcript"
    )
    assert row.state["current_question"] == _QUESTION, "the question on screen still stands"
    assert row.state["current_choices"] == ["I hold the GCP Associate certificate."]
    assert row.state["questions_per_gap"] == {}, "no follow-up budget was spent"
    assert row.state["addressed_gaps"] == []
    # The in-memory row the door holds is clean too (shared list undone in place).
    assert record.state["messages"] == [{"role": "assistant", "content": _QUESTION}]
    vault_after = (await _fresh(factory, MasterProfile, profile.id)).profile_json
    assert vault_after == vault_before, "no trail record, no write"


@pytest.mark.asyncio
async def test_resend_after_not_saved_is_an_ordinary_turn(db):
    """The same message sent again, once the engine works, is reconciled normally."""
    from applire.exceptions import ReconcileFailedError
    from applire.models.session import InterviewSession
    from applire.services.session import send_message

    session, factory = db
    record, _profile = await _seed(session)
    writer = AsyncMock(return_value={"question": "A concrete GCP example?", "choices": None})
    with patch("applire.services.session.question_generator_with_profile", new=writer):
        with patch(_ENGINE, new=_failing_engine()):
            with pytest.raises(ReconcileFailedError):
                await send_message(record.id, _ANSWER, session, _provider())
        with patch(_ENGINE, new=_empty_engine()):
            resp = await send_message(record.id, _ANSWER, session, _provider())

    assert resp.complete is False
    row = await _fresh(factory, InterviewSession, record.id)
    users = [m for m in row.state["messages"] if m["role"] == "user"]
    assert users == [{"role": "user", "content": _ANSWER}], "exactly one copy of the answer"


@pytest.mark.asyncio
async def test_control_empty_reconcile_still_takes_the_follow_up_branch(db):
    """The CONTROL that makes the two tests above meaningful: an engine that
    RETURNS an empty result is "changed nothing" and still gets its follow-up."""
    from applire.services.session import send_message

    session, _factory = db
    record, _profile = await _seed(session)
    writer = AsyncMock(return_value={"question": "A concrete GCP example?", "choices": None})
    with (
        patch(_ENGINE, new=_empty_engine()),
        patch("applire.services.session.question_generator_with_profile", new=writer),
    ):
        resp = await send_message(record.id, _ANSWER, session, _provider())

    writer.assert_awaited_once()
    assert resp.question == "A concrete GCP example?"
    assert resp.current_gap_id == "GCP certification"


# ---------------------------------------------------------------------------
# 2. The doors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_http_interview_door_answers_503_answer_not_saved(db):
    from fastapi import HTTPException

    from applire.routers.session import post_message
    from applire.schemas.session import SessionMessageRequest

    session, _factory = db
    record, _profile = await _seed(session)
    user = MagicMock()
    user.id = None  # the owner context of the test harness decides (ruling 3d-1)
    with (
        patch(_ENGINE, new=_failing_engine()),
        patch("applire.services.session.question_generator_with_profile", new=AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await post_message(
                record.id, SessionMessageRequest(message=_ANSWER), session, _provider(), user
            )

    assert exc_info.value.status_code == 503
    detail = exc_info.value.detail
    assert detail["error_code"] == "answer_not_saved"
    assert detail["turn_state"] == "not_saved"
    assert _ANSWER not in detail["message"], "the body never echoes testimony"


@pytest.mark.asyncio
async def test_mode_c_door_answers_503_and_keeps_the_session(db, monkeypatch):
    from fastapi import HTTPException

    import applire.routers.profile_enrich as pe
    from applire.models.profile import MasterProfile
    from applire.models.session import InterviewSession
    from applire.routers.profile_enrich import respond_to_enrich, start_enrich_session
    from applire.schemas.enrich import EnrichRespondRequest, EnrichStartRequest

    session, factory = db
    await retire_live_profiles(session)
    prof = make_master_profile(profile_json=copy.deepcopy(_PROFILE_JSON))
    session.add(prof)
    await session.commit()
    monkeypatch.setattr(
        pe, "gap_detector_mode_c",
        lambda profile_data, scope=None: ["team_size: Engineer @ Acme GmbH", "budget: Engineer @ Acme GmbH"],
    )
    next_q = AsyncMock(return_value={"question": "Tell me about team_size"})
    monkeypatch.setattr(pe, "question_generator_with_profile", next_q)
    first = await start_enrich_session(EnrichStartRequest(), session, _provider(), None)
    next_q.reset_mock()
    before = copy.deepcopy((await _fresh(factory, InterviewSession, first.session_id)).state)
    vault_before = copy.deepcopy((await _fresh(factory, MasterProfile, prof.id)).profile_json)

    with patch(_ENGINE, new=_failing_engine()):
        with pytest.raises(HTTPException) as exc_info:
            await respond_to_enrich(
                first.session_id, EnrichRespondRequest(answer=_ANSWER), session, _provider(), None
            )
    await session.commit()

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["error_code"] == "answer_not_saved"
    next_q.assert_not_awaited()
    after = (await _fresh(factory, InterviewSession, first.session_id)).state
    assert after == before, "transcript, gap index and counters unchanged"
    # The identity-map row the door mutated in place is clean too: a later flush
    # of a reassigned `state` must not carry the unsaved answer along.
    in_memory = await session.get(InterviewSession, first.session_id)
    assert in_memory.state["messages"] == before["messages"]
    assert (await _fresh(factory, MasterProfile, prof.id)).profile_json == vault_before


@pytest.mark.asyncio
async def test_gap_click_micro_session_turn_is_not_saved(db):
    """The recorded-cluster path (Gap-Click / resolve_gap): same contract."""
    from applire.exceptions import ReconcileFailedError
    from applire.models.gap import GapAnalysis
    from applire.models.session import InterviewSession
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session, send_message

    session, factory = db
    await retire_live_profiles(session)
    job = _make_job()
    profile = make_master_profile(profile_json=copy.deepcopy(_PROFILE_JSON))
    session.add_all([job, profile])
    await session.flush()

    def _row(concept):
        return {"concept": concept, "surface_forms": [concept], "sources": ["required"],
                "fit_weight": 1.0, "status": "gap", "evidence": "", "claimable": False}

    session.add(GapAnalysis(
        job_analysis_id=job.id, profile_id=profile.id, match_score=0.4,
        critical_gaps=[], minor_gaps=[], strengths=[], keyword_gaps=[],
        category_a=[], category_b=[], category_c=["GCP", "FastAPI"],
        keyword_ledger=[_row("GCP"), _row("FastAPI")],
        gap_clusters=[{
            "id": "cluster-cloud", "label": "Cloud", "category": "C", "gaps": ["GCP"],
            "jd_skills": ["GCP"], "jd_context": "",
            "outcome": {"asked": 0, "covered": [], "declined": [], "session_ids": []},
            "coverage": "open",
        }],
    ))
    await session.commit()
    writer = AsyncMock(return_value={"question": "Where did you use GCP?", "choices": None})
    with patch("applire.services.session.question_generator_with_profile", new=writer):
        created = await create_session(
            SessionCreateRequest(job_id=job.id, mode="targeted", target_gap="cluster-cloud"),
            session, _provider(),
        )
        writer.reset_mock()
        with patch(_ENGINE, new=_failing_engine()):
            with pytest.raises(ReconcileFailedError):
                await send_message(created.session_id, _ANSWER, session, _provider())
    await session.commit()

    writer.assert_not_awaited()
    row = await _fresh(factory, InterviewSession, created.session_id)
    assert row.status == "active", "the micro-session waits for the resend"
    assert not [m for m in row.state["messages"] if m["role"] == "user"]
    gap = (await _fresh(factory, GapAnalysis, (await _gap_id(factory, job.id))))
    assert gap.gap_clusters[0]["outcome"]["asked"] == 0, "no budget charged for an unsaved answer"


async def _gap_id(factory, job_id):
    from applire.models.gap import GapAnalysis

    async with factory() as s:
        return (await s.execute(
            select(GapAnalysis.id).where(GapAnalysis.job_analysis_id == job_id)
        )).scalar_one()
