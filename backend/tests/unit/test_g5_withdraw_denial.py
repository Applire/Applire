# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""G-5 (Hazelnut build 4; ADR-059 amended 2026-10-24) — the explicit un-denial
act. The candidate withdraws a denial; nothing else does.

Pinned here:
* the record moves VERBATIM to ``metadata.withdrawn_denials`` (mark, don't
  erase) and the trail gains one ``denial_withdrawn`` receipt, kept off
  ``changes``;
* a skill the denial retracted returns to ``unconfirmed`` (revoking is not
  claiming — ADR-059 cl. 2, ruling V-5b = A) and SURVIVES the commit's
  persisted-``denied`` floor — unless another active denial still declares it;
  the next ordinary write that names it re-confirms it;
* a miss writes nothing; the un-denial intake carries ``WithdrawDenial`` only,
  and ``WithdrawDenial`` travels through no other intake;
* re-denying afterwards writes a fresh record;
* the ``cv_holds`` answer to a parked ``denial_scope`` item IS the act;
  ``denial_holds`` writes nothing;
* the model can never emit the op.
"""

import copy
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, ProfileSnapshot
from applire.services.profile.commit import (
    UN_DENIAL_INTAKE,
    CommitProvenance,
    TurnGrounding,
    commit_ops,
)
from applire.services.profile.reconcile.ops import (
    ResolveConfirmation,
    UpsertSkill,
    WithdrawDenial,
)

_DENIAL = {
    "concept": "Ansible",
    "statement": "I have never used Ansible.",
    "source": "interview",
    "date": "2026-08-01",
    "denial_level": "direct",
    "probe_asked": True,
}

_SEED = {
    "personal_info": {"full_name": "Daniel Kovač", "email": "daniel@example.invalid"},
    "work_experience": [
        {"id": "w1", "company": "Rheinwerk GmbH", "role": "Automation Engineer",
         "start_date": "2018-01", "end_date": "2023-12"}
    ],
    "skills": [
        {"id": "s-ans", "name": "Ansible", "category": "technical", "status": "denied"},
        {"id": "s-tf", "name": "Terraform", "category": "technical", "status": "confirmed"},
    ],
    "metadata": {
        "completeness_score": 0.0,
        "created_via": "cv_upload",
        "created_at": "2020-01-01T00:00:00Z",
        "last_updated": "2020-01-01T00:00:00Z",
        "denied_concepts": [dict(_DENIAL)],
    },
}

_CONF_ID = "conf-denial-1"
_PARKED = {
    "confirmation_id": _CONF_ID,
    "question": "By your own answer, “Ansible” does not apply to you. Your CV says: “Ansible”.",
    "options": ["Yes, my answer is right", "No, the CV is right: withdraw my answer"],
    "context": {"kind": "denial_scope", "denial_concept": "Ansible", "cv_label": "Ansible"},
    "source": "denial_scope",
    "question_i18n": {"de": "Stimmt deine frühere Angabe noch?", "en": "Is your earlier answer still right?"},
    "options_i18n": [
        {"de": "Ja, meine Angabe stimmt", "en": "Yes, my answer is right"},
        {"de": "Nein, der Lebenslauf stimmt: Angabe zurücknehmen",
         "en": "No, the CV is right: withdraw my answer"},
    ],
    "option_keys": ["denial_holds", "cv_holds"],
}


def _prov(intake: str = UN_DENIAL_INTAKE) -> CommitProvenance:
    return CommitProvenance(source="manual_edit", intake=intake, session_id=str(uuid.uuid4()))


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db_session, seed=None):
    from applire.models.profile import authorized_profile_write

    with authorized_profile_write():
        record = MasterProfile(profile_json=copy.deepcopy(seed or _SEED))
    db_session.add(record)
    await db_session.commit()
    return record


def _skill(record, name):
    return next(s for s in record.profile_json["skills"] if s["name"] == name)


@pytest.mark.asyncio
async def test_withdraw_moves_the_record_verbatim_and_receipts_it(db_session):
    record = await _seed(db_session)
    result = await commit_ops(db_session, [WithdrawDenial(concept="ansible")], _prov(), record=record)

    meta = record.profile_json["metadata"]
    assert meta["denied_concepts"] == []
    [moved] = meta["withdrawn_denials"]
    assert {k: moved[k] for k in _DENIAL} == _DENIAL  # the candidate's words, untouched
    assert moved["withdrawn_at"]
    assert result.changes == []  # never gap-addressing content (invariant 7)
    trail = meta["enrichment_history"][-1]["changes"]
    removed = [c for c in trail if c["field"] == "denied_concepts"]
    assert removed and removed[0]["action"] == "removed"
    assert removed[0]["old_value"] == "Ansible"
    assert removed[0]["rationale_key"] == "denial_withdrawn"


@pytest.mark.asyncio
async def test_the_retracted_skill_counts_again_and_survives_the_floor(db_session):
    record = await _seed(db_session)
    result = await commit_ops(db_session, [WithdrawDenial(concept="Ansible")], _prov(), record=record)

    assert _skill(record, "Ansible")["status"] == "unconfirmed"  # unproven, never claimable
    assert result.refloored == []  # the persisted-status floor exempted exactly it
    assert any(
        c.rationale_key == "denial_withdrawn_skill" and c.entity_id == "s-ans"
        for c in result.denials
    )


@pytest.mark.asyncio
async def test_a_skill_another_denial_still_declares_stays_denied(db_session):
    # Two records with different normalised concepts that both DECLARE the
    # skill (the #207 alias group): withdrawing one leaves the other's floor.
    seed = copy.deepcopy(_SEED)
    seed["skills"].append({"id": "s-js", "name": "JavaScript", "category": "technical", "status": "denied"})
    seed["metadata"]["denied_concepts"] += [
        {**_DENIAL, "concept": "JS", "statement": "No JS."},
        {**_DENIAL, "concept": "Javascript", "statement": "No Javascript either."},
    ]
    record = await _seed(db_session, seed)
    result = await commit_ops(db_session, [WithdrawDenial(concept="JS")], _prov(), record=record)

    assert _skill(record, "JavaScript")["status"] == "denied"
    # Never released at all — not released and then re-floored by the commit.
    assert not [c for c in result.denials + result.refloored if c.entity_id == "s-js"]
    assert [d["concept"] for d in record.profile_json["metadata"]["withdrawn_denials"]] == ["JS"]


@pytest.mark.asyncio
async def test_other_denied_skills_stay_floored(db_session):
    seed = copy.deepcopy(_SEED)
    seed["skills"].append({"id": "s-pup", "name": "Puppet", "category": "technical", "status": "denied"})
    record = await _seed(db_session, seed)
    await commit_ops(db_session, [WithdrawDenial(concept="Ansible")], _prov(), record=record)

    assert _skill(record, "Puppet")["status"] == "denied"


@pytest.mark.asyncio
async def test_a_miss_writes_nothing_and_the_service_says_so(db_session):
    from applire.services.profile import DenialNotFound, withdraw_denial

    record = await _seed(db_session)
    before = copy.deepcopy(record.profile_json)
    with pytest.raises(DenialNotFound):
        await withdraw_denial(db_session, concept="Kubernetes")
    assert record.profile_json == before

    # The op itself is a quiet no-op on a miss — never containment, never a mint.
    await commit_ops(db_session, [WithdrawDenial(concept="Ansib")], _prov(), record=record)
    assert record.profile_json["metadata"]["denied_concepts"][0]["concept"] == "Ansible"
    assert record.profile_json["metadata"].get("withdrawn_denials", []) == []


@pytest.mark.asyncio
async def test_the_service_withdraws(db_session):
    from applire.services.profile import withdraw_denial

    await _seed(db_session)
    response = await withdraw_denial(db_session, concept="Ansible")
    assert response.profile.metadata.denied_concepts == []
    assert [d.concept for d in response.profile.metadata.withdrawn_denials] == ["Ansible"]


@pytest.mark.asyncio
async def test_the_intake_and_the_op_only_travel_together(db_session):
    record = await _seed(db_session)
    before = copy.deepcopy(record.profile_json)
    with pytest.raises(NotImplementedError):
        await commit_ops(
            db_session,
            [WithdrawDenial(concept="Ansible"), UpsertSkill(name="Ansible", category="technical", status="confirmed")],
            _prov(),
            record=record,
        )
    with pytest.raises(NotImplementedError):
        await commit_ops(db_session, [WithdrawDenial(concept="Ansible")], _prov("field_edit"), record=record)
    assert record.profile_json == before


@pytest.mark.asyncio
async def test_re_denying_after_a_withdrawal_writes_a_fresh_record(db_session):
    record = await _seed(db_session)
    await commit_ops(db_session, [WithdrawDenial(concept="Ansible")], _prov(), record=record)
    await commit_ops(
        db_session,
        [],
        CommitProvenance(source="testimony", intake="testimony", session_id=str(uuid.uuid4())),
        record=record,
        grounding=TurnGrounding(text="No, really, I never touched Ansible.", denials=["Ansible"]),
    )
    meta = record.profile_json["metadata"]
    assert [d["statement"] for d in meta["denied_concepts"]] == ["No, really, I never touched Ansible."]
    assert [d["statement"] for d in meta["withdrawn_denials"]] == ["I have never used Ansible."]
    # The demotion acts on a CONFIRMED skill only; the withdrawn one is still
    # unproven (V-5b), backs no claim, and the new denial floors the ledger.
    assert _skill(record, "Ansible")["status"] == "unconfirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, withdrawn",
    [
        ("Nein, der Lebenslauf stimmt: Angabe zurücknehmen", True),
        ("Ja, meine Angabe stimmt", False),
    ],
)
async def test_the_health_item_answer_is_the_act(db_session, answer, withdrawn):
    seed = copy.deepcopy(_SEED)
    seed["metadata"]["pending_confirmations"] = [copy.deepcopy(_PARKED)]
    record = await _seed(db_session, seed)
    await commit_ops(
        db_session,
        [ResolveConfirmation(confirmation_id=_CONF_ID, chosen_option=answer)],
        _prov("profile_review"),
        record=record,
    )
    meta = record.profile_json["metadata"]
    assert meta["pending_confirmations"] == []
    assert (meta["denied_concepts"] == []) is withdrawn
    assert (_skill(record, "Ansible")["status"] == "unconfirmed") is withdrawn


def test_the_model_can_never_emit_the_op():
    from applire.services.profile.reconcile.engine import _parse_ops

    rejected: list[str] = []
    assert _parse_ops([{"op": "withdraw_denial", "concept": "Ansible"}], rejected=rejected) == []
    assert rejected == ["withdraw_denial"]


# --- ruling V-5 (c): the Health hub answers a denial_scope ask in place ------


def test_the_health_item_carries_the_asks_own_options_for_denial_scope_only():
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.health import assess_health

    other = {**copy.deepcopy(_PARKED), "confirmation_id": "conf-other",
             "context": {"kind": "near_duplicate_role"}}
    seed = copy.deepcopy(_SEED)
    seed["metadata"]["pending_confirmations"] = [copy.deepcopy(_PARKED), other]
    issues = {i.confirmation_id: i for i in assess_health(MasterProfileData.model_validate(seed)).issues
              if i.thread == "confirmation"}

    scoped = issues[_CONF_ID]
    assert scoped.confirmation_kind == "denial_scope"
    assert scoped.options == _PARKED["options"]
    assert scoped.options_i18n == _PARKED["options_i18n"]
    # Every other family keeps "Beheben" (the interview): no options offered.
    assert issues["conf-other"].options is None and issues["conf-other"].options_i18n is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "option, withdrawn",
    [("No, the CV is right: withdraw my answer", True), ("Yes, my answer is right", False)],
)
async def test_the_health_answer_through_the_service_is_the_act(db_session, option, withdrawn):
    from applire.services.profile import resolve_confirmation

    seed = copy.deepcopy(_SEED)
    seed["metadata"]["pending_confirmations"] = [copy.deepcopy(_PARKED)]
    await _seed(db_session, seed)
    response = await resolve_confirmation(_CONF_ID, option, db_session)

    meta = response.profile.metadata
    assert meta.pending_confirmations == []
    assert (meta.denied_concepts == []) is withdrawn
    ansible = next(s for s in response.profile.skills if s.name == "Ansible")
    assert (ansible.status == "unconfirmed") is withdrawn


@pytest.mark.asyncio
async def test_an_answered_ask_is_not_found_the_second_time(db_session):
    from applire.services.profile import resolve_confirmation

    seed = copy.deepcopy(_SEED)
    seed["metadata"]["pending_confirmations"] = [copy.deepcopy(_PARKED)]
    await _seed(db_session, seed)
    await resolve_confirmation(_CONF_ID, "Yes, my answer is right", db_session)
    with pytest.raises(LookupError):
        await resolve_confirmation(_CONF_ID, "No, the CV is right: withdraw my answer", db_session)


def test_the_route_exists():
    from applire.routers.profile import router

    assert any(getattr(r, "path", "").endswith("/confirmations/resolve") and "POST" in r.methods
               for r in router.routes)


@pytest.mark.asyncio
async def test_the_next_ordinary_write_re_confirms_the_withdrawn_skill(db_session):
    record = await _seed(db_session)
    await commit_ops(db_session, [WithdrawDenial(concept="Ansible")], _prov(), record=record)
    assert _skill(record, "Ansible")["status"] == "unconfirmed"
    await commit_ops(
        db_session,
        [UpsertSkill(name="Ansible", category="technical", status="confirmed")],
        CommitProvenance(source="cv_upload", intake="cv_import", session_id=str(uuid.uuid4())),
        record=record,
    )
    assert _skill(record, "Ansible")["status"] == "confirmed"


@pytest.mark.asyncio
async def test_denial_holds_records_the_answered_question_for_lead_g(db_session, monkeypatch):
    from applire.services.profile.reconcile import stance

    calls: list = []
    monkeypatch.setattr(
        stance, "record_denial_scope_answer",
        lambda metadata, context: calls.append((metadata is not None, context)), raising=False,
    )
    seed = copy.deepcopy(_SEED)
    seed["metadata"]["pending_confirmations"] = [copy.deepcopy(_PARKED)]
    record = await _seed(db_session, seed)
    await commit_ops(
        db_session,
        [ResolveConfirmation(confirmation_id=_CONF_ID, chosen_option="Ja, meine Angabe stimmt")],
        _prov("profile_review"),
        record=record,
    )
    assert calls == [(True, _PARKED["context"])]
