# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-089 clauses 1, 2, 6 at the interview doors (WP-B).

The per-gap record is written by EVERY answered cluster turn, the budget is
shared across sessions, a partially covering answer earns ONE follow-up aimed
at the open members, a spent or covered gap is refused, a waiting follow-up is
resumed (ruling B-3), and the full interview reads the record: it skips what
cannot be asked, seeds the budget, and hands the question writer the earlier
exchange.

Every test drives the REAL ``send_message`` / ``create_session`` path against
an in-memory SQLite database. Only the three LLM-bound seams are doubled: the
reconciler (a FAITHFUL double that writes the vault it reports, like
``test_session_service._bridge_writing``), the question writer, and the
completion recompute (``analyze_gaps`` — Lead A's merge, pinned in its own
tests; here only its ``answer_scope`` argument is read).

Run:
    LLM_PROVIDER=mistral DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend \\
      python3 -m pytest tests/unit/test_gap_followups_session.py -q
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

from tests.support.profile_factory import make_master_profile  # noqa: E402

_INFRA = "cluster-infra"
_API = "cluster-api"
_PER_GAP = 2  # INTERVIEW_MAX_QUESTIONS_PER_GAP default — asserted below


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _row(concept: str, status: str = "gap", *, claimable: bool = False, sources=("required",)):
    return {
        "concept": concept, "surface_forms": [concept], "sources": list(sources),
        "fit_weight": 1.0, "status": status, "evidence": "" if not claimable else "cv",
        "claimable": claimable,
    }


def _cluster(cid: str, label: str, members: list[str], *, category: str = "C", outcome=None,
             coverage: str = "open") -> dict:
    return {
        "id": cid, "label": label, "category": category, "gaps": list(members),
        "jd_skills": list(members), "jd_context": "",
        "outcome": outcome or {"asked": 0, "covered": [], "declined": [], "session_ids": []},
        "coverage": coverage,
    }


