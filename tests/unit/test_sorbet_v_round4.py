# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sorbet invite-gate WP-V, round 4 — adv/vault "Re-probe 2" (RULING MD-G17).

* NF-9 — a parked size dispute never outlives the size it disputes: a later
  write of the same field settles it (the stated value is now stored) or
  re-bases it under a new id (another value is stored); a dispute whose entry
  is gone is dropped; resolving a dispute whose "Bisher" is no longer stored
  writes nothing and asks again with the current values. Every creator of a
  size dispute (F-11 ``set_field``, NF-4 ``upsert_work``, ``flag_conflict``,
  V-5 merge divergence), both doors.
* NF-10 — one normalisation and one re-ask rule for the three closed
  questions (replace, size follow-up, leadership kind).
* NF-12 — ``profile_updated_at`` is reported whenever the turn moved the row.

Synthetic data only.
"""
from __future__ import annotations

import copy
import json
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

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))  # the round-3 kind-chip harness

from tests.support.mcp_door import mcp_signing_secret  # noqa: E402,F401 — autouse
from tests.support.profile_factory import make_master_profile, retire_live_profiles  # noqa: E402

W, R = "w-kt", "w-rs"


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models  # noqa: F401 — every table

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _vault(size=38, *, dispute=(38, 45), entity=W, rs_size=14):
    """Weberit stored ``size``; an open dispute ``dispute`` on ``entity``."""
    conflicts = []
    if dispute is not None:
        conflicts.append({"conflict_id": "c-1", "section": "work_experience", "field": "team_size",
                          "entity_id": entity, "existing_value": dispute[0],
                          "incoming_value": dispute[1], "source": "agent_interview"})
    return {
        "personal_info": {"name": "Tom Berger", "email": "tom@example.org"},
        "work_experience": [
            {"id": W, "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
             "start_date": "2020-01", "is_current": True, "team_size": size,
             "responsibilities": [f"Führung von {size} Mitarbeitenden in zwei Schichten."]},
            {"id": R, "company": "Rasselstein Umformtechnik GmbH", "role": "Schichtleiter",
             "start_date": "2014-01", "end_date": "2019-12", "team_size": rs_size,
             "responsibilities": ["Schichtleitung in der Blechumformung."]},
        ],
        "metadata": {"pending_conflicts": conflicts},
    }


def _model(data):
    from applire.schemas.profile import MasterProfileData

    return MasterProfileData.model_validate(copy.deepcopy(data))


def _open(profile):
    return [c for c in (profile.metadata.pending_conflicts if profile.metadata else []) if not c.resolved]


def _size(profile, eid=W):
    return next(e for e in profile.work_experience if e.id == eid).team_size


def _kind(eid, size, option="disciplinary"):
    from applire.services.profile.leadership_kind import leadership_kind_op

    return leadership_kind_op(eid, option, answer=f"Disziplinarisch – {size} Personen", team_size=size)


# ── apply level: a later write settles or re-bases the open dispute ─────────


@pytest.mark.parametrize("source", ["interview", "agent_interview"])
def test_nf9_the_stated_size_written_later_settles_the_dispute(source):
    from applire.services.profile.reconcile.apply import apply_ops

    out = apply_ops(_model(_vault()), [_kind(W, 45)], source).profile
    assert _size(out) == 45
    assert _open(out) == []


def test_nf9_another_size_written_later_rebases_the_dispute_under_a_new_id():
    from applire.services.profile.reconcile.apply import apply_ops

    out = apply_ops(_model(_vault()), [_kind(W, 50)], "interview").profile
    assert _size(out) == 50
    (c,) = _open(out)
    assert (c.existing_value, c.incoming_value) == (50, 45)
    assert c.conflict_id != "c-1", "a stale card must not resolve the re-based dispute"


def test_nf9_a_write_on_another_entry_leaves_the_dispute_alone():
    from applire.services.profile.reconcile.apply import apply_ops

    out = apply_ops(_model(_vault()), [_kind(R, 16)], "interview").profile
    (c,) = _open(out)
    assert (c.conflict_id, c.existing_value, c.incoming_value) == ("c-1", 38, 45)


def test_nf9_a_dispute_whose_entry_was_deleted_is_dropped():
    from applire.services.profile.reconcile.ops import ReplaceSection

    data = _vault()
    kept = [e for e in data["work_experience"] if e["id"] == R]
    from applire.services.profile.reconcile.apply import apply_ops

    out = apply_ops(_model(data), [ReplaceSection(section="work_experience", value=kept)], "manual_edit").profile
    assert _open(out) == []


@pytest.mark.parametrize(("rs_size", "expected"), [(45, []), (16, [(16, 45)])])
def test_nf9_a_dispute_whose_entry_was_merged_follows_the_kept_entry(rs_size, expected):
    """The merge re-points the dispute to the kept entry; that entry's size
    decides: the stated size stored → settled, another → re-based."""
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import MergeEntries

    out = apply_ops(_model(_vault(rs_size=rs_size)),
                    [MergeEntries(section="work_experience", target_id=R, source_id=W)], "manual_edit").profile
    assert [(c.existing_value, c.incoming_value) for c in _open(out)] == expected


@pytest.mark.parametrize("make", ["set_field", "upsert_work", "flag_conflict"])
def test_nf9_every_creator_of_a_size_dispute_is_settled_by_the_later_answer(make):
    """F-11 ``set_field``, NF-4 ``upsert_work``, ``flag_conflict``: parked in one
    turn, the person's later kind answer with that size settles it."""
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import FlagConflict, SetField, UpsertWork

    op = {
        "set_field": SetField(target=W, field="team_size", value="45"),
        "upsert_work": UpsertWork(ref="w1", target=W, company="Weberit Kunststofftechnik GmbH",
                                  role="Produktionsleiter", team_size=45),
        "flag_conflict": FlagConflict(target=W, field="team_size", existing="38", incoming="45"),
    }[make]
    first = apply_ops(_model(_vault(dispute=None)), [op], "agent_interview")
    parked = first.profile.model_copy(deep=True)
    parked.metadata.pending_conflicts.extend(first.conflicts)
    assert [(c.existing_value, c.incoming_value) for c in _open(parked)] in ([(38, 45)], [(38, "45")])
    out = apply_ops(parked, [_kind(W, 45)], "interview").profile
    assert _size(out) == 45 and _open(out) == []


