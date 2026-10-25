# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#763 — the interview turn carries the no-write receipt (SF-LLM.7).

A turn that STATED something and wrote nothing already leaves a receipt on the
committed record's ``not_applied`` (``witness.compute_no_write`` via apply.py's
M-1c block), which the Health hub reads. #763 carries that SAME item to the
interview's response so the candidate reads it under their answer.

Three layers, each pinned where it can silently break:

* the bridge, against the REAL committer (a stub LLM provider, nothing patched
  in reconcile/commit) — the receipt equals the persisted item;
* ``no_write_receipt_of`` — the closed-set pick;
* the session seams — ONE named test per response site in ``services.session``
  that builds a response from a reconciled turn, each driving the real
  ``send_message`` with the bridge doubled. A site that forgets to thread the
  field would otherwise ship ``None`` on that path and nothing else would fail.

Run:
    LLM_PROVIDER=mistral DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend \\
      python3 -m pytest tests/unit/test_interview_no_write_receipt.py -q
"""

import copy
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile, retire_live_profiles  # noqa: E402

_POSITIVE = "I led the migration of our billing platform to Kubernetes at Contoso in 2022."
_DENIAL = "I have never worked with Kubernetes."
_RECEIPT = "no_write_already_known"

_INFRA = "cluster-infra"
_API = "cluster-api"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """Full-schema in-memory SQLite (session + gap + profile rows)."""
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


# ---------------------------------------------------------------------------
# 1. The bridge against the REAL committer
# ---------------------------------------------------------------------------


class _Reply:
    """A stub LLM provider whose reconcile reply is a fixed payload."""

    def __init__(self, **payload):
        self._payload = {"ops": [], "ambiguities": [], **payload}

    async def aparse_json(self, prompt, **kw):
        return copy.deepcopy(self._payload)


async def _real_turn(db, provider, answer, *, profile_json=None):
    """Seed a profile row, run ONE interview turn through the real committer."""
    await retire_live_profiles(db)
    record = make_master_profile(
        profile_json=profile_json
        or {"personal_info": {"name": "Mara Test", "email": "mara@example.de"}}
    )
    db.add(record)
    await db.commit()

    from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn

    out = await reconcile_interview_turn(
        db,
        profile_record=record,
        gap="Kubernetes",
        question="Where have you used Kubernetes?",
        answer=answer,
        provider=provider,
        session_id="s-763",
    )
    await db.commit()  # the bridge flushes; the DOOR commits (ADR-063 cl. 6)
    return out


@pytest.mark.asyncio
async def test_bridge_empty_batch_without_reason_carries_no_write(db):
    out = await _real_turn(db, _Reply(), _POSITIVE)
    assert out.addressed is False
    assert out.no_write_receipt == "no_write"


@pytest.mark.asyncio
async def test_bridge_already_known_reason_carries_no_write_already_known(db):
    out = await _real_turn(db, _Reply(empty_reason="already_known"), _POSITIVE)
    assert out.addressed is False
    assert out.no_write_receipt == "no_write_already_known"


@pytest.mark.asyncio
async def test_bridge_question_only_reason_carries_no_write_question_only(db):
    out = await _real_turn(db, _Reply(empty_reason="question_only"), _POSITIVE)
    assert out.addressed is False
    assert out.no_write_receipt == "no_write_question_only"


@pytest.mark.asyncio
async def test_bridge_denial_only_answer_carries_no_receipt(db):
    """A denial-only answer has no positive residue — the denial is recorded
    and nothing else is owed, so there is nothing to say 'was not recorded'."""
    out = await _real_turn(db, _Reply(denials=["Kubernetes"]), _DENIAL)
    assert out.denial_recorded is True
    assert out.addressed is False
    assert out.no_write_receipt is None


@pytest.mark.asyncio
async def test_bridge_denial_answer_with_empty_batch_and_reason_still_no_receipt(db):
    """The model's `empty_reason` never overrides the FACT half: no positive
    residue, no receipt — whatever the model said."""
    out = await _real_turn(db, _Reply(empty_reason="already_known"), _DENIAL)
    assert out.no_write_receipt is None


@pytest.mark.asyncio
async def test_bridge_turn_that_writes_carries_no_receipt(db):
    op = {
        "op": "upsert_skill", "name": "Kubernetes", "category": "technical",
        "proficiency": "advanced", "evidence": [],
    }
    out = await _real_turn(db, _Reply(ops=[op]), _POSITIVE)
    assert out.addressed is True, "the control: this batch really wrote"
    assert any(s.get("name") == "Kubernetes" for s in out.profile_dict.get("skills", []))
    assert out.no_write_receipt is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (_Reply(), "no_write"),
        (_Reply(empty_reason="already_known"), "no_write_already_known"),
        (_Reply(empty_reason="question_only"), "no_write_question_only"),
    ],
)
async def test_bridge_receipt_is_the_item_the_health_hub_reads(db, reply, expected):
    """One source of truth: the interview's receipt IS the `not_applied` item the
    committer persisted on the profile's last enrichment record."""
    out = await _real_turn(db, reply, _POSITIVE)
    persisted = out.profile_dict["metadata"]["enrichment_history"][-1]["not_applied"]
    reasons = [item["reason"] for item in persisted]
    assert reasons == [expected], "exactly one receipt item is persisted for the turn"
    assert out.no_write_receipt == reasons[0]