async def _seed(db, *, clusters=None, ledger=None, jd_language="en"):
    """Job + profile + ONE analysis row carrying a keyword ledger and clusters."""
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis

    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Platform Engineer. Requirements: Kubernetes, Terraform, FastAPI.",
        role_title="Platform Engineer",
        required_skills=["Kubernetes", "Terraform", "FastAPI"],
        nice_to_have_skills=[], keywords=[], seniority_level="Senior",
        company_culture_signals=[], language_requirement="English", jd_language=jd_language,
    )
    profile = make_master_profile(profile_json={
        "personal_info": {"name": "Mara Test", "email": "mara@example.de"},
        "skills": [{"name": "Python", "category": "technical"}],
        "work_experience": [{"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}],
    })
    db.add_all([job, profile])
    await db.flush()
    row = GapAnalysis(
        job_analysis_id=job.id, profile_id=profile.id, match_score=0.4,
        critical_gaps=[], minor_gaps=[], strengths=["Python"], keyword_gaps=[],
        category_a=["Python"], category_b=[], category_c=["Kubernetes", "Terraform", "FastAPI"],
        keyword_ledger=ledger if ledger is not None else [
            _row("Python", "direct", claimable=True),
            _row("Kubernetes"), _row("Terraform"), _row("FastAPI"),
        ],
        gap_clusters=clusters if clusters is not None else [
            _cluster(_INFRA, "Cloud infrastructure", ["Kubernetes", "Terraform"]),
            _cluster(_API, "API development", ["FastAPI"]),
        ],
    )
    db.add(row)
    await db.commit()
    return job, profile, row


def _writing_bridge(*, add_skills=(), deny=(), addressed=None):
    """A faithful ``reconcile_interview_turn`` double: it WRITES the vault it
    reports (skills added / denials recorded) and returns the turn result."""
    from applire.models.profile import authorized_profile_write
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    async def _run(db, *, profile_record, **_kwargs):
        pj = copy.deepcopy(profile_record.profile_json)
        changes = []
        for name in add_skills:
            pj.setdefault("skills", []).append({"name": name, "category": "technical"})
            changes.append(FieldChange(section="skills", field=name, action="added", new_value=name))
        if deny:
            meta = pj.setdefault("metadata", {})
            meta.setdefault("denied_concepts", [])
            for concept in deny:
                meta["denied_concepts"].append({
                    "concept": concept, "statement": f"I have never used {concept}.",
                    "source": "interview", "date": "2026-09-23", "denial_level": "direct",
                })
        with authorized_profile_write():
            profile_record.profile_json = pj
        await db.flush()
        return InterviewTurnResult(
            profile_dict=pj,
            changes=changes,
            addressed=bool(changes) if addressed is None else addressed,
            denial_recorded=bool(deny),
            denied_concepts=list(deny),
        )

    return AsyncMock(side_effect=_run)


def _writer(*questions):
    """The question writer double: returns the given questions in order and
    records every call's kwargs."""
    seq = list(questions) or ["Next question?"]
    calls: list[dict] = []

    async def _gen(state, profile, provider, **kwargs):
        calls.append({"gap": state["critical_gaps"][state["current_gap_index"]], **kwargs})
        q = seq.pop(0) if len(seq) > 1 else seq[0]
        return {"question": q, "choices": [f"choice for {q}"]}

    mock = AsyncMock(side_effect=_gen)
    mock.calls = calls
    return mock


def _provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="unused")
    provider.aparse_json = AsyncMock(return_value={"approved": True})
    return provider


async def _latest_cluster(db, job_id, cid):
    from applire.models.gap import GapAnalysis

    rows = (await db.execute(
        select(GapAnalysis).where(GapAnalysis.job_analysis_id == job_id)
        .order_by(GapAnalysis.created_at.desc())
    )).scalars().all()
    for row in rows:
        await db.refresh(row)
        for c in row.gap_clusters or []:
            if c.get("id") == cid:
                return c
    return None


async def _gap_click(db, job, cid, writer):
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session

    with patch("applire.services.session.question_generator_with_profile", new=writer):
        return await create_session(
            SessionCreateRequest(job_id=job.id, mode="targeted", target_gap=cid), db, _provider()
        )


async def _answer(db, session_id, text, bridge, writer, recompute=None):
    from applire.services.session import send_message

    with (
        patch("applire.services.session.reconcile_interview_turn", new=bridge),
        patch("applire.services.session.question_generator_with_profile", new=writer),
        patch("applire.services.session.analyze_gaps", new=recompute or AsyncMock()),
    ):
        return await send_message(session_id, text, db, _provider())


def test_the_default_per_gap_budget_is_two():
    from applire.constants import INTERVIEW_MAX_QUESTIONS_PER_GAP

    assert INTERVIEW_MAX_QUESTIONS_PER_GAP == _PER_GAP


# ---------------------------------------------------------------------------
# Clause 1 — the micro-session is the formula's n = 1 case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_micro_session_ceiling_is_the_formula_n1_case(db):
    from applire.models.session import InterviewSession
    from applire.services.interview.budget import derive_hard_ceiling

    job, _profile, _row_ = await _seed(db)
    created = await _gap_click(db, job, _INFRA, _writer("Tell me about Kubernetes and Terraform."))

    assert created.hard_ceiling == derive_hard_ceiling(1)
    assert created.estimated_questions == _PER_GAP
    record = await db.get(InterviewSession, created.session_id)
    assert record.state["micro_session"] is True  # the #627 predicate stays the marker


# ---------------------------------------------------------------------------
# Clause 2 — the partial-coverage follow-up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_answer_earns_one_follow_up_aimed_at_the_open_member(db):
    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.", "And Terraform?")
    created = await _gap_click(db, job, _INFRA, writer)

    answer = "I ran Kubernetes clusters in production at Acme for three years."
    resp = await _answer(db, created.session_id, answer,
                         _writing_bridge(add_skills=["Kubernetes"]), writer)

    assert resp.complete is False, "a partially covering answer earns a follow-up"
    assert resp.question == "And Terraform?"
    assert resp.choices == ["choice for And Terraform?"]
    follow_up_call = writer.calls[-1]
    assert follow_up_call["follow_up_focus"] == ["Terraform"], (
        "the follow-up is aimed at exactly the open member, by name"
    )
    assert "follow_up_hint" not in follow_up_call, "never the adjacent-domain retry prompt"
    assert resp.cluster_coverage is not None
    assert resp.cluster_coverage.cluster_id == _INFRA
    assert resp.cluster_coverage.coverage == "partly_covered"
    assert resp.cluster_coverage.open_concepts == ["Terraform"]
    assert resp.cluster_coverage.budget_remaining == _PER_GAP - 1
    assert resp.changes_applied is True

    persisted = await _latest_cluster(db, job.id, _INFRA)
    assert persisted["outcome"]["asked"] == 1
    assert persisted["outcome"]["covered"] == ["Kubernetes"]
    assert persisted["gaps"] == ["Terraform"]
    assert persisted["outcome"]["session_ids"] == [str(created.session_id)]
    assert persisted["coverage"] == "partly_covered"


@pytest.mark.asyncio
async def test_follow_up_answer_completes_and_spends_the_budget(db):
    from applire.services.gap_coverage import AnswerScope

    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.", "And Terraform?")
    created = await _gap_click(db, job, _INFRA, writer)
    a1 = "I ran Kubernetes clusters in production at Acme for three years."
    await _answer(db, created.session_id, a1, _writing_bridge(add_skills=["Kubernetes"]), writer)

    recompute = AsyncMock()
    a2 = "Terraform only from a weekend course, never at work."
    # The follow-up's answer changes nothing: the budget (2) is spent, so the
    # session completes instead of asking a third time.
    resp = await _answer(db, created.session_id, a2, _writing_bridge(addressed=False),
                         writer, recompute)

    assert resp.complete is True
    assert resp.reason == "gaps_resolved"
    assert resp.cluster_coverage.budget_remaining == 0
    assert resp.cluster_coverage.open_concepts == ["Terraform"]
    persisted = await _latest_cluster(db, job.id, _INFRA)
    assert persisted["outcome"]["asked"] == 2
    # Clause 5 — the completion recompute is answer-driven, scoped to what this
    # session touched.
    kwargs = recompute.await_args.kwargs
    assert kwargs["answer_scope"] == AnswerScope(cluster_ids=(_INFRA,))
    assert "clamp_to_previous" not in kwargs


@pytest.mark.asyncio
async def test_fully_covering_answer_completes_without_a_follow_up(db):
    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.")
    created = await _gap_click(db, job, _INFRA, writer)
    resp = await _answer(
        db, created.session_id,
        "Kubernetes and Terraform are my daily tools — I run both at Acme.",
        _writing_bridge(add_skills=["Kubernetes", "Terraform"]), writer,
    )

    assert resp.complete is True
    assert resp.cluster_coverage.coverage == "covered"
    assert resp.cluster_coverage.open_concepts == []
    assert resp.changes_applied is True
    assert len(writer.calls) == 1, "no follow-up question was generated"


@pytest.mark.asyncio
async def test_no_change_answer_keeps_the_retry_follow_up(db):
    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.", "Something adjacent?")
    created = await _gap_click(db, job, _INFRA, writer)
    resp = await _answer(db, created.session_id, "Hmm, not sure what to say.",
                         _writing_bridge(addressed=False), writer)

    assert resp.complete is False
    assert "follow_up_hint" in writer.calls[-1], "the no-change retry keeps its own prompt"
    assert "follow_up_focus" not in writer.calls[-1]
    assert resp.cluster_coverage.coverage == "open"
    assert resp.cluster_coverage.budget_remaining == _PER_GAP - 1
    assert resp.changes_applied is False


@pytest.mark.asyncio
async def test_mixed_turn_declines_one_member_and_follows_up_on_the_other(db):
    """Precedence: a turn that both added content and denied a member is not a
    probe (the probe needs a denial-only turn); the partial-coverage follow-up
    then aims only at the member that is still OPEN — never the declined one."""
    job, _profile, _ = await _seed(db, clusters=[
        _cluster(_INFRA, "Cloud infrastructure", ["Kubernetes", "Terraform", "Helm"]),
    ], ledger=[_row("Kubernetes"), _row("Terraform"), _row("Helm")])
    writer = _writer("Tell me about your cloud tooling.", "What about Helm?")
    created = await _gap_click(db, job, _INFRA, writer)
    resp = await _answer(
        db, created.session_id,
        "Kubernetes daily at Acme. I have never used Terraform.",
        _writing_bridge(add_skills=["Kubernetes"], deny=["Terraform"]), writer,
    )

    assert resp.complete is False
    assert writer.calls[-1]["follow_up_focus"] == ["Helm"]
    persisted = await _latest_cluster(db, job.id, _INFRA)
    assert persisted["outcome"]["covered"] == ["Kubernetes"]
    assert persisted["outcome"]["declined"] == ["Terraform"]
    assert persisted["gaps"] == ["Helm"]


@pytest.mark.asyncio
async def test_denial_only_turn_on_a_required_concept_still_gets_the_probe_first(db):
    """ADR-064's transfer probe outranks every other follow-up."""
    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.", "Any container orchestration?")
    created = await _gap_click(db, job, _INFRA, writer)
    with patch(
        "applire.services.session._mark_probe_asked",
        new=AsyncMock(side_effect=lambda db, rec, concept, sid: rec.profile_json),
    ):
        resp = await _answer(db, created.session_id, "I have never used Kubernetes.",
                             _writing_bridge(deny=["Kubernetes"]), writer)

    assert resp.complete is False
    assert writer.calls[-1].get("denial_probe") is True
    assert resp.denial_recorded is True
    assert resp.cluster_coverage.budget_remaining == _PER_GAP - 1


# ---------------------------------------------------------------------------
# Clause 1 — one budget across sessions, the full interview honours it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_interview_after_a_gap_click_shares_the_budget_and_reads_the_exchange(db):
    from applire.schemas.session import SessionCreateRequest
    from applire.models.session import InterviewSession
    from applire.services.session import create_session

    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.", "And Terraform?")
    micro = await _gap_click(db, job, _INFRA, writer)
    a1 = "I ran Kubernetes clusters in production at Acme for three years."
    await _answer(db, micro.session_id, a1, _writing_bridge(add_skills=["Kubernetes"]), writer)
    # The candidate leaves the follow-up unanswered and starts the full interview.

    full_writer = _writer("Terraform — where have you used it?", "FastAPI?")
    with patch("applire.services.session.question_generator_with_profile", new=full_writer):
        full = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, _provider())

    retired = await db.get(InterviewSession, micro.session_id)
    assert retired.status == "complete"
    assert full.gaps_total == 2 and full.current_gap_id == _INFRA
    first_call = full_writer.calls[0]
    assert first_call["prior_exchanges"] == [
        {"question": "Tell me about Kubernetes and Terraform.", "answer": a1}
    ], "the question writer sees the Gap-Click exchange, read from its transcript"

    record = await db.get(InterviewSession, full.session_id)
    assert record.state["questions_per_gap"][_INFRA] == 2, (
        "the cluster already spent one question elsewhere — its counter starts there"
    )
    assert record.state["gap_clusters_by_id"][_INFRA]["gaps"] == ["Terraform"]

    # A second partial answer: addressed, a member still open — but the budget
    # is spent, so NO follow-up: the interview advances to the next cluster.
    resp = await _answer(db, full.session_id, "Terraform a bit, together with Pulumi at Acme.",
                         _writing_bridge(add_skills=["Pulumi"]), full_writer)
    assert resp.complete is False
    assert resp.current_gap_id == _API, "budget spent — advance, never a third question"
    assert "follow_up_focus" not in full_writer.calls[-1]
    persisted = await _latest_cluster(db, job.id, _INFRA)
    assert persisted["outcome"]["asked"] == 2
    assert persisted["outcome"]["session_ids"] == [str(micro.session_id), str(full.session_id)]