def test_nf9_a_merge_divergence_dispute_is_settled_the_same_way():
    """V-5 — a confirmed merge's differing size parks with the stored size as
    "Bisher"; a later answer of the merged-in size settles it."""
    from applire.services.profile.reconcile.apply import apply_ops

    data = _vault(dispute=None)
    data["metadata"]["pending_conflicts"] = [{
        "conflict_id": "m-1", "section": "work_experience", "field": "team_size", "entity_id": W,
        "existing_value": 38, "incoming_value": 40, "source": "cv_upload"}]
    out = apply_ops(_model(data), [_kind(W, 40)], "interview").profile
    assert _open(out) == []


# ── the resolution: a "Bisher" that is no longer stored writes nothing ───────


@pytest.mark.parametrize("resolution", ["existing", "incoming"])
def test_nf9_resolving_a_stale_dispute_writes_nothing_and_rebases_it(resolution):
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.resolution import build_resolve_field_op

    profile = _model(_vault(size=50))          # stored 50, the card says 38 → 45
    conflict = _open(profile)[0]
    out = apply_ops(profile, [build_resolve_field_op(conflict, resolution=resolution)], "manual_edit").profile
    assert _size(out) == 50
    (c,) = _open(out)
    assert (c.existing_value, c.incoming_value) == (50, 45) and c.conflict_id != "c-1"


@pytest.mark.parametrize(("resolution", "size"), [("existing", 38), ("incoming", 45)])
def test_nf9_a_current_dispute_still_resolves_as_before(resolution, size):
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.resolution import build_resolve_field_op

    profile = _model(_vault())
    out = apply_ops(profile, [build_resolve_field_op(_open(profile)[0], resolution=resolution)],
                    "manual_edit").profile
    assert _size(out) == size and _open(out) == []


def test_nf9_a_manual_value_is_the_persons_own_answer_and_is_written():
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.resolution import build_resolve_field_op

    profile = _model(_vault(size=50))
    out = apply_ops(profile, [build_resolve_field_op(_open(profile)[0], resolution="manual", value=47)],
                    "manual_edit").profile
    assert _size(out) == 47 and _open(out) == []


