# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-089 clause 7 — the agent door stays stateless and gains the follow-up.

``resolve_gap(job_id, gap_id, answer)`` keeps its signature. Its result adds
``coverage``, ``open_concepts``, ``budget_remaining`` and — when the gap asks
one more question — ``follow_up_question``; ``status`` gains
``partly_covered``. Calling it again on the same gap IS the follow-up turn: the
waiting question is resumed (ruling B-3), so the testimony is reconciled against
the question the human actually saw, and ``question_asked`` reports it. A call
on a gap that cannot be asked, or a retry identical to the last recorded
answer, is ``invalid_input`` and charges nothing.

Drives the REAL ``resolve_gap`` → ``create_session`` → ``send_message``
composition against in-memory SQLite; only the LLM-bound seams are doubled.

Run:
    LLM_PROVIDER=mistral DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend \\
      python3 -m pytest tests/unit/test_gap_followups_resolve_gap.py -q
"""

import copy
import uuid
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from mcp.shared.exceptions import McpError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_INFRA = "cluster-infra"
_INVALID_INPUT = -32602


@pytest_asyncio.fixture
async def db():
    import importlib
    import pkgutil

    import applire.models  # noqa: F401

    for _m in pkgutil.iter_modules(applire.models.__path__):
        importlib.import_module(f"applire.models.{_m.name}")
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _row(concept, status="gap", claimable=False):
    return {"concept": concept, "surface_forms": [concept], "sources": ["required"],
            "fit_weight": 1.0, "status": status, "evidence": "", "claimable": claimable}


async def _seed(db, *, outcome=None, coverage="open", members=("Kubernetes", "Terraform")):
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis

    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex, raw_text="Platform Engineer: Kubernetes, Terraform.",
        role_title="Platform Engineer", required_skills=["Kubernetes", "Terraform"],
        nice_to_have_skills=[], keywords=[], seniority_level="Senior",
        company_culture_signals=[], language_requirement="English", jd_language="en",
    )
    profile = make_master_profile(profile_json={
        "personal_info": {"name": "Mara Test"},
        "skills": [{"name": "Python", "category": "technical"}],
        "work_experience": [{"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}],
    })
    db.add_all([job, profile])
    await db.flush()
    db.add(GapAnalysis(
        job_analysis_id=job.id, profile_id=profile.id, match_score=0.4,
        critical_gaps=[], minor_gaps=[], strengths=[], keyword_gaps=[],
        category_a=[], category_b=[], category_c=["Kubernetes", "Terraform"],
        keyword_ledger=[_row("Kubernetes"), _row("Terraform")],
        gap_clusters=[{
            "id": _INFRA, "label": "Cloud infrastructure", "category": "C",
            "gaps": list(members), "jd_skills": ["Kubernetes", "Terraform"], "jd_context": "",
            "outcome": outcome or {"asked": 0, "covered": [], "declined": [], "session_ids": []},
            "coverage": coverage,
        }],
    ))
    await db.commit()
    return job.id


def _bridge(*, add_skills=(), addressed=None):
    from applire.models.profile import authorized_profile_write
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    async def _run(db, *, profile_record, **_kwargs):
        pj = copy.deepcopy(profile_record.profile_json)
        changes = []
        for name in add_skills:
            pj.setdefault("skills", []).append({"name": name, "category": "technical"})
            changes.append(FieldChange(section="skills", field=name, action="added", new_value=name))
        with authorized_profile_write():
            profile_record.profile_json = pj
        await db.flush()
        return InterviewTurnResult(
            profile_dict=pj, changes=changes,
            addressed=bool(changes) if addressed is None else addressed,
        )

    return AsyncMock(side_effect=_run)


def _writer(*questions):
    seq = list(questions)
    calls: list[dict] = []

    async def _gen(state, profile, provider, **kwargs):
        calls.append(kwargs)
        q = seq.pop(0) if len(seq) > 1 else seq[0]
        return {"question": q, "choices": None}

    mock = AsyncMock(side_effect=_gen)
    mock.calls = calls
    return mock


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _call(db, job_id, answer, *, bridge, writer, gap_id=_INFRA):
    from applire.mcp.server import resolve_gap

    with ExitStack() as stack:
        stack.enter_context(patch("applire.mcp.server.get_db", return_value=_db_cm(db)))
        stack.enter_context(patch("applire.mcp.server.get_provider", return_value=MagicMock()))
        stack.enter_context(patch("applire.services.session.reconcile_interview_turn", new=bridge))
        stack.enter_context(patch("applire.services.session.question_generator_with_profile", new=writer))
        stack.enter_context(patch("applire.services.session.analyze_gaps", new=AsyncMock()))
        return await resolve_gap(job_id=str(job_id), gap_id=gap_id, answer=answer)


async def _cluster(db, job_id):
    from applire.models.gap import GapAnalysis

    row = (await db.execute(
        select(GapAnalysis).where(GapAnalysis.job_analysis_id == job_id)
    )).scalar_one()
    await db.refresh(row)
    return next(c for c in row.gap_clusters if c["id"] == _INFRA)


async def _session_count(db):
    from applire.models.session import InterviewSession

    return (await db.execute(select(func.count()).select_from(InterviewSession))).scalar_one()


@pytest.mark.asyncio
async def test_a_partial_answer_returns_partly_covered_and_the_follow_up(db):
    job_id = await _seed(db)
    writer = _writer("Kubernetes and Terraform — where?", "And Terraform?")
    out = await _call(db, job_id, "Kubernetes in production at Acme.",
                      bridge=_bridge(add_skills=["Kubernetes"]), writer=writer)

    assert out["status"] == "partly_covered"
    assert out["coverage"] == "partly_covered"
    assert out["open_concepts"] == ["Terraform"]
    assert out["budget_remaining"] == 1
    assert out["follow_up_question"] == "And Terraform?"
    assert out["question_asked"] == "Kubernetes and Terraform — where?"
    assert isinstance(out["profile_completeness"], float), "a follow-up turn still reports the score"
    # ADR-084 — the new JD-derived keys are marked for the agent.
    marked = set(out["untrusted_content"]["fields"])
    assert {"open_concepts", "follow_up_question"} <= marked


@pytest.mark.asyncio
async def test_calling_again_answers_the_waiting_follow_up_in_the_same_session(db):
    """Ruling B-3 — the second call RESUMES the waiting session: the testimony
    is reconciled against the follow-up the agent was given, which is also what
    `question_asked` reports, and no second question is drafted for the slot."""
    job_id = await _seed(db)
    writer = _writer("Kubernetes and Terraform — where?", "And Terraform?")
    first = await _call(db, job_id, "Kubernetes in production at Acme.",
                        bridge=_bridge(add_skills=["Kubernetes"]), writer=writer)
    sessions_after_first = await _session_count(db)

    second_writer = _writer("NEVER GENERATED")
    bridge = _bridge(add_skills=["Terraform"])
    second = await _call(db, job_id, "Terraform too — I wrote our modules.",
                         bridge=bridge, writer=second_writer)

    assert second["question_asked"] == first["follow_up_question"] == "And Terraform?"
    assert bridge.await_args.kwargs["question"] == "And Terraform?", (
        "the answer is reconciled against the question the human actually saw"
    )
    assert second_writer.calls == []
    assert await _session_count(db) == sessions_after_first, "no new session was opened"
    assert second["status"] == "addressed"
    assert second["coverage"] == "covered"
    assert second["open_concepts"] == []
    assert second["budget_remaining"] == 0
    assert "follow_up_question" not in second
    cluster = await _cluster(db, job_id)
    assert cluster["outcome"]["asked"] == 2


@pytest.mark.asyncio
async def test_an_identical_retry_is_refused_and_charges_nothing(db):
    job_id = await _seed(db)
    writer = _writer("Kubernetes and Terraform — where?", "And Terraform?")
    answer = "Kubernetes in production at Acme."
    await _call(db, job_id, answer, bridge=_bridge(add_skills=["Kubernetes"]), writer=writer)
    sessions = await _session_count(db)

    bridge = _bridge(add_skills=["Kubernetes"])
    with pytest.raises(McpError) as exc:
        # Normalisation: case and trailing whitespace/punctuation do not make it new.
        await _call(db, job_id, "  kubernetes in production at Acme  ", bridge=bridge, writer=writer)
    assert exc.value.error.code == _INVALID_INPUT
    assert "identical" in exc.value.error.message
    assert bridge.await_count == 0, "the retry never reached the reconciler"
    assert await _session_count(db) == sessions
    assert (await _cluster(db, job_id))["outcome"]["asked"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome,coverage,members,needle",
    [
        ({"asked": 2, "covered": [], "declined": [], "session_ids": []}, "open",
         ["Kubernetes", "Terraform"], "budget"),
        ({"asked": 1, "covered": ["Kubernetes", "Terraform"], "declined": [], "session_ids": []},
         "covered", [], "covered"),
    ],
    ids=["budget_spent", "covered"],
)
async def test_a_gap_that_cannot_be_asked_is_invalid_input(db, outcome, coverage, members, needle):
    job_id = await _seed(db, outcome=outcome, coverage=coverage, members=members)
    writer = _writer("never")
    with pytest.raises(McpError) as exc:
        await _call(db, job_id, "Some new testimony.", bridge=_bridge(), writer=writer)
    assert exc.value.error.code == _INVALID_INPUT
    assert needle in exc.value.error.message
    assert "Cloud infrastructure" in exc.value.error.message
    assert writer.calls == []


@pytest.mark.asyncio
async def test_a_no_change_answer_reports_no_change_with_its_follow_up(db):
    job_id = await _seed(db)
    writer = _writer("Kubernetes and Terraform — where?", "Something adjacent?")
    out = await _call(db, job_id, "Not sure, really.", bridge=_bridge(addressed=False), writer=writer)

    assert out["status"] == "no_change"
    assert out["coverage"] == "open"
    assert out["budget_remaining"] == 1
    assert out["follow_up_question"] == "Something adjacent?"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "coverage,expected",
    [("covered", "addressed"), ("partly_covered", "partly_covered"),
     ("open", "partly_covered"), ("declined", "denial_recorded"), (None, "addressed")],
)
async def test_status_reads_the_turns_own_record(db, coverage, expected):
    """A change landed: `addressed` only when the gap is now covered; a turn
    that wrote no record (a legacy row) keeps the pre-ADR-089 `addressed`."""
    from applire.mcp import server
    from applire.schemas.session import (
        ClusterCoverage, SessionCreateResponse, SessionMessageResponse,
    )

    job_id = await _seed(db)
    sid = uuid.uuid4()
    created = SessionCreateResponse(
        session_id=sid, mode="targeted", first_question="Q?", question="Q?",
        estimated_questions=2, gaps_total=1, gaps_remaining=1,
    )
    cc = None if coverage is None else ClusterCoverage(
        cluster_id=_INFRA, coverage=coverage, open_concepts=[], budget_remaining=1,
    )
    result = SessionMessageResponse(
        complete=True, reason="gaps_resolved", completeness_score=0.5,
        changes_applied=True, denial_recorded=False, cluster_coverage=cc,
    )
    with ExitStack() as stack:
        stack.enter_context(patch.object(server, "get_db", return_value=_db_cm(db)))
        stack.enter_context(patch.object(server, "get_provider", return_value=MagicMock()))
        stack.enter_context(patch.object(server.session_svc, "create_session",
                                         new=AsyncMock(return_value=created)))
        stack.enter_context(patch.object(server.session_svc, "send_message",
                                         new=AsyncMock(return_value=result)))
        out = await server.resolve_gap(job_id=str(job_id), gap_id=_INFRA, answer="Testimony.")
    assert out["status"] == expected