@pytest.mark.asyncio
async def test_full_interview_skips_a_spent_and_a_covered_cluster(db):
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session

    job, _profile, _ = await _seed(db, clusters=[
        _cluster(_INFRA, "Cloud infrastructure", ["Terraform"],
                 outcome={"asked": 2, "covered": [], "declined": [], "session_ids": []}),
        _cluster(_API, "API development", [], coverage="covered",
                 outcome={"asked": 1, "covered": ["FastAPI"], "declined": [], "session_ids": []}),
        _cluster("cluster-k8s", "Kubernetes", ["Kubernetes"]),
    ])
    writer = _writer("Kubernetes?")
    with patch("applire.services.session.question_generator_with_profile", new=writer):
        full = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, _provider())

    assert full.gaps_total == 1
    assert full.current_gap_id == "cluster-k8s"


@pytest.mark.asyncio
async def test_nothing_left_to_ask_says_so_honestly(db):
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session

    job, _profile, _ = await _seed(db, clusters=[
        _cluster(_INFRA, "Cloud infrastructure", ["Terraform"],
                 outcome={"asked": 2, "covered": [], "declined": [], "session_ids": []}),
    ])
    with patch("applire.services.session.question_generator_with_profile", new=_writer("x")):
        full = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, _provider())

    assert full.gaps_total == 0
    assert "already been worked" in full.first_question
    assert "strong match" not in full.first_question
    assert "unavailable" not in full.first_question