def test_nf9_a_dispute_on_another_field_resolves_as_before():
    """Only size disputes carry the stored value as "Bisher"; another field's
    ``flag_conflict.existing`` is the model's reading and is not compared."""
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.resolution import build_resolve_field_op

    data = _vault(dispute=None)
    data["work_experience"][0]["location"] = "Koblenz"
    data["metadata"]["pending_conflicts"] = [{
        "conflict_id": "l-1", "section": "work_experience", "field": "location", "entity_id": W,
        "existing_value": "Neuwied", "incoming_value": "Andernach", "source": "agent_interview"}]
    profile = _model(data)
    out = apply_ops(profile, [build_resolve_field_op(_open(profile)[0], resolution="incoming")],
                    "manual_edit").profile
    assert next(e for e in out.work_experience if e.id == W).location == "Andernach"
    assert _open(out) == []


# ── doors ────────────────────────────────────────────────────────────────────


async def _seed_profile(db, data):
    await retire_live_profiles(db)
    profile = make_master_profile(profile_json=copy.deepcopy(data))
    db.add(profile)
    await db.commit()
    return profile


@pytest.mark.asyncio
async def test_nf9_rest_resolve_of_a_stale_dispute_is_refused_and_the_successor_stays(db):
    """``POST /api/profile/conflicts/<id>/resolve`` on a dispute whose "Bisher"
    is no longer stored: nothing written, 409 ``conflict_changed`` (the service
    raises ``ConflictChanged``), the re-based dispute is open with the current
    values."""
    from applire.services.profile import ConflictChanged, resolve_conflict

    profile = await _seed_profile(db, _vault(size=50))
    with pytest.raises(ConflictChanged) as err:
        await resolve_conflict("c-1", "existing", None, db, user_id=profile.user_id)
    await db.refresh(profile)
    stored = _model(profile.profile_json)
    assert _size(stored) == 50
    (c,) = _open(stored)
    assert (c.existing_value, c.incoming_value) == (50, 45)
    assert err.value.successor_id == c.conflict_id


@pytest.mark.asyncio
async def test_nf9_rest_route_maps_conflict_changed_to_409(db):
    from fastapi import HTTPException

    from applire.routers.profile import resolve_profile_conflict
    from applire.schemas.profile import ConflictResolutionRequest

    profile = await _seed_profile(db, _vault(size=50))
    user = MagicMock(id=profile.user_id)
    with patch("applire.routers.profile._uid", return_value=profile.user_id):
        with pytest.raises(HTTPException) as err:
            await resolve_profile_conflict("c-1", ConflictResolutionRequest(resolution="existing"), db, user)
    assert err.value.status_code == 409 and err.value.detail == "conflict_changed"


# the re-probe's own sequence: dispute parked, the kind chip states the size,
# then "Bisher" on the old card (UI door and agent door for the chip)


async def _add_dispute(db, profile, *, eid, incoming):
    """The agent door states another size (F-11 parks it, as in re-probe 2 t4)."""
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import SetField

    await commit_ops(db, [SetField(target=eid, field="team_size", value=str(incoming))],
                     CommitProvenance(source="agent_interview", intake="interview", actor="candidate"),
                     record=profile, grounding=None, snapshot=None, llm_provider=None)
    await db.commit()
    await db.refresh(profile)
    (c,) = _open(_model(profile.profile_json))
    return c.conflict_id


