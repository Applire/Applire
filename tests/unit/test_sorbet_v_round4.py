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

