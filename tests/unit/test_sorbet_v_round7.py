# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sorbet invite-gate WP-V, round 7 — adv/vault "Re-probe 5" (RULING MD-G23).

* NF-20 — a team size given as TEXT is digits only ("+" and leading zeros
  tolerated); any separator inside the number ("1.000", "45.0", "45,0",
  "1 000", "1'000") is no size: a typed reply is re-asked, ``manual`` and
  flag time refuse. A JSON float with a zero fraction (45.0) stays a size.
* NF-21 — a digit string longer than ``MAX_TEAM_SIZE`` has digits is out of
  range BEFORE any conversion: the plain refusal, never Python's int-limit text.

Synthetic data only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from tests.support.mcp_door import mcp_signing_secret  # noqa: E402,F401 — autouse
from test_sorbet_v_round4 import W, _model, _open, _seed_profile, _vault, db  # noqa: E402,F401
from test_sorbet_v_round5 import _review_turn  # noqa: E402
from test_sorbet_v_round6 import CHIPS_EN, _rebased, _state  # noqa: E402

LONG = "4" * 5000          # over Python's 4,300-digit int-string limit
LONG_9 = "9" * 4300        # at the limit — also no size
SEPARATED = ["1.000", "10.000", "100.000", "45.0", "45.00", "45,0", "1 000", "1'000", "1’000", "4.5"]
_RAW = ("int_parsing", "validation error", "pydantic", "MasterProfileData", "Exceeds the limit",
        "set_int_max_str_digits", "integer string conversion")


def _plain(text: str) -> None:
    assert "team size" in text, text[:300]
    for raw in _RAW:
        assert raw not in text, raw
    assert len(text) < 600, "the refusal does not echo a 5,000-digit value"


# ── NF-20 / NF-21: the reader ───────────────────────────────────────────────


@pytest.mark.parametrize(("value", "size"), [
    ("45", 45), ("+45", 45), ("045", 45), ("0045", 45), (" 45 ", 45), ("٤٥", 45), (45, 45), (45.0, 45),
    ("1000", 1000), ("10000000", 10_000_000), ("0" * 5000 + "45", 45),
    *[(s, None) for s in SEPARATED],
    ("45.", None), (45.5, None), ("1e3", None), (LONG, None), (LONG_9, None), ("-" + LONG, None),
    ("+" + LONG, None), pytest.param(10**5000, None, id="10^5000"), (float("inf"), None), (float("nan"), None),
], ids=lambda v: (str(v)[:12] + "…") if isinstance(v, str) and len(v) > 12 else None)
def test_nf20_nf21_the_size_reader(value, size):
    from applire.services.profile.reconcile.apply import readable_size

    assert readable_size(value) == size


@pytest.mark.parametrize("value", [LONG, pytest.param(10**5000, id="10^5000"), "1.000", "-" + LONG],
                         ids=lambda v: str(v)[:12])
def test_nf21_the_refusal_message_is_plain_and_bounded(value):
    from applire.services.profile.reconcile.apply import unreadable_size_message

    _plain(unreadable_size_message(value))


@pytest.mark.parametrize("reply", SEPARATED)
def test_nf20_a_separated_number_is_re_asked(reply):
    from applire.services.interview_graph import read_size_reply

    assert read_size_reply(reply, 50, 45, CHIPS_EN) == ("unclear", None)


def test_nf20_the_reply_normalisations_closing_full_stop_stays():
    from applire.services.interview_graph import read_size_reply

    assert read_size_reply("45.", 50, 45, CHIPS_EN) == ("incoming", None)
    assert read_size_reply("1000.", 50, 45, CHIPS_EN) == ("manual", 1000)


def test_nf21_a_long_digit_string_is_a_bare_number_refused_at_write_time():
    from applire.services.interview_graph import read_size_reply

    decision, value = read_size_reply(LONG, 50, 45, CHIPS_EN)
    assert decision == "manual" and value == LONG