# ---------------------------------------------------------------------------
# Clause 1/7 — refusal of a gap that cannot be asked (HTTP 409 at the REST door)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome,coverage,code",
    [
        ({"asked": 2, "covered": [], "declined": [], "session_ids": []}, "open", "gap_budget_spent"),
        ({"asked": 1, "covered": ["Kubernetes", "Terraform"], "declined": [], "session_ids": []},
         "covered", "gap_already_covered"),
        ({"asked": 1, "covered": [], "declined": ["Kubernetes", "Terraform"], "session_ids": []},
         "declined", "gap_already_covered"),
    ],
    ids=["budget_spent", "covered", "declined"],
)
@pytest.mark.asyncio
async def test_gap_click_on_a_cluster_that_cannot_be_asked_is_refused(db, outcome, coverage, code):
    from applire.services.session import GapNotAskableError

    members = [] if coverage in ("covered", "declined") else ["Kubernetes", "Terraform"]
    job, _profile, _ = await _seed(db, clusters=[
        _cluster(_INFRA, "Cloud infrastructure", members, outcome=outcome, coverage=coverage),
    ])
    writer = _writer("never asked")
    with pytest.raises(GapNotAskableError) as exc:
        await _gap_click(db, job, _INFRA, writer)
    assert exc.value.error_code == code
    assert "Cloud infrastructure" in exc.value.message, "the refusal names the cluster"
    assert writer.calls == [], "no question is generated for a refused gap"


