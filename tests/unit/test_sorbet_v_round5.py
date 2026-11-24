# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sorbet invite-gate WP-V, round 5 — adv/vault "Re-probe 3" (RULING MD-G19).

* NF-13 — on a team-size dispute the number in the reply decides: one side's
  number chooses that side when no intent word says otherwise; a number that
  is neither side, both sides, or contradicts the intent word is unclear → the
  one re-ask, read by no model, nothing written. A reply without a number
  keeps today's reading; non-size disputes are unchanged.
* NF-15 — a team-size ``flag_conflict`` whose value is no whole number is not
  parked (receipted as a dropped change); a stored text-valued size dispute
  answers "Neu" with a plain message on both doors, never a validation dump.

Synthetic data only.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from tests.support.mcp_door import mcp_signing_secret  # noqa: E402,F401 — autouse
from test_sorbet_v_round4 import (  # noqa: E402,F401 — the round-4 harness
    W, _kind, _model, _move_size, _open, _seed_profile, _vault, db,
)

KEEP50 = ["Keep current: 50", "Use the new value: 45"]


# ── NF-13: the number decides ────────────────────────────────────────────────


@pytest.mark.parametrize(("reply", "existing", "incoming", "choices", "decision"), [
    # the reviewer's sequence: re-based 50 → 45, the old chip "Keep current: 38"
    ("Keep current: 38", 50, 45, KEEP50, "unclear"),
    ("Keep current: 50", 50, 45, KEEP50, "existing"),
    ("Use the new value: 45", 50, 45, KEEP50, "incoming"),
    ("Bisher behalten: 38", 50, 45, None, "unclear"),
    # "Nimm die 38" while 38 is the current side: "take" says the new one
    ("Nimm die 38", 38, 45, None, "unclear"),
    ("Nimm die 45", 38, 45, None, "incoming"),
    ("Take 38", 38, 45, None, "unclear"),
    ("Behalten: 45", 38, 45, None, "unclear"),
    ("Ändern auf 38", 38, 45, None, "unclear"),
    # a number alone, or with an agreeing word, chooses its side
    ("38", 38, 45, None, "existing"),
    ("es sind 45", 38, 45, None, "incoming"),
    ("Bisher behalten, 38 stimmt", 38, 45, None, "existing"),
    ("Neu übernehmen – 45 Leute", 38, 45, None, "incoming"),
    # neither side, both sides
    ("Es sind jetzt 52", 38, 45, None, "unclear"),
    ("Nicht 38, sondern 45", 38, 45, None, "unclear"),
    # no number: today's reading
    ("Bisher behalten", 38, 45, None, "existing"),
    ("Keep current", 38, 45, None, "existing"),
    ("Neu übernehmen", 38, 45, None, "incoming"),
    # string-typed sides (a dispute stored before typed values)
    ("Keep current: 38", "50", "45", KEEP50, "unclear"),
])
def test_nf13_on_a_size_dispute_the_number_decides(reply, existing, incoming, choices, decision):
    from applire.services.interview_graph import interpret_conflict_answer

    assert interpret_conflict_answer(reply, existing, incoming, choices=choices, field="team_size") == decision


@pytest.mark.parametrize(("reply", "decision"), [
    ("Keep current: Bonn", "existing"),     # a value that is neither side: the intent word, as before
    ("Use the new value: Köln", "incoming"),
    ("Neuwied", "existing"),
])
def test_nf13_a_non_size_dispute_reads_as_before(reply, decision):
    from applire.services.interview_graph import interpret_conflict_answer

    assert interpret_conflict_answer(reply, "Neuwied", "Andernach", field="location") == decision
    assert interpret_conflict_answer(reply, "Neuwied", "Andernach") == decision


async def _review_turn(db, door, sid, provider, message):
    from applire.models.session import InterviewSession

    if door == "ui":
        from applire.services.session import send_message

        return (await send_message(sid, message, db, provider)).model_dump(mode="json")
    from applire.mcp.server import mcp

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    uid = (await db.get(InterviewSession, sid)).user_id
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider", return_value=provider),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=uid)),
    ):
        out = await mcp.call_tool("send_message", {"session_id": str(sid), "message": message})
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


def _no_model():
    provider = MagicMock()
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no model call"))
    provider.acomplete = AsyncMock(side_effect=AssertionError("no model call"))
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
async def test_nf13_the_reviewers_sequence_never_decides_the_rebased_dispute(db, door):
    """Re-probe 3 (ub): 38 → 45 parked; review shows "Keep current: 38"; the
    size moves to 50; "Keep current: 38" → asked again with 50 → 45; "Keep
    current: 38" AGAIN → the one re-ask, no model call, nothing written,
    dispute open."""
    from applire.services.profile.decisions import default_follow_up
    from applire.services.session import create_profile_review_session

    profile = await _seed_profile(db, _vault())
    provider = _no_model()
    created = await create_profile_review_session(db, provider, lang="en", user_id=profile.user_id)
    await _move_size(db, profile, 50)
    first = await _review_turn(db, door, created.session_id, provider, "Keep current: 38")
    assert "50" in first["question"] and first["complete"] is False
    again = await _review_turn(db, door, created.session_id, provider, "Keep current: 38")
    await db.refresh(profile)
    assert again["complete"] is False
    assert again["question"].startswith(default_follow_up("en"))
    assert profile.profile_json["work_experience"][0]["team_size"] == 50
    assert [(c.existing_value, c.incoming_value) for c in _open(_model(profile.profile_json))] == [(50, 45)]
    # the matching chip then decides
    done = await _review_turn(db, door, created.session_id, provider, "Keep current: 50")
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["team_size"] == 50
    assert _open(_model(profile.profile_json)) == []
    assert done["complete"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
@pytest.mark.parametrize("reply", ["Nimm die 38", "Es sind jetzt 52"])
async def test_nf13_a_contradicting_or_third_number_writes_nothing(db, door, reply):
    from applire.services.profile.decisions import default_follow_up
    from applire.services.session import create_profile_review_session

    profile = await _seed_profile(db, _vault())
    provider = _no_model()
    created = await create_profile_review_session(db, provider, lang="de", user_id=profile.user_id)
    out = await _review_turn(db, door, created.session_id, provider, reply)
    await db.refresh(profile)
    # the follow-up line's language is the conversation language (no posting:
    # the account's), the dispute's own question the review's — pre-existing
    assert out["complete"] is False
    assert out["question"].startswith((default_follow_up("de"), default_follow_up("en")))
    assert profile.profile_json["work_experience"][0]["team_size"] == 38
    assert [(c.existing_value, c.incoming_value) for c in _open(_model(profile.profile_json))] == [(38, 45)]


@pytest.mark.asyncio
async def test_nf13_the_re_ask_is_spent_once(db):
    """After the one re-ask, a second unclear number answers ``outcome_required``
    (the existing ADR-099 rule), still writing nothing."""
    from applire.services.session import OutcomeRequired, create_profile_review_session, send_message

    profile = await _seed_profile(db, _vault())
    provider = _no_model()
    created = await create_profile_review_session(db, provider, lang="de", user_id=profile.user_id)
    await send_message(created.session_id, "Nimm die 38", db, provider)
    with pytest.raises(OutcomeRequired):
        await send_message(created.session_id, "Nimm die 38", db, provider)
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["team_size"] == 38