async def _kind_chip(db, sid, provider, answer, door):
    from test_sorbet_v_round3 import _bridge

    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(False)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        if door == "ui":
            from applire.services.session import send_message

            return (await send_message(sid, answer, db, provider)).model_dump(mode="json")
        from applire.mcp.server import mcp

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=db)
        cm.__aexit__ = AsyncMock(return_value=False)
        from applire.models.session import InterviewSession

        uid = (await db.get(InterviewSession, sid)).user_id
        with (
            patch("applire.mcp.server.get_db", return_value=cm),
            patch("applire.mcp.server.get_provider", return_value=provider),
            patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uid)),
        ):
            out = await mcp.call_tool("send_message", {"session_id": str(sid), "message": answer})
        blocks = out[0] if isinstance(out, tuple) else out
        return json.loads(blocks[0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
@pytest.mark.parametrize(("answer", "size", "left"), [
    ("Fachliche Anleitung – 3 Personen", 3, []),           # the stated size: settled
    ("Fachliche Anleitung – 4 Personen", 4, [(4, 3)]),     # another size: re-based
])
async def test_nf9_after_the_kind_chip_the_old_card_cannot_revert_the_answer(db, door, answer, size, left):
    from applire.services.profile import resolve_conflict
    from test_sorbet_v_round3 import _kind_session

    sid, first, provider, profile = await _kind_session(db, team_size=2)
    cid = await _add_dispute(db, profile, eid="w1", incoming=3)
    out = await _kind_chip(db, sid, provider, answer, door)
    assert out["changes_applied"] is True
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["team_size"] == size
    with pytest.raises(LookupError):                       # 404 / 409 at the REST door
        await resolve_conflict(cid, "existing", None, db, user_id=profile.user_id)
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["team_size"] == size, "the answer stays"
    assert [(c.existing_value, c.incoming_value) for c in _open(_model(profile.profile_json))] == left


# the profile review (UI drawer and the agent's review session): a question
# rendered before the size moved is asked again with the current values


async def _review(db, door, sid, provider, *, outcome=None, message=""):
    if door == "ui":
        from applire.services.session import send_message

        return (await send_message(sid, message, db, provider, outcome=outcome)).model_dump(mode="json")
    from applire.mcp.server import mcp
    from applire.models.session import InterviewSession

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    uid = (await db.get(InterviewSession, sid)).user_id
    # the agent door types its answer (no structured outcome on send_message)
    args = {"session_id": str(sid), "message": message or {"keep_current": "Bisher behalten"}[outcome]}
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider", return_value=provider),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uid)),
    ):
        out = await mcp.call_tool("send_message", args)
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


async def _move_size(db, profile, size):
    from applire.services.profile.commit import CommitProvenance, commit_ops

    await commit_ops(db, [_kind(W, size)], CommitProvenance(source="interview", intake="interview",
                                                            actor="candidate"),
                     record=profile, grounding=None, snapshot=None, llm_provider=None)
    await db.commit()
    await db.refresh(profile)


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
@pytest.mark.parametrize("how", [{"outcome": "keep_current"}, {"message": "Bisher behalten"}])
async def test_nf9_a_review_question_shown_before_the_size_moved_is_asked_again(db, door, how):
    from applire.services.session import create_profile_review_session

    profile = await _seed_profile(db, _vault())
    provider = MagicMock()
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no model call"))
    created = await create_profile_review_session(db, provider, lang="de", user_id=profile.user_id)
    assert "38" in created.question
    await _move_size(db, profile, 50)                      # e.g. the size chip in a job interview
    out = await _review(db, door, created.session_id, provider, **how)
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["team_size"] == 50, "nothing written"
    assert out["complete"] is False and "50" in out["question"] and "45" in out["question"]
    (successor,) = _open(_model(profile.profile_json))
    assert out["current_conflict"]["conflict_id"] == successor.conflict_id
    again = await _review(db, door, created.session_id, provider, outcome="keep_current")
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["team_size"] == 50
    assert _open(_model(profile.profile_json)) == []
    assert again["profile_updated_at"]


@pytest.mark.asyncio
async def test_nf9_a_review_question_whose_dispute_was_answered_meanwhile_moves_on(db):
    """The stated size was stored meanwhile: nothing to ask — ADV-8's
    "already answered", the review advances, nothing written."""
    from applire.services.session import create_profile_review_session, send_message

    profile = await _seed_profile(db, _vault())
    provider = MagicMock()
    created = await create_profile_review_session(db, provider, lang="de", user_id=profile.user_id)
    await _move_size(db, profile, 45)
    out = await send_message(created.session_id, "", db, provider, outcome="keep_current")
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["team_size"] == 45
    assert out.interpreted is not None and out.interpreted.already_answered is True


@pytest.mark.asyncio
async def test_nf9_another_statement_on_the_same_size_is_not_taken_for_the_shown_one(db):
    """Answered meanwhile, and a NEW statement (45 → 50) parked on the same
    size: the old question is "already answered"; the new dispute is its own."""
    from applire.services.session import create_profile_review_session, send_message
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import SetField

    profile = await _seed_profile(db, _vault())
    provider = MagicMock()
    created = await create_profile_review_session(db, provider, lang="de", user_id=profile.user_id)
    await _move_size(db, profile, 45)
    await commit_ops(db, [SetField(target=W, field="team_size", value="50")],
                     CommitProvenance(source="agent_interview", intake="interview", actor="candidate"),
                     record=profile, grounding=None, snapshot=None, llm_provider=None)
    await db.commit()
    out = await send_message(created.session_id, "", db, provider, outcome="keep_current")
    await db.refresh(profile)
    assert out.interpreted is not None and out.interpreted.already_answered is True
    assert [(c.existing_value, c.incoming_value) for c in _open(_model(profile.profile_json))] == [(45, 50)]


# ── NF-10: one normalisation, one rule for the three closed questions ────────
# replace (V-1 / V-F1), size follow-up (Q8-6), leadership kind (MD-Q5): the
# same decorations are tolerated (case, spacing, surrounding quotes, closing
# ".", "!", "…"); a question-shaped reply ("…?") is never the chip and gets the
# one re-ask with the follow-up kept open; a non-chip reply is read without
# Applire's question text (F-1).


def _size_cl(new=12):
    from test_sorbet_v_round3 import _size_cluster

    return _size_cluster(new=new)


@pytest.mark.parametrize(("message", "decision"), [
    ("Auf 12 ändern", "change"), ("auf 12 ändern!", "change"), ("„Auf 12 ändern“", "change"),
    ("Auf 12 ändern …", "change"), ("Change to 12!", "change"), ("'So lassen'", "keep"),
    ("Auf 12 ändern?", None), ("„Auf 12 ändern?“", None), ("Auf 12 ändern?!", None),
    ("Auf 12 ändern？", None), ("Ja, auf 12 ändern", None),
    ("Auf 12 a\u0308ndern", "change"),       # a decomposed umlaut (NFD input)
])
def test_nf10_the_size_chip_takes_the_shared_normalisation(message, decision):
    from applire.services.interview.scope_question import size_bullet_decision

    assert size_bullet_decision(_size_cl(), message) == decision


@pytest.mark.parametrize(("message", "parsed"), [
    ("Disziplinarisch (Personalverantwortung) – 45 Personen", ("disciplinary", 45)),
    ("Disziplinarisch (Personalverantwortung) – 45 Personen!", ("disciplinary", 45)),
    ("„Disziplinarisch (Personalverantwortung) – 45 Personen“", ("disciplinary", 45)),
    ("„Fachliche Anleitung“", ("functional", None)),
    ("Disciplinary (line management) – 45 people…", ("disciplinary", 45)),
])
def test_nf10_the_kind_chip_takes_the_shared_normalisation(message, parsed):
    from applire.services.profile.leadership_kind import parse_kind_answer

    assert parse_kind_answer(message) == parsed


@pytest.mark.parametrize("message", [
    "Ersetzen?", "Auf 45 ändern?", "Disziplinarisch (Personalverantwortung) – 38 Personen?",
    "„Fachliche Anleitung?“", "Fachliche Anleitung?!", "Replace？", "Keep both‽", "So lassen ?",
])
def test_nf10_a_question_shaped_reply_is_recognised_the_same_way_everywhere(message):
    from applire.services.interview.scope_question import closed_reply_is_question

    assert closed_reply_is_question(message) is True


@pytest.mark.parametrize("message", ["Ersetzen!", "„Auf 45 ändern“", "Fachliche Anleitung – 3 Personen.",
                                     "Ich habe drei Leute eingearbeitet."])
def test_nf10_a_statement_is_not_question_shaped(message):
    from applire.services.interview.scope_question import closed_reply_is_question

    assert closed_reply_is_question(message) is False


async def _closed_q(state, profile, provider, *_a, **kw):
    from applire.services import interview_graph as g
    from applire.services.interview.scope_question import (
        is_bullet_replace_cluster, is_leadership_kind_cluster, is_size_bullet_cluster)

    current = state["gap_clusters_by_id"][state["critical_gaps"][state["current_gap_index"]]]
    lang = kw.get("lang") or "de"
    if is_size_bullet_cluster(current):
        return g.size_bullet_question(current, lang)
    if is_bullet_replace_cluster(current):
        return g.bullet_replace_question(current, lang)
    if is_leadership_kind_cluster(current):
        return g.leadership_kind_question(current, lang, profile or None)
    return {"question": "Weiter?", "choices": None}


async def _size_session(db):
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.apply import apply_ops
    from test_sorbet_v_round3 import _NEXT, SHIFT, _profile, _seed

    base = MasterProfileData.model_validate(_profile(responsibilities=[SHIFT], team_size=14))
    corrected = apply_ops(base, [_kind("w-1", 12)], "interview").profile
    return await _seed(db, [_size_cl(), _NEXT], corrected.model_dump(mode="json"))


def _nothing_bridge():
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    return AsyncMock(return_value=InterviewTurnResult(profile_dict={}, changes=[], addressed=False))


async def _closed_turn(db, record, answer, door, bridge):
    from test_sorbet_v_round3 import _patches, _provider

    p1, p2, p3 = _patches(bridge)
    with p1, p2, p3, patch("applire.services.session._cluster_question", new=_closed_q):
        if door == "ui":
            from applire.services.session import send_message

            return (await send_message(record.id, answer, db, _provider())).model_dump(mode="json")
        from applire.mcp.server import mcp

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=db)
        cm.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("applire.mcp.server.get_db", return_value=cm),
            patch("applire.mcp.server.get_provider", return_value=_provider()),
            patch("applire.mcp.server._current_user_id", AsyncMock(return_value=record.user_id)),
        ):
            out = await mcp.call_tool("send_message", {"session_id": str(record.id), "message": answer})
        blocks = out[0] if isinstance(out, tuple) else out
        return json.loads(blocks[0].text)