@pytest.mark.asyncio
async def test_liability_only_cluster_stays_askable(db):
    """Ruling A-1 — a #260 keyword liability is OPEN until its story is in the
    vault; the "tell the story" exit must keep opening a session on it."""
    job, _profile, _ = await _seed(db, clusters=[
        _cluster(_INFRA, "Cloud infrastructure", ["Kubernetes"], coverage="partly_covered"),
    ], ledger=[{**_row("Kubernetes", "direct", claimable=True), "narrative_backed": False}])
    created = await _gap_click(db, job, _INFRA, _writer("Tell the Kubernetes story?"))
    assert created.first_question == "Tell the Kubernetes story?"


def test_the_rest_door_answers_409_with_the_error_code():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.session import _get_provider, router
    from applire.services.session import GapNotAskableError

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[_get_provider] = lambda: MagicMock()
    app.dependency_overrides[get_auth_provider] = lambda: MagicMock()
    refusal = GapNotAskableError("gap_budget_spent", '"Cloud infrastructure" is spent.', _INFRA)
    with patch("applire.routers.session.create_session", new=AsyncMock(side_effect=refusal)):
        resp = TestClient(app).post(
            "/api/session", json={"job_id": str(uuid.uuid4()), "target_gap": _INFRA}
        )
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "error_code": "gap_budget_spent", "message": '"Cloud infrastructure" is spent.',
    }


