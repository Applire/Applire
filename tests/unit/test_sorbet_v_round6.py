# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sorbet invite-gate WP-V, round 6 — adv/vault "Re-probe 4" (RULING MD-G22).

* NF-16 / NF-17 — a typed reply to a TEAM-SIZE dispute is read without a word
  list and without a model: the exact chip → that side; a bare whole number →
  that side when it equals one, else the person's correction (``manual``);
  anything else (a sentence, a negation, two numbers, a number word, text with
  no number) → the one existing re-ask, nothing written; a second such reply →
  ``outcome_required``.
* NF-18 — a ``manual`` team size goes through the flag path's size reader
  (whole number, 1 … ``MAX_TEAM_SIZE``); anything else is refused with the
  plain message on every door, nothing written, the dispute stays open.
* Non-size disputes — a reply with a negation is not decided by the legacy
  keep/use word matcher; it goes to the existing model reading.

Synthetic data only.
"""
from __future__ import annotations

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
    W, _model, _move_size, _open, _seed_profile, _vault, db,
)
from test_sorbet_v_round5 import _no_model, _review_turn  # noqa: E402

CHIPS_EN = ["Keep current: 50", "Use the new value: 45"]

#: Re-probe 4's replies on the 50 → 45 dispute (38 the stale "Bisher"), plus
#: the shapes it listed as untested and round 5's own size rows — every one is
#: re-asked: no model, nothing written.
REASKED = [
    # NF-16: a negated side's number / a negation with no number
    "Nicht 45", "Die 45 ist falsch", "Nein, nicht 45.", "not 45", "Nicht 50",
    "Keine 45, das stimmt nicht", "45 stimmt nicht", "50 ist veraltet", "Not 50 anymore",
    "Nicht ändern", "Nicht behalten", "Bitte nicht die alten Werte behalten",
    "Don't keep the current one",
    # NF-17: intent words outside any list, the stale number
    "Behalte die 38", "Lass es bei 38", "Stick with 38", "Nicht 38", "go with 38", "wähle 38",
    "übernimm 38", "Behalte die 45", "Lass die 45",
    # sentences with a number (MD-G19b reversed: "Es sind jetzt 52" is re-asked)
    "Es sind jetzt 52", "Seit 2019 sind es 52", "Seit 2019 sind es 45", "es sind 45",
    "Nicht mehr 38, das ist alt", "We are 52 people now", "Keep current: 38", "Nimm die 38",
    "Nimm die 45", "Bisher behalten, 50 stimmt",
    # two numbers, ranges, dates, decimals, number words, exponent
    "45 statt 50", "38-45", "45–50", "45 Leute, davon 5 Azubis", "Ab 01.01.2025 sind es 45",
    "45,0", "45.5", "4 5", "achtunddreißig", "Behalte achtunddreißig", "forty-five", "1e3",
    # no number at all, a question
    "Bisher behalten", "Keep current", "Neu übernehmen", "Behalten", "45?", "",
]


async def _rebased(db, lang="en"):
    """Re-probe 4's scenario: 38 → 45 parked, the size moved to 50 (dispute
    re-based to 50 → 45), then a profile review opened."""
    from applire.services.session import create_profile_review_session

    profile = await _seed_profile(db, _vault())
    await _move_size(db, profile, 50)
    provider = _no_model()
    created = await create_profile_review_session(db, provider, lang=lang, user_id=profile.user_id)
    return profile, provider, created


def _state(profile):
    data = _model(profile.profile_json)
    return data.work_experience[0].team_size, [(c.existing_value, c.incoming_value) for c in _open(data)]


# ── the reading itself ──────────────────────────────────────────────────────


@pytest.mark.parametrize("reply", REASKED)
def test_md_g22_anything_but_a_chip_or_a_bare_number_is_unclear(reply):
    from applire.services.interview_graph import read_size_reply

    assert read_size_reply(reply, 50, 45, CHIPS_EN) == ("unclear", None)


@pytest.mark.parametrize(("reply", "reading"), [
    ("Keep current: 50", ("existing", None)),
    ("keep current: 50.", ("existing", None)),
    ("  „Use the new value: 45“ ", ("incoming", None)),
    ("45", ("incoming", None)),
    (" 45. ", ("incoming", None)),
    ("+45", ("incoming", None)),
    ("045", ("incoming", None)),
    ("45.0", ("incoming", None)),
    ("50", ("existing", None)),
    ("٤٥", ("incoming", None)),
    ("52", ("manual", 52)),
    ("38", ("manual", 38)),        # a bare number IS the person's answer (A1-1 kept)
    ("0", ("manual", "0")),        # a bare number that is no size: refused at write time
    ("-3", ("manual", "-3")),
    ("1000000000000", ("manual", "1000000000000")),
])
def test_md_g22_the_chip_or_a_bare_number_decides(reply, reading):
    from applire.services.interview_graph import read_size_reply

    assert read_size_reply(reply, 50, 45, CHIPS_EN) == reading


def test_md_g22_string_typed_sides_read_the_same():
    from applire.services.interview_graph import read_size_reply

    assert read_size_reply("45", "50", "45", None) == ("incoming", None)
    assert read_size_reply("Nicht 45", "50", "45", None) == ("unclear", None)


def test_md_g22_the_size_intent_word_lists_are_gone():
    import applire.services.interview_graph as ig

    assert not hasattr(ig, "_TAKE_WORDS")
    assert not hasattr(ig, "size_reply_decision")


# ── both doors: the reviewer's list ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
@pytest.mark.parametrize("reply", [r for r in REASKED if r])
async def test_md_g22_the_reviewers_list_is_re_asked_on_both_doors(db, door, reply):
    from applire.services.profile.decisions import size_follow_up

    profile, provider, created = await _rebased(db)
    out = await _review_turn(db, door, created.session_id, provider, reply)
    await db.refresh(profile)
    assert provider.aparse_json.await_count == 0 and provider.acomplete.await_count == 0
    assert out["complete"] is False
    # RULING V-6: the size re-ask says the number works (language: the
    # conversation's — pre-existing, collector line round 5)
    assert out["question"].startswith((size_follow_up("de"), size_follow_up("en")))
    assert _state(profile) == (50, [(50, 45)])


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
@pytest.mark.parametrize(("reply", "size", "outcome"), [
    ("Keep current: 50", 50, "keep_current"),
    ("Use the new value: 45", 45, "take_new"),
    ("45", 45, "take_new"),
    ("50.", 50, "keep_current"),
    ("52", 52, "manual"),
    ("38", 38, "manual"),
])
async def test_md_g22_a_chip_or_a_bare_number_closes_it_without_a_model(db, door, reply, size, outcome):
    profile, provider, created = await _rebased(db)
    out = await _review_turn(db, door, created.session_id, provider, reply)
    await db.refresh(profile)
    assert provider.aparse_json.await_count == 0
    assert out["interpreted"]["outcome"] == outcome
    assert _state(profile) == (size, [])


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
async def test_md_g22_twice_in_a_row_answers_outcome_required(db, door):
    """The one re-ask is spent; the second sentence answers ``outcome_required``
    (agent: an error), still with no model and nothing written (ADR-099,
    unchanged)."""
    profile, provider, created = await _rebased(db, lang="de")
    await _review_turn(db, door, created.session_id, provider, "Es sind jetzt 52")
    with pytest.raises(Exception) as err:
        await _review_turn(db, door, created.session_id, provider, "Nicht 45")
    assert "outcome" in str(err.value).lower() or type(err.value).__name__ == "OutcomeRequired"
    await db.refresh(profile)
    assert provider.aparse_json.await_count == 0
    assert _state(profile) == (50, [(50, 45)])


def test_v6_the_size_re_ask_line_has_both_languages_and_says_the_number_works():
    from applire.services.profile.decisions import _SIZE_FOLLOW_UP, default_follow_up, size_follow_up

    assert set(_SIZE_FOLLOW_UP) == {"de", "en"}
    assert size_follow_up("de") == ("Ich habe deine Antwort nicht eindeutig zuordnen können. "
                                    "Wähle eine der Optionen unten oder antworte nur mit der Zahl.")
    assert size_follow_up("en") == ("I could not tell which option you mean. "
                                    "Pick one of the options below, or reply with just the number.")
    assert size_follow_up("fr") == size_follow_up("en")
    assert size_follow_up("de") != default_follow_up("de")


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
@pytest.mark.parametrize(("reply", "size", "outcome"), [("52", 52, "manual"), ("45", 45, "take_new"),
                                                        ("Aktuellen behalten: 50", 50, "keep_current")])
async def test_v6_after_the_re_ask_a_bare_number_or_the_chip_is_taken(db, door, reply, size, outcome):
    """The line says "reply with just the number": after the one re-ask that
    reply is taken — not ``outcome_required``."""
    from applire.services.profile.decisions import size_follow_up

    profile, provider, created = await _rebased(db, lang="de")
    first = await _review_turn(db, door, created.session_id, provider, "Es sind jetzt 52")
    assert first["question"].startswith((size_follow_up("de"), size_follow_up("en")))
    out = await _review_turn(db, door, created.session_id, provider, reply)
    await db.refresh(profile)
    assert provider.aparse_json.await_count == 0
    assert out["interpreted"]["outcome"] == outcome
    assert _state(profile) == (size, [])


@pytest.mark.parametrize(("reply", "decision"), [
    ("Keep current: 50", "existing"), ("45", "incoming"), ("50", "existing"),
    ("52", "unclear"), ("Bisher behalten", "unclear"), ("Neu übernehmen", "unclear"), ("Nicht 45", "unclear"),
])
def test_md_g22_interpret_conflict_answer_on_a_size_reads_only_the_chip_or_a_side(reply, decision):
    """The shared matcher, asked about a size, answers by MD-G22 too (a bare
    third number is "unclear" there: only the session writes ``manual``)."""
    from applire.services.interview_graph import interpret_conflict_answer

    assert interpret_conflict_answer(reply, 50, 45, choices=CHIPS_EN, field="team_size") == decision


# ── NF-18: a typed size is a size ───────────────────────────────────────────


@pytest.mark.parametrize(("value", "size"), [
    (45, 45), ("45", 45), (" 45 ", 45), ("45.0", 45), (45.0, 45), ("+45", 45), ("045", 45), ("٤٥", 45),
    (True, None), (False, None), (0, None), ("0", None), (-3, None), ("-3", None), ("1e3", None),
    ("ca. 45", None), ("45 Personen", None), ("achtunddreißig", None), ("45.5", None), (45.5, None),
    ("4 5", None), (None, None), ("", None),
])
def test_nf18_the_size_reader(value, size):
    from applire.services.profile.reconcile.apply import readable_size

    assert readable_size(value) == size


def test_nf18_the_upper_bound_is_a_named_constant():
    from applire.services.profile.reconcile.apply import MAX_TEAM_SIZE, readable_size

    assert readable_size(MAX_TEAM_SIZE) == MAX_TEAM_SIZE
    assert readable_size(MAX_TEAM_SIZE + 1) is None
    assert readable_size(str(MAX_TEAM_SIZE + 1)) is None
    assert readable_size(10**12) is None


@pytest.mark.parametrize("incoming", [10**12, "1e3", "1000000000000"])
def test_nf18_flag_time_applies_the_bound_and_refuses_an_exponent(incoming):
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import FlagConflict

    out = apply_ops(_model(_vault(dispute=None)),
                    [FlagConflict(target=W, field="team_size", existing="38", incoming=incoming)],
                    "agent_interview")
    assert out.conflicts == []
    assert [i.reason for i in out.not_applied] == ["op_rejected"]


_BAD_MANUAL = ["achtunddreißig", "45 Personen", "true", True, 0, "0", -3, "-3", "1000000000000", "1e3"]


def _plain(text: str, value) -> None:
    assert "team size" in text and str(value) in text
    for raw in ("int_parsing", "validation error", "pydantic", "MasterProfileData"):
        assert raw not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("value", _BAD_MANUAL)
async def test_nf18_rest_manual_refuses_with_the_plain_message(db, value):
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
    _plain(str(err.value.detail), value)
    await db.refresh(profile)
    assert _state(profile) == (38, [(38, 45)])


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "stored"), [("52", 52), (52, 52), ("+52", 52), ("52.0", 52), (" 52 ", 52), ("٥٢", 52), ("052", 52), (52.0, 52)])
async def test_nf18_a_readable_manual_size_is_written_as_a_number(db, value, stored):
    from applire.services.profile import resolve_conflict

    profile = await _seed_profile(db, _vault())
    await resolve_conflict("c-1", "manual", value, db, user_id=profile.user_id)
    await db.refresh(profile)
    assert _state(profile) == (stored, [])


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["0", "-3", "1000000000000"])
async def test_nf18_the_agent_door_gets_the_plain_message_for_a_bare_non_size(db, reply):
    """Through ``mcp.call_tool``: the error TEXT is the plain message."""
    profile, provider, created = await _rebased(db)
    with pytest.raises(Exception) as err:
        await _review_turn(db, "agent", created.session_id, provider, reply)
    _plain(str(err.value), reply)
    await db.refresh(profile)
    assert provider.aparse_json.await_count == 0
    assert _state(profile) == (50, [(50, 45)])


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["0", "-3", "1000000000000"])
async def test_nf18_the_ui_review_route_refuses_with_the_plain_message(db, reply):
    from fastapi import HTTPException

    from applire.routers.session import post_message
    from applire.schemas.session import SessionMessageRequest

    profile, provider, created = await _rebased(db, lang="de")
    with pytest.raises(HTTPException) as err:
        await post_message(created.session_id, SessionMessageRequest(message=reply), db, provider,
                           MagicMock(id=profile.user_id))
    assert err.value.status_code in (409, 422)
    _plain(str(err.value.detail), reply)
    await db.refresh(profile)
    assert _state(profile) == (50, [(50, 45)])


@pytest.mark.asyncio
async def test_nf18_after_a_refusal_the_dispute_still_answers(db):
    from applire.services.profile import resolve_conflict

    profile = await _seed_profile(db, _vault())
    with pytest.raises(ValueError):
        await resolve_conflict("c-1", "manual", "0", db, user_id=profile.user_id)
    await resolve_conflict("c-1", "manual", "52", db, user_id=profile.user_id)
    await db.refresh(profile)
    assert _state(profile) == (52, [])


# ── non-size disputes: a negation is not read by the word matcher ───────────


@pytest.mark.parametrize(("reply", "field", "existing", "incoming"), [
    ("Nicht ändern", "location", "Neuwied", "Andernach"),
    ("Nicht behalten", "location", "Neuwied", "Andernach"),
    ("Don't keep the current one", "location", "Neuwied", "Andernach"),
    ("Bitte nicht die alten Werte behalten", "location", "Neuwied", "Andernach"),
    ("Nicht ändern", "budget", "120", "150"),
    ("Never change it", "budget", "120", "150"),
    ("Kein neuer Wert, behalten", "budget", "120", "150"),
])
def test_md_g22_a_negation_is_not_decided_by_the_word_matcher(reply, field, existing, incoming):
    from applire.services.interview_graph import interpret_conflict_answer

    assert interpret_conflict_answer(reply, existing, incoming, field=field) == "unclear"
    assert interpret_conflict_answer(reply, existing, incoming) == "unclear"


@pytest.mark.parametrize(("reply", "decision"), [
    ("Keep current: Bonn", "existing"), ("Use the new value: Köln", "incoming"), ("Neuwied", "existing"),
    ("Behalten", "existing"), ("Neu übernehmen", "incoming"),
])
def test_md_g22_a_non_size_reply_without_a_negation_reads_as_before(reply, decision):
    from applire.services.interview_graph import interpret_conflict_answer

    assert interpret_conflict_answer(reply, "Neuwied", "Andernach", field="location") == decision


async def create_review(db, provider, profile, lang="de"):
    from applire.services.session import create_profile_review_session

    return await create_profile_review_session(db, provider, lang=lang, user_id=profile.user_id)


def _location_vault():
    data = _vault(dispute=None)
    data["work_experience"][0]["location"] = "Neuwied"
    data["metadata"]["pending_conflicts"] = [{
        "conflict_id": "l-1", "section": "work_experience", "field": "location", "entity_id": W,
        "existing_value": "Neuwied", "incoming_value": "Andernach", "source": "agent_interview"}]
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
@pytest.mark.parametrize("reply", ["Nicht ändern", "Don't change it"])
async def test_md_g22_a_negated_non_size_reply_goes_to_the_model(db, door, reply):
    """"Nicht ändern" on a location dispute no longer picks the new value by
    the word "ändern": the existing model reading answers (here: keep)."""
    from applire.services.session import create_profile_review_session

    profile = await _seed_profile(db, _location_vault())
    provider = MagicMock()
    provider.aparse_json = AsyncMock(
        return_value={"outcome": "keep_current", "corrected_value": None, "follow_up": None})
    provider.acomplete = AsyncMock(side_effect=AssertionError("no free-text call"))
    created = await create_profile_review_session(db, provider, lang="de", user_id=profile.user_id)
    provider.aparse_json.reset_mock()
    out = await _review_turn(db, door, created.session_id, provider, reply)
    await db.refresh(profile)
    assert provider.aparse_json.await_count == 1, "the reply reaches the model reading"
    assert out["interpreted"]["outcome"] == "keep_current"
    assert _model(profile.profile_json).work_experience[0].location == "Neuwied"


@pytest.mark.asyncio
@pytest.mark.parametrize("door", ["ui", "agent"])
async def test_v6_a_non_size_dispute_keeps_the_existing_re_ask_line(db, door):
    from applire.services.profile.decisions import default_follow_up, size_follow_up

    profile = await _seed_profile(db, _location_vault())
    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value={"outcome": None, "corrected_value": None, "follow_up": None})
    provider.acomplete = AsyncMock(side_effect=AssertionError("no free-text call"))
    created = await create_review(db, provider, profile)
    out = await _review_turn(db, door, created.session_id, provider, "Nicht ändern")
    assert out["question"].startswith((default_follow_up("de"), default_follow_up("en")))
    assert not out["question"].startswith((size_follow_up("de"), size_follow_up("en")))


@pytest.mark.asyncio
async def test_v6_a_typed_merge_on_a_bullet_dispute_keeps_the_existing_re_ask_line(db):
    """The session's own fallback line (a typed "merge" needs the sentence
    confirmed): still the general line on a non-size dispute."""
    from applire.services.profile.decisions import default_follow_up, size_follow_up
    from applire.services.session import send_message

    data = _vault(dispute=None)
    data["work_experience"][0]["achievements"] = ["Introduced a supplier scorecard."]
    data["metadata"]["pending_conflicts"] = [{
        "conflict_id": "b-1", "section": "work_experience", "field": "achievements", "entity_id": W,
        "existing_value": "Introduced a supplier scorecard.",
        "incoming_value": "Merged 12 reporting tools into one lakehouse.", "source": "cv_upload"}]
    profile = await _seed_profile(db, data)
    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value={"outcome": "merge", "corrected_value": None, "follow_up": None})
    provider.acomplete = AsyncMock(side_effect=AssertionError("no free-text call"))
    created = await create_review(db, provider, profile, lang="en")
    out = await send_message(created.session_id, "Fasse beide zusammen", db, provider)
    assert out.complete is False
    assert out.question.startswith((default_follow_up("de"), default_follow_up("en")))
    assert not out.question.startswith((size_follow_up("de"), size_follow_up("en")))