@pytest.mark.parametrize("incoming", ["1.000", "10.000", "45.0", "45,0", "1 000", LONG,
                                      pytest.param(10**5000, id="10^5000")], ids=lambda v: str(v)[:12])
def test_nf20_nf21_flag_time_refuses(incoming):
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import FlagConflict

    out = apply_ops(_model(_vault(dispute=None)),
                    [FlagConflict(target=W, field="team_size", existing="38", incoming=incoming)],
                    "agent_interview")
    assert out.conflicts == []
    assert [i.reason for i in out.not_applied] == ["op_rejected"]
    assert len(out.not_applied[0].label) < 300


def test_nf20_flag_time_keeps_a_json_float_with_zero_fraction():
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import FlagConflict

    out = apply_ops(_model(_vault(dispute=None)),
                    [FlagConflict(target=W, field="team_size", existing="38", incoming=45.0)],
                    "agent_interview")
    assert [(c.existing_value, c.incoming_value) for c in out.conflicts] == [(38, 45)]


# ── NF-20 / NF-21: the doors ────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
@pytest.mark.parametrize("reply", ["1.000", "10.000", "45.0", "1 000"])
async def test_nf20_a_separated_number_is_re_asked_on_both_doors(db, door, reply):
    from applire.services.profile.decisions import size_follow_up

    profile, provider, created = await _rebased(db)
    out = await _review_turn(db, door, created.session_id, provider, reply)
    await db.refresh(profile)
    assert provider.aparse_json.await_count == 0
    assert out["complete"] is False
    assert out["question"].startswith((size_follow_up("de"), size_follow_up("en")))
    assert _state(profile) == (50, [(50, 45)])


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [LONG, LONG_9])
async def test_nf21_the_agent_review_door_refuses_plainly(db, reply):
    profile, provider, created = await _rebased(db)
    with pytest.raises(Exception) as err:
        await _review_turn(db, "agent", created.session_id, provider, reply)
    _plain(str(err.value))
    await db.refresh(profile)
    assert _state(profile) == (50, [(50, 45)])


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [LONG, LONG_9])
async def test_nf21_the_ui_review_route_refuses_plainly(db, reply):
    from fastapi import HTTPException

    from applire.routers.session import post_message
    from applire.schemas.session import SessionMessageRequest

    profile, provider, created = await _rebased(db)
    with pytest.raises(HTTPException) as err:
        await post_message(created.session_id, SessionMessageRequest(message=reply), db, provider,
                           MagicMock(id=profile.user_id))
    assert err.value.status_code in (409, 422)
    _plain(str(err.value.detail))
    await db.refresh(profile)
    assert _state(profile) == (50, [(50, 45)])


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["1.000", "10.000", "45.0", "45.00", "45,0", "1 000", "1'000", LONG, LONG_9])
async def test_nf20_nf21_rest_manual_refuses_plainly(db, value):
    from fastapi import HTTPException

    from applire.routers.profile import resolve_profile_conflict
    from applire.schemas.profile import ConflictResolutionRequest

    profile = await _seed_profile(db, _vault())
    with patch("applire.routers.profile._uid", return_value=profile.user_id):
        with pytest.raises(HTTPException) as err:
            await resolve_profile_conflict(
                "c-1", ConflictResolutionRequest(resolution="manual", value=value), db,
                MagicMock(id=profile.user_id))
    assert err.value.status_code == 422
    _plain(str(err.value.detail))
    await db.refresh(profile)
    assert _state(profile) == (38, [(38, 45)])


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "stored"), [("1000", 1000), (45.0, 45), ("+045", 45)])
async def test_nf20_rest_manual_keeps_digits_and_a_zero_fraction_float(db, value, stored):
    from applire.services.profile import resolve_conflict

    profile = await _seed_profile(db, _vault())
    await resolve_conflict("c-1", "manual", value, db, user_id=profile.user_id)
    await db.refresh(profile)
    assert _state(profile) == (stored, [])