# ---------------------------------------------------------------------------
# Ruling B-3 — a waiting follow-up is resumed, not replaced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reopening_a_gap_with_a_waiting_follow_up_resumes_it(db):
    job, _profile, _ = await _seed(db)
    writer = _writer("Tell me about Kubernetes and Terraform.", "And Terraform?")
    created = await _gap_click(db, job, _INFRA, writer)
    await _answer(db, created.session_id, "Kubernetes daily at Acme.",
                  _writing_bridge(add_skills=["Kubernetes"]), writer)

    again = _writer("A DIFFERENT, freshly generated question")
    reopened = await _gap_click(db, job, _INFRA, again)

    assert reopened.session_id == created.session_id, "the SAME session, not a new one"
    assert reopened.first_question == "And Terraform?"
    assert reopened.question == "And Terraform?"
    assert reopened.choices == ["choice for And Terraform?"]
    assert reopened.resumed is True
    # A resumed micro-session reports its cluster's remaining budget, like a
    # fresh one — not the full-interview ceiling-midpoint estimate.
    assert reopened.estimated_questions == 1
    assert again.calls == [], "nothing is generated twice for one budget slot"


@pytest.mark.asyncio
async def test_an_unanswered_orphan_is_still_replaced(db):
    from applire.models.session import InterviewSession

    job, _profile, _ = await _seed(db)
    first = await _gap_click(db, job, _INFRA, _writer("Q1?"))
    second = await _gap_click(db, job, _INFRA, _writer("Q1 again?"))

    assert second.session_id != first.session_id
    assert (await db.get(InterviewSession, first.session_id)).status == "complete"


# ---------------------------------------------------------------------------
# Clause 3 — the record is part of the turn's ONE transaction (#179)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_turn_record_rolls_back_with_a_failed_follow_up(db):
    from applire.exceptions import LLMTruncatedError

    job, _profile, _ = await _seed(db)
    job_id = job.id  # captured: the rollback below expires every loaded object
    created = await _gap_click(db, job, _INFRA, _writer("Tell me about Kubernetes and Terraform."))
    failing = AsyncMock(side_effect=LLMTruncatedError("cut"))
    with pytest.raises(LLMTruncatedError):
        await _answer(db, created.session_id, "Kubernetes daily at Acme.",
                      _writing_bridge(add_skills=["Kubernetes"]), failing)
    await db.rollback()

    persisted = await _latest_cluster(db, job_id, _INFRA)
    assert persisted["outcome"]["asked"] == 0, "a rolled-back turn charges nothing"
    assert persisted["outcome"]["session_ids"] == []


# ---------------------------------------------------------------------------
# Clauses 5/6 — the scope and the input view are read from the transcript
# ---------------------------------------------------------------------------


def test_answer_scope_names_only_the_worked_clusters():
    from applire.services.gap_coverage import AnswerScope
    from applire.services.session import _answer_scope

    state = {
        "messages": [
            {"role": "assistant", "content": "Q1"}, {"role": "user", "content": "A1"},
            {"role": "assistant", "content": "Q2"}, {"role": "user", "content": "A2"},
            {"role": "assistant", "content": "Q3"}, {"role": "user", "content": "done"},
        ],
        "cluster_turns": [
            {"cluster_id": _INFRA, "q": 0, "a": 1},
            {"cluster_id": _INFRA, "q": 2, "a": 3},
        ],
    }
    assert _answer_scope(state) == AnswerScope(cluster_ids=(_INFRA,))
    assert _answer_scope({}) == AnswerScope()