def _current(record):
    st = record.state
    return st["gap_clusters_by_id"][st["critical_gaps"][st["current_gap_index"]]]


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
@pytest.mark.parametrize("reply", ["Auf 12 ändern?", "Ja, auf 12 ändern", "Change to 12?"])
async def test_nf10_a_non_chip_reply_to_the_size_follow_up_gets_the_one_reask(db, door, reply):
    from applire.services.profile.decisions import default_follow_up

    record, profile = await _size_session(db)
    bridge = _nothing_bridge()
    out = await _closed_turn(db, record, reply, door, bridge)
    await db.refresh(profile)
    await db.refresh(record)
    assert "14 Mitarbeitenden" in profile.profile_json["work_experience"][0]["responsibilities"][0]
    assert out["question"].startswith(default_follow_up("de"))
    nxt = _current(record)
    assert nxt.get("kind") == "size_bullet" and nxt.get("reask") is True
    assert bridge.await_args.kwargs["question"] == "", "read without Applire's question (F-1)"
    # the chip on the re-ask decides
    again = await _closed_turn(db, record, "Auf 12 ändern", door, _nothing_bridge())
    await db.refresh(profile)
    assert "12 Mitarbeitenden" in profile.profile_json["work_experience"][0]["responsibilities"][0]
    assert again["changes_applied"] is True