# ---------------------------------------------------------------------------
# 2. no_write_receipt_of — the closed-set pick
# ---------------------------------------------------------------------------


def _item(reason: str):
    from applire.schemas.profile import ImportNotApplied

    return ImportNotApplied(section=None, label="x", reason=reason)


def test_no_write_reasons_is_the_closed_witness_vocabulary():
    from applire.services.profile.reconcile.interview_bridge import NO_WRITE_REASONS
    from applire.services.profile.reconcile.witness import _EMPTY_REASON_TO_RECEIPT

    assert NO_WRITE_REASONS == frozenset(_EMPTY_REASON_TO_RECEIPT.values()) | {"no_write"}


@pytest.mark.parametrize("reason", ["no_write", "no_write_already_known", "no_write_question_only"])
def test_no_write_receipt_of_returns_each_no_write_reason(reason):
    from applire.services.profile.reconcile.interview_bridge import no_write_receipt_of

    assert no_write_receipt_of([_item(reason)]) == reason


def test_no_write_receipt_of_picks_the_first_no_write_reason():
    from applire.services.profile.reconcile.interview_bridge import no_write_receipt_of

    items = [_item("no_write_question_only"), _item("no_write_already_known")]
    assert no_write_receipt_of(items) == "no_write_question_only"


def test_no_write_receipt_of_ignores_other_reasons():
    from applire.services.profile.reconcile.interview_bridge import no_write_receipt_of

    others = [_item("op_rejected"), _item("confirmation_discarded"), _item("no_op_carried_entry")]
    assert no_write_receipt_of(others) is None
    # ... and finds the no_write item among them, wherever it sits.
    assert no_write_receipt_of([*others, _item("no_write_already_known")]) == "no_write_already_known"
    assert no_write_receipt_of([_item("op_rejected"), _item("no_write")]) == "no_write"


@pytest.mark.parametrize("empty", [None, [], ()])
def test_no_write_receipt_of_empty_or_none_is_none(empty):
    from applire.services.profile.reconcile.interview_bridge import no_write_receipt_of

    assert no_write_receipt_of(empty) is None


def test_response_schema_accepts_only_the_three_reasons():
    from pydantic import ValidationError

    from applire.schemas.session import SessionMessageResponse

    for reason in ("no_write", "no_write_already_known", "no_write_question_only", None):
        assert SessionMessageResponse(complete=False, no_write_receipt=reason).no_write_receipt == reason
    with pytest.raises(ValidationError):
        SessionMessageResponse(complete=False, no_write_receipt="op_rejected")


# ---------------------------------------------------------------------------
# 3. Session seams — one named test per response site
# ---------------------------------------------------------------------------


def _provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="unused")
    provider.aparse_json = AsyncMock(
        return_value={"question": "Next question?", "choices": None, "approved": True}
    )
    provider.__class__.__name__ = "MockProvider"
    return provider


def _make_job(**kwargs):
    from applire.models.job import JobAnalysis

    defaults = dict(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Platform Engineer. Requirements: Kubernetes, Terraform, FastAPI.",
        role_title="Platform Engineer",
        required_skills=["Kubernetes", "Terraform", "FastAPI"],
        nice_to_have_skills=[], keywords=[], seniority_level="Senior",
        company_culture_signals=[], language_requirement="English", jd_language="en",
    )
    defaults.update(kwargs)
    return JobAnalysis(**defaults)