@pytest.mark.asyncio
async def test_prior_exchanges_come_only_from_live_referenced_transcripts(db):
    from applire.models.session import InterviewSession
    from applire.services.session import _prior_exchanges

    job, profile, row = await _seed(db)

    def _session(messages, turns, *, deleted=False):
        return InterviewSession(
            job_analysis_id=job.id, gap_analysis_id=row.id, profile_id=profile.id,
            mode="targeted", status="complete",
            state={"messages": messages, "cluster_turns": turns},
            hard_ceiling=4, questions_asked=2,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            deleted_at=datetime.now(timezone.utc) if deleted else None,
        )

    live = _session(
        [{"role": "assistant", "content": "Q-infra"}, {"role": "user", "content": "A-infra"},
         {"role": "assistant", "content": "Q-api"}, {"role": "user", "content": "A-api"}],
        [{"cluster_id": _INFRA, "q": 0, "a": 1}, {"cluster_id": _API, "q": 2, "a": 3}],
    )
    gone = _session([{"role": "assistant", "content": "Q-old"}, {"role": "user", "content": "A-old"}],
                    [{"cluster_id": _INFRA, "q": 0, "a": 1}], deleted=True)
    broken = _session([{"role": "user", "content": "x"}], [{"cluster_id": _INFRA, "q": 0, "a": 5}])
    db.add_all([live, gone, broken])
    await db.commit()

    cluster = _cluster(_INFRA, "Cloud infrastructure", ["Terraform"], outcome={
        "asked": 3, "covered": [], "declined": [],
        "session_ids": [str(gone.id), str(live.id), str(broken.id), str(uuid.uuid4())],
    })
    assert await _prior_exchanges(db, cluster) == [{"question": "Q-infra", "answer": "A-infra"}]
    assert await _prior_exchanges(db, cluster, exclude_session_id=str(live.id)) == []


# ---------------------------------------------------------------------------
# Ruling B-4 — the follow-up never re-asks what the answer just named
# ---------------------------------------------------------------------------


def _b_cluster_seed():
    members = ["Prometheus", "Grafana", "SLOs"]
    clusters = [_cluster(_INFRA, "Observability practice", members, category="B",
                         coverage="partly_covered")]
    ledger = [_row(m, "partial", claimable=True) for m in members]
    return clusters, ledger


@pytest.mark.asyncio
async def test_b_cluster_follow_up_asks_only_what_the_answer_did_not_name(db):
    """A Category B member stays `partial` on the ledger after the candidate
    describes it (the #188 seam never re-upgrades a claimable row), so the
    record keeps it open — but the follow-up must not ask about it again."""
    clusters, ledger = _b_cluster_seed()
    job, _profile, _ = await _seed(db, clusters=clusters, ledger=ledger)
    writer = _writer("Observability — Prometheus, Grafana, SLOs?", "And Grafana and SLOs?")
    created = await _gap_click(db, job, _INFRA, writer)
    resp = await _answer(db, created.session_id,
                         "I built the Prometheus setup myself: RED metrics for all four services.",
                         _writing_bridge(add_skills=["Prometheus"]), writer)

    assert resp.complete is False
    assert writer.calls[-1]["follow_up_focus"] == ["Grafana", "SLOs"]
    # The record stays strict until the completion recompute re-classifies.
    assert resp.cluster_coverage.open_concepts == ["Prometheus", "Grafana", "SLOs"]
    assert resp.cluster_coverage.coverage == "partly_covered"


