# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 WP-A1 — UAT-1 (ADR-099 cl. 11) / U-2 / "Jetzt beantworten".

T-A: every outcome of a dispute, through the profile-review session AND the
REST door, asserted on the persisted vault. T-bound: a typed answer is read
once; after ONE follow-up only a structured outcome answers. Synthetic data.
"""
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import MasterProfileData  # noqa: E402

from tests.support.profile_factory import make_master_profile  # noqa: E402

A_EXISTING = "Introduced a supplier scorecard for vendor selection."
A_INCOMING = "Merged 12 reporting tools into one central lakehouse."


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


class Stub:
    """A provider whose every JSON answer is ``reply``; records each call."""

    def __init__(self, reply=None):
        self.reply = reply if reply is not None else {}
        self.calls: list[str] = []

    async def aparse_json(self, prompt, *, system=None, **kw):
        self.calls.append(system or "")
        return self.reply

    async def acomplete(self, *a, **k):  # never used on these paths
        raise AssertionError("acomplete must not be called")


def _conflict(cid, field, existing, incoming, section="work_experience", entity="w1"):
    return {
        "conflict_id": cid, "section": section, "field": field, "entity_id": entity,
        "existing_value": existing, "incoming_value": incoming,
        "source": "cv_upload", "resolved": False,
    }


def _profile(*conflicts):
    return {
        "personal_info": {"name": "Erika Probe", "email": "erika@example.de", "location": "Köln"},
        "work_experience": [{
            "id": "w1", "company": "Lumen Logistik", "role": "Head of Data",
            "start_date": "2019-01", "end_date": "2023-06",
            "responsibilities": ["Ran the data team"],
            "achievements": [A_EXISTING],
        }],
        "metadata": {"pending_conflicts": list(conflicts), "enrichment_history": []},
    }


async def _seed(db, *conflicts):
    rec = make_master_profile(profile_json=_profile(*conflicts))
    db.add(rec)
    await db.commit()
    return rec


async def _vault(db, rec) -> MasterProfileData:
    await db.refresh(rec)
    from applire.services.profile import get_profile_for_user

    live = await get_profile_for_user(db)
    return MasterProfileData.model_validate(live.profile_json)


def _ach(p: MasterProfileData) -> list[str]:
    return list(p.work_experience[0].achievements)


async def _review(db, provider, **kw):
    from applire.services.session import create_profile_review_session

    return await create_profile_review_session(db, provider, **kw)


# ── T-A: each outcome, structured, through the session ────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome, expected",
    [
        ("keep_current", [A_EXISTING]),
        ("take_new", [A_INCOMING]),
        # SF-PROFILE.22 — "Beide behalten" keeps BOTH (founder UAT: kept one).
        ("keep_both", [A_EXISTING, A_INCOMING]),
    ],
)
async def test_list_outcome_through_the_session_is_written(db, outcome, expected):
    from applire.services.session import send_message

    rec = await _seed(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    created = await _review(db, Stub())
    assert created.current_conflict.outcomes == ["keep_current", "take_new", "keep_both", "merge"]
    assert created.current_conflict.shape == "list"
    resp = await send_message(created.session_id, "", db, Stub(), outcome=outcome)
    assert resp.interpreted.outcome == outcome
    p = await _vault(db, rec)
    assert _ach(p) == expected
    assert not p.metadata.pending_conflicts


@pytest.mark.asyncio
async def test_merge_confirm_passing_writes_the_sentence_and_one_undo_restores_both(db):
    from applire.services.profile.snapshots import undo_last_merge
    from applire.services.session import send_message

    rec = await _seed(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    created = await _review(db, Stub())
    merged = ("Introduced a supplier scorecard for vendor selection and merged 12 "
              "reporting tools into one central lakehouse.")
    resp = await send_message(created.session_id, "", db, Stub(), outcome="merge_confirm",
                              merged_text=merged)
    assert (resp.interpreted.outcome, resp.interpreted.verifier) == ("merge", "passed")
    p = await _vault(db, rec)
    assert _ach(p) == [merged]
    change = p.metadata.enrichment_history[-1].changes[-1]
    assert change.old_value == [A_EXISTING, A_INCOMING]  # both originals on the receipt

    undone = await undo_last_merge(db)
    assert undone.restored is True
    p = await _vault(db, rec)
    assert _ach(p) == [A_EXISTING]  # pre-resolution state, the dispute open again
    assert [c.conflict_id for c in p.metadata.pending_conflicts] == ["c1"]


@pytest.mark.asyncio
async def test_merge_confirm_failing_the_verifier_is_written_as_manual(db):
    """RULING A1-1 — the candidate's edited sentence carries a figure neither
    bullet states: written as the manual resolution, reported as such."""
    from applire.services.session import send_message

    rec = await _seed(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    created = await _review(db, Stub())
    edited = "Merged 15 reporting tools into one central lakehouse."
    resp = await send_message(created.session_id, "", db, Stub(), outcome="merge_confirm",
                              merged_text=edited)
    assert (resp.interpreted.outcome, resp.interpreted.verifier) == ("manual", "failed")
    assert _ach(await _vault(db, rec)) == [edited]


@pytest.mark.asyncio
async def test_keep_both_on_an_entry_identity_field_creates_the_second_entry(db):
    """RULING A1-i1 — two roles, not one disputed title."""
    from applire.services.profile.entry_identity import open_pairs
    from applire.services.session import send_message

    rec = await _seed(db, _conflict("c1", "role", "Head of Data", "Data Engineer"))
    created = await _review(db, Stub())
    assert created.current_conflict.outcomes == ["keep_current", "take_new", "keep_both"]
    await send_message(created.session_id, "", db, Stub(), outcome="keep_both")
    p = await _vault(db, rec)
    roles = [(e.role, e.company, e.start_date, list(e.achievements)) for e in p.work_experience]
    assert roles == [
        ("Head of Data", "Lumen Logistik", "2019-01", [A_EXISTING]),
        ("Data Engineer", "Lumen Logistik", "2019-01", []),
    ]
    # recorded as kept-separate → the duplicate-entry question never asks it
    assert len(p.metadata.separate_entry_pairs) == 1
    assert open_pairs(p) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["keep_both", "merge_confirm"])
async def test_an_outcome_the_dispute_does_not_offer_is_refused(db, outcome):
    from applire.services.session import OutcomeNotOffered, send_message

    rec = await _seed(db, _conflict("c1", "location", "Köln", "Bonn",
                                    section="personal_info", entity=None))
    created = await _review(db, Stub())
    assert created.current_conflict.outcomes == ["keep_current", "take_new"]
    with pytest.raises(OutcomeNotOffered):
        await send_message(created.session_id, "", db, Stub(), outcome=outcome, merged_text="Köln")
    p = await _vault(db, rec)
    assert p.personal_info.location == "Köln" and len(p.metadata.pending_conflicts) == 1


# ── the same outcomes through the REST door (door parity) ─────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolution, value, expected",
    [
        ("keep_both", None, [A_EXISTING, A_INCOMING]),
        ("merge", "Introduced a supplier scorecard and merged 12 reporting tools.",
         ["Introduced a supplier scorecard and merged 12 reporting tools."]),
        ("merge", "Merged 15 reporting tools.", ["Merged 15 reporting tools."]),  # → manual
    ],
)
async def test_rest_resolve_door_writes_the_same_outcomes(db, resolution, value, expected):
    from applire.services.profile import last_applied_resolution, resolve_conflict

    rec = await _seed(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    await resolve_conflict("c1", resolution, value, db)
    assert _ach(await _vault(db, rec)) == expected
    if value == "Merged 15 reporting tools.":
        assert last_applied_resolution() == "manual"


@pytest.mark.asyncio
async def test_rest_door_refuses_keep_both_on_a_plain_scalar(db):
    from applire.services.profile import resolve_conflict

    await _seed(db, _conflict("c1", "location", "Köln", "Bonn", section="personal_info", entity=None))
    with pytest.raises(ValueError):
        await resolve_conflict("c1", "keep_both", None, db)


# ── T-bound: a typed answer is read once ──────────────────────────────────────


@pytest.mark.asyncio
async def test_typed_beide_behalten_is_never_read_by_the_keep_word_matcher(db):
    """SF-PROFILE.22 mutation shape: the legacy word sets hear "behalten" and
    keep ONE. "beide" sends the answer to the model, whose keep_both is taken."""
    from applire.services.session import send_message

    rec = await _seed(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    created = await _review(db, Stub())
    model = Stub({"outcome": "keep_both", "corrected_value": None, "follow_up": None})
    resp = await send_message(created.session_id, "Beide behalten bitte", db, model)
    assert len(model.calls) == 1
    assert resp.interpreted.outcome == "keep_both"
    assert _ach(await _vault(db, rec)) == [A_EXISTING, A_INCOMING]


@pytest.mark.asyncio
async def test_a_corrected_value_from_the_reply_is_written_as_manual(db):
    from applire.services.session import send_message

    rec = await _seed(db, _conflict("c1", "end_date", "2023-06", "2023-09"))
    created = await _review(db, Stub())
    model = Stub({"outcome": None, "corrected_value": "2023-07", "follow_up": None})
    resp = await send_message(created.session_id, "Weder noch, es war 2023-07", db, model)
    assert (resp.interpreted.outcome, resp.interpreted.corrected_value) == ("manual", "2023-07")
    assert (await _vault(db, rec)).work_experience[0].end_date == "2023-07"


@pytest.mark.asyncio
async def test_a_value_the_model_composed_is_never_written_and_one_follow_up_then_buttons(db):
    from applire.services.session import OutcomeRequired, send_message

    rec = await _seed(db, _conflict("c1", "end_date", "2023-06", "2023-09"))
    created = await _review(db, Stub())
    # the model "corrects" to a value the candidate never wrote
    model = Stub({"outcome": None, "corrected_value": "2023-08", "follow_up": None})
    first = await send_message(created.session_id, "irgendwann im Sommer", db, model)
    assert first.interpreted.follow_up and first.interpreted.outcome is None
    assert first.follow_up_used is True
    assert first.current_conflict.conflict_id == "c1"
    assert "2023-06" in first.question  # the dispute's own question stays under it
    with pytest.raises(OutcomeRequired):
        await send_message(created.session_id, "na im Sommer halt", db, model)
    assert len(model.calls) == 1  # never a second model read
    p = await _vault(db, rec)
    assert p.work_experience[0].end_date == "2023-06" and len(p.metadata.pending_conflicts) == 1
    # the button still answers
    done = await send_message(created.session_id, "", db, Stub(), outcome="take_new")
    assert done.interpreted.outcome == "take_new"


# ── U-2: the clicked item first ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_id_is_asked_first_and_replaces_an_in_flight_review(db):
    await _seed(
        db,
        _conflict("c1", "location", "Köln", "Bonn", section="personal_info", entity=None),
        _conflict("c2", "achievements", A_EXISTING, A_INCOMING),
    )
    first = await _review(db, Stub())
    assert first.current_gap_id == "conflict:c1"
    clicked = await _review(db, Stub(), issue_id="conflict:c2")
    assert clicked.current_gap_id == "conflict:c2"
    assert clicked.session_id != first.session_id
    assert clicked.gaps_total == 2  # the other item still follows
    again = await _review(db, Stub(), issue_id="conflict:c2")
    assert again.session_id == clicked.session_id  # same item → resume


@pytest.mark.asyncio
async def test_unknown_issue_id_is_not_found(db):
    await _seed(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    with pytest.raises(LookupError):
        await _review(db, Stub(), issue_id="conflict:nope")


# ── merge proposal door ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_merge_proposal_is_verified_and_writes_nothing(db):
    from applire.services.session import review_merge_proposal

    rec = await _seed(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    created = await _review(db, Stub())
    bad = Stub({"merged": "Merged 15 reporting tools and introduced a scorecard."})
    p = await review_merge_proposal(created.session_id, "c1", db, bad)
    assert (p.proposal, p.verifier) == (None, "failed")
    good_text = "Introduced a supplier scorecard and merged 12 reporting tools."
    p = await review_merge_proposal(created.session_id, "c1", db, Stub({"merged": good_text}))
    assert (p.proposal, p.verifier) == (good_text, "passed")
    assert _ach(await _vault(db, rec)) == [A_EXISTING]
    with pytest.raises(ValueError):  # 422 — a 404 here means "not your session"
        await review_merge_proposal(created.session_id, "other", db, Stub())


# ── "Jetzt beantworten": requirement_concepts ─────────────────────────────────


def _row(concept):
    return {"concept": concept, "surface_forms": [concept], "sources": ["required"],
            "fit_weight": 1.0, "status": "gap", "evidence": "", "claimable": False}


async def _seed_job_with_ledger(db, rec):
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis

    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=uuid.uuid4().hex, raw_text="Data lead", role_title="Data Lead",
        required_skills=["Kubernetes", "Snowflake"], nice_to_have_skills=[], keywords=[],
        seniority_level="senior", company_culture_signals=[], language_requirement="en",
    )
    db.add(job)
    await db.flush()
    row = GapAnalysis(
        id=uuid.uuid4(), job_analysis_id=job.id, profile_id=rec.id, user_id=rec.user_id,
        match_score=0.5, critical_gaps=[], minor_gaps=[], strengths=[], keyword_gaps=[],
        category_a=[], category_b=[], category_c=[], requirement_breakdown=[],
        gap_clusters=[], keyword_ledger=[_row("Kubernetes"), _row("Snowflake")],
    )
    db.add(row)
    await db.commit()
    return job, row


@pytest.mark.asyncio
async def test_requirement_concepts_unknown_is_refused(db):
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import UnknownRequirement, create_session

    rec = await _seed(db)
    job, _row_ = await _seed_job_with_ledger(db, rec)
    with pytest.raises(UnknownRequirement) as err:
        await create_session(
            SessionCreateRequest(job_id=job.id, requirement_concepts=["Snowflake", "Cobol"]),
            db, Stub(),
        )
    assert err.value.concepts == ["Cobol"]


@pytest.mark.asyncio
async def test_requirement_concepts_asks_exactly_those_in_order(db, monkeypatch):
    from applire.schemas.session import SessionCreateRequest
    from applire.services import session as session_svc

    asked: list[str] = []

    async def fake_question(state, profile_json, provider, db_, **kw):
        gap = state["critical_gaps"][state["current_gap_index"]]
        asked.append(gap)
        return {"question": f"Q about {gap}", "choices": None}

    monkeypatch.setattr(session_svc, "_cluster_question", fake_question)
    rec = await _seed(db)
    job, _row_ = await _seed_job_with_ledger(db, rec)
    resp = await session_svc.create_session(
        SessionCreateRequest(job_id=job.id, requirement_concepts=["snowflake", "Kubernetes"]),
        db, Stub(),
    )
    assert resp.gaps_total == 2 and resp.current_gap_id == "requirement:Snowflake"
    assert asked == ["requirement:Snowflake"]
    from applire.models.session import InterviewSession

    record = await db.get(InterviewSession, resp.session_id)
    assert record.state["critical_gaps"] == ["requirement:Snowflake", "requirement:Kubernetes"]
    assert record.state["gap_clusters_by_id"]["requirement:Kubernetes"]["gaps"] == ["Kubernetes"]


# ── SEC-1: the requirement list is bounded ────────────────────────────────────


def test_requirement_concepts_is_bounded_in_the_schema():
    from pydantic import ValidationError

    from applire.schemas.session import SessionCreateRequest

    job = uuid.uuid4()
    SessionCreateRequest(job_id=job, requirement_concepts=["x" * 200] * 50)
    for bad in (["x"] * 51, ["x" * 201], [""]):
        with pytest.raises(ValidationError):
            SessionCreateRequest(job_id=job, requirement_concepts=bad)


def test_unknown_requirement_echo_is_capped():
    from applire.services.session import UnknownRequirement

    err = UnknownRequirement([f"c{i}" * 300 for i in range(40)])
    assert len(err.concepts) == UnknownRequirement.ECHO_CAP
    assert all(len(c) <= 200 for c in err.concepts)


@pytest.mark.asyncio
async def test_duplicate_requirement_concepts_are_asked_once(db, monkeypatch):
    from applire.schemas.session import SessionCreateRequest
    from applire.services import session as session_svc

    async def fake_question(state, profile_json, provider, db_, **kw):
        return {"question": "Q", "choices": None}

    monkeypatch.setattr(session_svc, "_cluster_question", fake_question)
    rec = await _seed(db)
    job, _row_ = await _seed_job_with_ledger(db, rec)
    resp = await session_svc.create_session(
        SessionCreateRequest(job_id=job.id, requirement_concepts=["Snowflake", " snowflake ", "SNOWFLAKE"]),
        db, Stub(),
    )
    assert resp.gaps_total == 1


# ── ADV-8: Undo names the merge it means; an already-gone dispute writes nothing ─


async def _merge_in_drawer(db, *conflicts):
    from applire.services.session import send_message

    rec = await _seed(db, *conflicts)
    created = await _review(db, Stub())
    merged = ("Introduced a supplier scorecard for vendor selection and merged 12 "
              "reporting tools into one central lakehouse.")
    resp = await send_message(created.session_id, "", db, Stub(), outcome="merge_confirm",
                              merged_text=merged)
    assert resp.interpreted.outcome == "merge"
    return rec, created, resp


def _basis_from_the_wire(resp):
    """The basis exactly as a browser sends it back: the JSON-serialised value,
    parsed by the request model."""
    from applire.schemas.profile import UndoLastMergeRequest

    wire = resp.model_dump(mode="json")["profile_updated_at"]
    assert isinstance(wire, str)
    return UndoLastMergeRequest.model_validate({"expected_updated_at": wire}).expected_updated_at


@pytest.mark.asyncio
async def test_undo_with_the_merge_basis_restores(db):
    from applire.services.profile.snapshots import undo_last_merge

    rec, _c, resp = await _merge_in_drawer(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    undone = await undo_last_merge(db, expected_updated_at=_basis_from_the_wire(resp))
    assert undone.restored is True and undone.discarded_later_edits is False
    assert _ach(await _vault(db, rec)) == [A_EXISTING]


@pytest.mark.asyncio
async def test_the_basis_compares_instants_not_representations(db):
    """SQLite hands back a naive datetime for a timezone=True column; the same
    instant written with another offset is the same basis."""
    from datetime import timedelta, timezone

    from applire.services.profile.snapshots import undo_last_merge

    _rec, _c, resp = await _merge_in_drawer(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    basis = _basis_from_the_wire(resp)
    if basis.tzinfo is None:  # SQLite: the wire carries the naive UTC value
        basis = basis.replace(tzinfo=timezone.utc)
    shifted = basis.astimezone(timezone(timedelta(hours=2)))
    assert shifted.utcoffset() == timedelta(hours=2)
    assert (await undo_last_merge(db, expected_updated_at=shifted)).restored is True


@pytest.mark.asyncio
async def test_undo_after_a_later_edit_is_refused_with_a_basis_and_restores_nothing(db):
    from applire.services.profile import resolve_conflict
    from applire.services.profile.snapshots import UndoStale, undo_last_merge

    rec, _c, resp = await _merge_in_drawer(
        db,
        _conflict("c1", "achievements", A_EXISTING, A_INCOMING),
        _conflict("c2", "location", "Köln", "Bonn"),
    )
    await resolve_conflict("c2", "incoming", None, db)  # a later write
    before = (await _vault(db, rec)).model_dump(mode="json")
    with pytest.raises(UndoStale):
        await undo_last_merge(db, expected_updated_at=_basis_from_the_wire(resp))
    assert (await _vault(db, rec)).model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_a_basis_that_is_not_the_profiles_last_write_is_refused(db):
    from datetime import timedelta

    from applire.services.profile.snapshots import UndoStale, undo_last_merge

    _rec, _c, resp = await _merge_in_drawer(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    with pytest.raises(UndoStale):
        await undo_last_merge(db, expected_updated_at=_basis_from_the_wire(resp) - timedelta(seconds=1))


@pytest.mark.asyncio
async def test_without_a_basis_undo_keeps_todays_behaviour(db):
    from applire.services.profile import resolve_conflict
    from applire.services.profile.snapshots import undo_last_merge

    _rec, _c, _resp = await _merge_in_drawer(
        db,
        _conflict("c1", "achievements", A_EXISTING, A_INCOMING),
        _conflict("c2", "location", "Köln", "Bonn"),
    )
    await resolve_conflict("c2", "incoming", None, db)
    undone = await undo_last_merge(db)
    assert undone.restored is True and undone.discarded_later_edits is True


@pytest.mark.asyncio
async def test_undo_router_answers_409_undo_stale(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers import profile as profile_router
    from applire.services.profile import snapshots

    seen = {}

    async def fake_undo(db, *, user_id=None, expected_updated_at=None):
        seen["basis"] = expected_updated_at
        if expected_updated_at is not None:
            raise snapshots.UndoStale()
        return snapshots.UndoResult(restored=True, discarded_later_edits=False)

    monkeypatch.setattr(profile_router, "undo_last_merge", fake_undo)
    app = FastAPI()
    app.include_router(profile_router.router)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/profile/undo-last-merge",
                          json={"expected_updated_at": "2026-10-28T12:00:00.123456+00:00"})
        assert (r.status_code, r.json()["detail"]) == (409, "undo_stale")
        assert seen["basis"].microsecond == 123456
        r = await ac.post("/api/profile/undo-last-merge")  # old clients: no body
        assert r.status_code == 200 and r.json()["restored"] is True
        assert seen["basis"] is None


@pytest.mark.asyncio
async def test_an_already_gone_dispute_is_reported_as_already_answered(db):
    """ADV-8 (2): resolved elsewhere between question and answer — nothing is
    written, so no outcome, no verifier, and no Undo basis."""
    from applire.services.profile import resolve_conflict
    from applire.services.session import send_message

    rec = await _seed(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    created = await _review(db, Stub())
    await resolve_conflict("c1", "existing", None, db)  # another tab
    head = len((await _vault(db, rec)).metadata.enrichment_history)
    resp = await send_message(created.session_id, "", db, Stub(), outcome="merge_confirm",
                              merged_text="Introduced a supplier scorecard for vendor selection.")
    assert resp.interpreted.already_answered is True
    assert (resp.interpreted.outcome, resp.interpreted.verifier) == (None, None)
    assert resp.profile_updated_at is None
    assert len((await _vault(db, rec)).metadata.enrichment_history) == head


@pytest.mark.asyncio
async def test_a_written_answer_carries_the_undo_basis(db):
    _rec, _c, resp = await _merge_in_drawer(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    assert resp.profile_updated_at is not None and resp.interpreted.already_answered is False


@pytest.mark.asyncio
async def test_a_basis_is_refused_when_the_newest_snapshot_is_not_that_merge(db):
    """Defence in depth: the time matches, but the newest snapshot belongs to
    another write (a snapshot chain moved without the profile row)."""
    from datetime import datetime, timedelta, timezone

    from applire.models.profile import ProfileSnapshot
    from applire.services.profile.snapshots import UndoStale, undo_last_merge

    rec, _c, resp = await _merge_in_drawer(db, _conflict("c1", "achievements", A_EXISTING, A_INCOMING))
    db.add(ProfileSnapshot(profile_id=rec.id, profile_json={}, enrichment_record_id="another-write",
                           created_at=datetime.now(timezone.utc) + timedelta(minutes=1)))
    await db.commit()
    with pytest.raises(UndoStale):
        await undo_last_merge(db, expected_updated_at=_basis_from_the_wire(resp))