@pytest.mark.asyncio
async def test_nf10_a_second_non_chip_reply_settles_the_size_follow_up(db):
    record, profile = await _size_session(db)
    await _closed_turn(db, record, "Auf 12 ändern?", "ui", _nothing_bridge())
    out = await _closed_turn(db, record, "Ja, auf 12 ändern", "ui", _nothing_bridge())
    await db.refresh(profile)
    await db.refresh(record)
    assert "14 Mitarbeitenden" in profile.profile_json["work_experience"][0]["responsibilities"][0]
    assert out["question"] == "Weiter?" and _current(record).get("kind") != "size_bullet"
    open_records = [r for r in profile.profile_json["work_experience"][0].get("size_corrections") or []
                    if r.get("status", "open") == "open"]
    assert open_records, "the record stays open: the CV keeps the line withheld"


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
async def test_nf10_a_question_shaped_kind_reply_is_not_stored_and_is_asked_again(db, door):
    from applire.services.profile.decisions import default_follow_up
    from applire.services.session import send_message
    from test_sorbet_v_round3 import _bridge, _kind_session

    sid, first, provider, profile = await _kind_session(db, team_size=2)
    reply = "Fachliche Anleitung – 2 Personen?"
    bridge = _bridge(False)
    with (
        patch("applire.services.session.reconcile_interview_turn", new=bridge),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        out = await _kind_chip(db, sid, provider, reply, door)
    await db.refresh(profile)
    entry = profile.profile_json["work_experience"][0]
    assert entry.get("leadership_kind") is None and not entry.get("leadership_kind_record")
    assert out["question"].startswith(default_follow_up("de"))
    assert out["choices"] and out["choices"][0].startswith("Disziplinarisch")
    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(False)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        done = await send_message(sid, "„Fachliche Anleitung – 2 Personen!“", db, provider)
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["leadership_kind"] == "functional"
    assert done.changes_applied is True


@pytest.mark.asyncio
async def test_nf10_a_kind_reply_in_own_words_is_still_recorded_as_own_words(db):
    """MD-Q5 unchanged: a statement in the person's own words is their answer."""
    from applire.services.session import send_message
    from test_sorbet_v_round3 import _bridge, _kind_session

    sid, first, provider, profile = await _kind_session(db, team_size=2)
    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(False)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        await send_message(sid, "Ich habe zwei Werkstudierende eingearbeitet.", db, provider)
    await db.refresh(profile)
    record = profile.profile_json["work_experience"][0].get("leadership_kind_record") or {}
    assert record.get("option") == "own_words"
    from applire.models.session import InterviewSession

    state = (await db.get(InterviewSession, sid)).state
    assert not any(c.get("reask") for c in state["gap_clusters_by_id"].values()), "no re-ask"


# ── NF-12: the basis is reported whenever the row moved ─────────────────────
# re-probe 2 tb t4: denials + a parked dispute moved ``updated_at``
# (20:53:58 → 20:55:20) while the turn said ``profile_updated_at: null``.

_DENIAL_AND_DISPUTE = {
    "ops": [{"op": "flag_conflict", "target": W, "field": "team_size", "existing": "38", "incoming": "45"}],
    "ambiguities": [], "denials": ["Kubernetes"], "items": [],
}


@pytest.mark.asyncio
async def test_nf12_a_denial_and_dispute_turn_carries_the_basis_at_the_bridge(db):
    from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn

    profile = await _seed_profile(db, _vault(dispute=None))
    before = profile.updated_at
    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value=copy.deepcopy(_DENIAL_AND_DISPUTE))
    out = await reconcile_interview_turn(
        db, profile_record=profile, gap="Kubernetes", question="Hast du mit Kubernetes gearbeitet?",
        answer="Nein, damit habe ich nicht gearbeitet. Bei Weberit sind es übrigens inzwischen 45 Leute.",
        provider=provider, session_id="s-v4", lang="de",
    )
    assert out.addressed is False and out.denial_recorded is True and out.conflict_summaries
    assert out.written_at is not None and out.written_at == profile.updated_at != before


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
async def test_nf12_both_doors_report_the_basis_of_a_denial_and_dispute_turn(db, door):
    from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn
    from test_sorbet_v_round3 import _NEXT, _seed

    record, profile = await _seed(db, [{"id": "Kubernetes", "label": "Kubernetes", "gaps": ["Kubernetes"],
                                        "jd_skills": [], "jd_context": ""}, _NEXT], _vault(dispute=None))
    from test_sorbet_v_round3 import _patches, _provider

    provider = _provider()
    provider.aparse_json = AsyncMock(return_value=copy.deepcopy(_DENIAL_AND_DISPUTE))
    p1, p2, p3 = _patches(reconcile_interview_turn)
    answer = "Nein, damit habe ich nicht gearbeitet. Bei Weberit sind es übrigens inzwischen 45 Leute."
    with p1, p2, p3:
        if door == "ui":
            from applire.services.session import send_message

            out = (await send_message(record.id, answer, db, provider)).model_dump(mode="json")
        else:
            from applire.mcp.server import mcp

            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=db)
            cm.__aexit__ = AsyncMock(return_value=False)
            with (
                patch("applire.mcp.server.get_db", return_value=cm),
                patch("applire.mcp.server.get_provider", return_value=provider),
                patch("applire.mcp.server._current_user_id", AsyncMock(return_value=record.user_id)),
            ):
                res = await mcp.call_tool("send_message", {"session_id": str(record.id), "message": answer})
            blocks = res[0] if isinstance(res, tuple) else res
            out = json.loads(blocks[0].text)
    await db.refresh(profile)
    assert out["changes_applied"] is False
    assert out["pending_conflicts"]
    assert out["profile_updated_at"] is not None
    assert datetime.fromisoformat(str(out["profile_updated_at"]).replace("Z", "+00:00")).replace(tzinfo=None) \
        == profile.updated_at.replace(tzinfo=None)