_PROFILE_JSON = {
    "personal_info": {"name": "Mara Test", "email": "mara@example.de"},
    "skills": [{"name": "Python", "category": "technical"}],
    "work_experience": [{"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}],
}


def _legacy_session(job_id, profile_id, state=None, *, hard_ceiling=12, questions_asked=1):
    """A session with NO recorded cluster (the pre-ADR-089 / guided shape)."""
    from applire.models.session import InterviewSession

    default_state = {
        "mode": "targeted",
        "job_id": str(job_id),
        "gap_analysis_id": None,
        "profile_id": str(profile_id),
        "critical_gaps": ["GCP certification", "FastAPI experience"],
        "gap_categories": {"GCP certification": "C", "FastAPI experience": "C"},
        "gap_clusters_by_id": {
            "GCP certification": {
                "id": "GCP certification", "label": "GCP certification",
                "gaps": ["GCP certification"], "jd_skills": [], "jd_context": "",
            },
            "FastAPI experience": {
                "id": "FastAPI experience", "label": "FastAPI experience",
                "gaps": ["FastAPI experience"], "jd_skills": [], "jd_context": "",
            },
        },
        "addressed_gaps": [],
        "current_gap_index": 0,
        "current_question": "Tell me about your GCP experience.",
        "messages": [{"role": "assistant", "content": "Tell me about your GCP experience."}],
        "questions_asked": questions_asked,
        "hard_ceiling": hard_ceiling,
        "questions_per_gap": {},
        "skipped_gaps": [],
        "full_gaps": [],
    }
    default_state.update(state or {})
    return InterviewSession(
        job_analysis_id=job_id, gap_analysis_id=None, profile_id=profile_id,
        mode="targeted", status="active", state=default_state,
        hard_ceiling=hard_ceiling, questions_asked=questions_asked,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )


async def _seed_legacy(db, state=None, **session_kwargs):
    await retire_live_profiles(db)
    job = _make_job()
    profile = make_master_profile(profile_json=copy.deepcopy(_PROFILE_JSON))
    db.add_all([job, profile])
    await db.flush()
    record = _legacy_session(job.id, profile.id, state, **session_kwargs)
    db.add(record)
    await db.commit()
    return record, profile


def _turn(profile_dict, *, addressed=False, receipt=_RECEIPT, **kwargs):
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    changes = (
        [FieldChange(section="skills", field="GCP", action="added", new_value="GCP")]
        if addressed else []
    )
    return InterviewTurnResult(
        profile_dict=profile_dict, changes=changes, addressed=addressed,
        no_write_receipt=receipt, **kwargs,
    )


def _bridge(turn):
    """A reconcile_interview_turn double that returns ``turn`` (the vault write
    is irrelevant to the receipt seam; the profile dict it carries is the row's)."""
    return AsyncMock(return_value=turn)


async def _send(db, session_id, text, bridge, writer=None):
    from applire.services.session import send_message

    writer = writer or AsyncMock(return_value={"question": "Next question?", "choices": None})
    with (
        patch("applire.services.session.reconcile_interview_turn", new=bridge),
        patch("applire.services.session.question_generator_with_profile", new=writer),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        return await send_message(session_id, text, db, _provider())


# -- the recorded-cluster fixtures (copied in shape from test_gap_followups_session) --


def _row(concept: str, status: str = "gap", *, claimable: bool = False):
    return {
        "concept": concept, "surface_forms": [concept], "sources": ["required"],
        "fit_weight": 1.0, "status": status, "evidence": "cv" if claimable else "",
        "claimable": claimable,
    }


def _cluster(cid, label, members):
    return {
        "id": cid, "label": label, "category": "C", "gaps": list(members),
        "jd_skills": list(members), "jd_context": "",
        "outcome": {"asked": 0, "covered": [], "declined": [], "session_ids": []},
        "coverage": "open",
    }


async def _seed_clusters(db):
    from applire.models.gap import GapAnalysis

    await retire_live_profiles(db)
    job = _make_job()
    profile = make_master_profile(profile_json=copy.deepcopy(_PROFILE_JSON))
    db.add_all([job, profile])
    await db.flush()
    db.add(GapAnalysis(
        job_analysis_id=job.id, profile_id=profile.id, match_score=0.4,
        critical_gaps=[], minor_gaps=[], strengths=["Python"], keyword_gaps=[],
        category_a=["Python"], category_b=[], category_c=["Kubernetes", "Terraform", "FastAPI"],
        keyword_ledger=[
            _row("Python", "direct", claimable=True),
            _row("Kubernetes"), _row("Terraform"), _row("FastAPI"),
        ],
        gap_clusters=[
            _cluster(_INFRA, "Cloud infrastructure", ["Kubernetes", "Terraform"]),
            _cluster(_API, "API development", ["FastAPI"]),
        ],
    ))
    await db.commit()
    return job, profile


def _question_writer(*questions):
    seq = list(questions) or ["Next question?"]

    async def _gen(state, profile, provider, **kwargs):
        q = seq.pop(0) if len(seq) > 1 else seq[0]
        return {"question": q, "choices": None}

    return AsyncMock(side_effect=_gen)


async def _gap_click(db, job, cid, writer):
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session

    with patch("applire.services.session.question_generator_with_profile", new=writer):
        return await create_session(
            SessionCreateRequest(job_id=job.id, mode="targeted", target_gap=cid), db, _provider()
        )


def _writing_bridge(*, add_skills=(), deny=(), receipt=_RECEIPT):
    """A FAITHFUL double: it WRITES the vault it reports (skills added /
    denials recorded), so the per-gap record classifies against a real vault."""
    from applire.models.profile import authorized_profile_write
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    async def _run(db, *, profile_record, **_kwargs):
        pj = copy.deepcopy(profile_record.profile_json)
        changes = []
        for name in add_skills:
            pj.setdefault("skills", []).append({"name": name, "category": "technical"})
            changes.append(FieldChange(section="skills", field=name, action="added", new_value=name))
        meta = pj.setdefault("metadata", {})
        for concept in deny:
            meta.setdefault("denied_concepts", []).append({
                "concept": concept, "statement": f"I have never used {concept}.",
                "source": "interview", "date": "2026-09-23", "denial_level": "direct",
            })
        with authorized_profile_write():
            profile_record.profile_json = pj
        await db.flush()
        return InterviewTurnResult(
            profile_dict=pj, changes=changes, addressed=bool(changes),
            denial_recorded=bool(deny), denied_concepts=list(deny),
            no_write_receipt=receipt,
        )

    return AsyncMock(side_effect=_run)


# -- one named test per response site --


@pytest.mark.asyncio
async def test_receipt_on_no_change_retry(db):
    """`_ask_no_change_retry` — a recorded cluster with open concepts, budget left."""
    job, _profile = await _seed_clusters(db)
    writer = _question_writer("Tell me about Kubernetes and Terraform.", "Something adjacent?")
    created = await _gap_click(db, job, _INFRA, writer)

    resp = await _send(db, created.session_id, _POSITIVE,
                       _writing_bridge(receipt=_RECEIPT), writer)

    assert resp.complete is False
    assert resp.question == "Something adjacent?", "the retry branch, not an advance"
    assert resp.cluster_coverage is not None and resp.cluster_coverage.open_concepts
    assert resp.changes_applied is False
    assert resp.no_write_receipt == _RECEIPT


@pytest.mark.asyncio
async def test_receipt_on_advance_next_question(db):
    """The advance branch returning the next question: per-gap budget spent."""
    record, profile = await _seed_legacy(
        db, state={"questions_per_gap": {"GCP certification": 2}}
    )
    turn = _turn(profile.profile_json)

    resp = await _send(db, record.id, _POSITIVE, _bridge(turn),
                       AsyncMock(return_value={"question": "Tell me about FastAPI.", "choices": None}))

    assert resp.complete is False
    assert resp.question == "Tell me about FastAPI."
    assert resp.current_gap_id == "FastAPI experience", "advanced off the spent gap"
    assert resp.addressed_gap_ids == ["GCP certification"]
    assert resp.no_write_receipt == _RECEIPT


@pytest.mark.asyncio
async def test_receipt_on_follow_up_stay_on_gap(db):
    """The else-branch follow-up: no recorded cluster, budget left, nothing written."""
    record, profile = await _seed_legacy(db)
    turn = _turn(profile.profile_json)

    resp = await _send(db, record.id, _POSITIVE, _bridge(turn),
                       AsyncMock(return_value={"question": "A concrete GCP example?", "choices": None}))

    assert resp.complete is False
    assert resp.question == "A concrete GCP example?"
    assert resp.current_gap_id == "GCP certification", "stayed on the gap"
    assert resp.addressed_gap_ids == []
    assert resp.no_write_receipt == _RECEIPT


@pytest.mark.asyncio
async def test_receipt_on_hard_ceiling_completion(db):
    """`_complete_session("max_questions_reached")` — the ceiling-tripping turn."""
    record, profile = await _seed_legacy(
        db, state={"hard_ceiling": 2, "questions_asked": 1}, hard_ceiling=2, questions_asked=1
    )
    turn = _turn(profile.profile_json)

    resp = await _send(db, record.id, _POSITIVE, _bridge(turn))

    assert resp.complete is True
    assert resp.reason == "max_questions_reached"
    assert resp.no_write_receipt == _RECEIPT


@pytest.mark.asyncio
async def test_receipt_on_gaps_resolved_completion(db):
    """`_complete_session("gaps_resolved")` — the last gap's per-gap budget is
    spent by a no-write turn, so it advances off the end and completes."""
    record, profile = await _seed_legacy(
        db,
        state={
            "critical_gaps": ["GCP certification"],
            "gap_categories": {"GCP certification": "C"},
            "questions_per_gap": {"GCP certification": 2},
        },
    )
    turn = _turn(profile.profile_json)

    resp = await _send(db, record.id, _POSITIVE, _bridge(turn))

    assert resp.complete is True
    assert resp.reason == "gaps_resolved"
    assert resp.no_write_receipt == _RECEIPT


# -- the remaining sites where a receipt can ride (completes the 8-site map) --


@pytest.mark.asyncio
async def test_receipt_on_partial_coverage_follow_up(db):
    """`_ask_partial_coverage_follow_up` — the vault changed, a member is open.

    R2-6 (4) (Hazelnut build 4): the receipt decides by ``changes`` first. This
    branch runs only on a turn that WROTE (``addressed``), so it never carries a
    "nothing was recorded" receipt — even when the bridge reports one."""
    job, _profile = await _seed_clusters(db)
    writer = _question_writer("Tell me about Kubernetes and Terraform.", "And Terraform?")
    created = await _gap_click(db, job, _INFRA, writer)

    resp = await _send(db, created.session_id, "Kubernetes daily at Acme.",
                       _writing_bridge(add_skills=["Kubernetes"], receipt=_RECEIPT), writer)

    assert resp.complete is False
    assert resp.question == "And Terraform?"
    assert resp.cluster_coverage.coverage == "partly_covered"
    assert resp.no_write_receipt is None


@pytest.mark.asyncio
@patch("applire.services.session._DENIAL_TRANSFER_PROBE_ENABLED", True)  # I-4: the retired probe, dormant path
async def test_receipt_on_denial_probe(db):
    """`_ask_denial_probe` — a denial-only turn on a JD-required concept."""
    job, _profile = await _seed_clusters(db)
    writer = _question_writer("Tell me about Kubernetes and Terraform.", "Any orchestration?")
    created = await _gap_click(db, job, _INFRA, writer)

    with patch(
        "applire.services.session._mark_probe_asked",
        new=AsyncMock(side_effect=lambda db, rec, concept, sid: rec.profile_json),
    ):
        resp = await _send(db, created.session_id, _DENIAL,
                           _writing_bridge(deny=["Kubernetes"], receipt=_RECEIPT), writer)

    assert resp.complete is False
    assert writer.await_args.kwargs.get("denial_probe") is True, "the probe branch, not a retry"
    assert resp.no_write_receipt == _RECEIPT


@pytest.mark.asyncio
async def test_receipt_on_confirmation_question(db):
    """`_ask_confirmation` — the reconciler owes the candidate a confirmation."""
    from applire.services.profile.reconcile.ops import RequestConfirmation

    record, profile = await _seed_legacy(db)
    turn = _turn(profile.profile_json)
    turn.pending_confirmations = [RequestConfirmation(
        question="Is 'Owner at applire' the same as your 'Founder' role?",
        options=["Yes, same role", "No, separate roles"],
        context={"existing": "Founder", "incoming": "Owner"},
    )]

    resp = await _send(db, record.id, _POSITIVE, _bridge(turn))

    assert resp.complete is False
    assert resp.pending_confirmations, "the confirmation branch"
    assert resp.no_write_receipt == _RECEIPT


# -- None stays None --


@pytest.mark.asyncio
async def test_no_receipt_when_turn_has_none(db):
    record, profile = await _seed_legacy(db)
    turn = _turn(profile.profile_json, receipt=None)
    assert turn.no_write_receipt is None

    resp = await _send(db, record.id, _POSITIVE, _bridge(turn))

    assert resp.complete is False
    assert resp.no_write_receipt is None


def test_turn_result_defaults_to_no_receipt():
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    assert InterviewTurnResult(profile_dict={}, changes=[], addressed=False).no_write_receipt is None


@pytest.mark.asyncio
async def test_termination_signal_has_no_receipt(db):
    """'done' ends the session before any reconcile — no reconciled turn, no receipt."""
    record, _profile = await _seed_legacy(db)
    bridge = AsyncMock(side_effect=AssertionError("a termination signal must not reconcile"))

    resp = await _send(db, record.id, "done", bridge)

    assert resp.complete is True
    bridge.assert_not_awaited()
    assert resp.no_write_receipt is None


# ---------------------------------------------------------------------------
# 4. The confirmation-answer door (adversarial pass 2026-10-17)
# ---------------------------------------------------------------------------
#
# `_handle_interview_confirmation_answer` resolves a parked confirmation
# deterministically and never re-runs the reconciler. A free-text answer that
# picked no option and wrote nothing must get the same receipt predicate.

_KEYLESS_CONF = {
    "confirmation_id": None,
    "question": "Do you mean your role at Cargonaut Logistics?",
    "options": ["Yes, it is that same role", "No, a different one"],
    "context": {"section": "work_experience"},  # no `incoming`: nothing to apply
}


async def _seed_confirmation(db, conf=None):
    return await _seed_legacy(db, state={
        "pending_interview_confirmation": copy.deepcopy(conf or _KEYLESS_CONF),
        "resolving_confirmation": True,
    })


@pytest.mark.asyncio
async def test_confirmation_door_free_text_that_wrote_nothing_carries_no_write(db):
    record, _profile = await _seed_confirmation(db)
    bridge = AsyncMock(side_effect=AssertionError("the confirmation door never reconciles"))

    resp = await _send(
        db, record.id,
        "At Cargonaut I built the PSD2 integration with Adyen in Python, four services.",
        bridge,
    )

    bridge.assert_not_awaited()
    assert resp.no_write_receipt == "no_write"


@pytest.mark.asyncio
async def test_confirmation_door_choosing_an_option_carries_no_receipt(db):
    """Picking one of the offered options is a decision, not a lost statement."""
    record, _profile = await _seed_confirmation(db)

    # Four words, no negation: positive residue — only the option check keeps
    # the receipt off a choice the candidate made.
    resp = await _send(db, record.id, "Yes, it is that same role", AsyncMock())

    assert resp.no_write_receipt is None


@pytest.mark.asyncio
async def test_confirmation_door_denial_only_answer_carries_no_receipt(db):
    record, _profile = await _seed_confirmation(db)

    resp = await _send(db, record.id, "I have never worked with Adyen at all.", AsyncMock())

    assert resp.no_write_receipt is None


@pytest.mark.asyncio
async def test_confirmation_door_answer_that_wrote_carries_no_receipt(db):
    """A skill confirmation resolved to `distinct` writes the skill — no receipt."""
    conf = {
        "confirmation_id": None,
        "question": "Add 'Adyen' as its own skill?",
        "options": ["Add it as a separate skill", "Keep the existing skills"],
        "option_keys": ["distinct", "keep"],
        "context": {"incoming_skill": "Adyen", "category": "technical"},
    }
    record, _profile = await _seed_confirmation(db, conf)

    resp = await _send(db, record.id, "Add it as a separate skill", AsyncMock())

    assert resp.no_write_receipt is None


def test_confirmation_receipt_is_off_when_the_answer_wrote():
    """The predicate's first half: something was written → no receipt, however
    much the answer states."""
    from applire.services.session import _confirmation_no_write_receipt

    statement = "At Cargonaut I built the PSD2 integration with Adyen in Python, four services."
    assert _confirmation_no_write_receipt(False, statement, ["Yes", "No"]) == "no_write"
    assert _confirmation_no_write_receipt(True, statement, ["Yes", "No"]) is None