@pytest.mark.asyncio
async def test_b_cluster_answer_naming_every_member_earns_no_follow_up(db):
    clusters, ledger = _b_cluster_seed()
    job, _profile, _ = await _seed(db, clusters=clusters, ledger=ledger)
    writer = _writer("Observability — Prometheus, Grafana, SLOs?")
    created = await _gap_click(db, job, _INFRA, writer)
    resp = await _answer(
        db, created.session_id,
        "Prometheus metrics, Grafana dashboards, and SLOs with error budgets — all mine at Acme.",
        _writing_bridge(add_skills=["Prometheus", "Grafana", "SLOs"]), writer,
    )

    assert resp.complete is True, "every open member was named — nothing left to follow up on"
    assert len(writer.calls) == 1


@pytest.mark.asyncio
async def test_liability_story_answer_earns_no_second_story_question(db):
    """Ruling A-1 + B-4 — a #260 liability stays OPEN until a recompute sees
    its story; the "tell the story" answer that names it must not be asked for
    the story a second time in the same session."""
    job, _profile, _ = await _seed(db, clusters=[
        _cluster(_INFRA, "Cloud infrastructure", ["Kubernetes"], coverage="partly_covered"),
    ], ledger=[{**_row("Kubernetes", "direct", claimable=True), "narrative_backed": False}])
    writer = _writer("Tell the Kubernetes story?")
    created = await _gap_click(db, job, _INFRA, writer)
    resp = await _answer(
        db, created.session_id,
        "I led the Kubernetes migration at Acme: 12 services in 9 months, deploys 45 → 8 minutes.",
        _writing_bridge(add_skills=["Kubernetes migration"]), writer,
    )

    assert resp.complete is True
    assert len(writer.calls) == 1, "no second story question"
    assert resp.cluster_coverage.open_concepts == ["Kubernetes"], "the record stays strict"


def test_members_named_by_reads_the_ledger_surface_forms():
    from applire.services.session import _members_named_by

    ledger = [{"concept": "Kubernetes", "surface_forms": ["Kubernetes", "K8s"]},
              {"concept": "Terraform", "surface_forms": ["Terraform"]}]
    assert _members_named_by("We ran K8s on EKS.", ["Kubernetes", "Terraform"], ledger) == {"Kubernetes"}
    assert _members_named_by("", ["Kubernetes"], ledger) == set()


# ---------------------------------------------------------------------------
# ADR-038 — the strings the per-gap record adds follow the conversation language
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lang,worked,spent,covered,foreign",
    [
        ("en", "already been worked through", "question budget", "already covered",
         ["bereits", "Lücke"]),
        ("de", "bereits bearbeitet", "Fragenbudget", "bereits abgedeckt",
         ["already", "worked through", "per gap", "is spent"]),
    ],
    ids=["en", "de"],
)
@pytest.mark.asyncio
async def test_the_record_strings_follow_the_conversation_language(
    db, lang, worked, spent, covered, foreign
):
    """The empty-plan first question and both 409 refusals are rendered in the
    session's conversation language (no ``ui_language`` set → the JD's), the
    way the gate/dispute copy is — a German UI shows them verbatim."""
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import GapNotAskableError, create_session

    job, _profile, _ = await _seed(db, clusters=[
        _cluster(_INFRA, "Cloud infrastructure", ["Terraform"],
                 outcome={"asked": 2, "covered": [], "declined": [], "session_ids": []}),
        _cluster(_API, "API development", [], coverage="covered",
                 outcome={"asked": 1, "covered": ["FastAPI"], "declined": [], "session_ids": []}),
    ], jd_language=lang)
    writer = _writer("never asked")
    with patch("applire.services.session.question_generator_with_profile", new=writer):
        full = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, _provider())
    assert worked in full.first_question

    with pytest.raises(GapNotAskableError) as spent_exc:
        await _gap_click(db, job, _INFRA, writer)
    with pytest.raises(GapNotAskableError) as covered_exc:
        await _gap_click(db, job, _API, writer)
    assert spent in spent_exc.value.message and "Cloud infrastructure" in spent_exc.value.message
    assert covered in covered_exc.value.message and "API development" in covered_exc.value.message
    for text in (full.first_question, spent_exc.value.message, covered_exc.value.message):
        assert not any(word in text for word in foreign), text
    assert writer.calls == []
