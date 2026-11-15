# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WP-Q4 (g), Sorbet quality-1 — a BRIDGE answer idea is a question with an
OPENING, never a claim (ADR-064 amended 2026-11-15, founder question Q4-1).

UAT-2 drafted a bridge as a one-click first-person hypothesis ("Als Schichtleiter
habe ich … auch an der Mengenprognose ausgerichtet"); a click sent it as the
candidate's own claim. Replayed on Luna (136 captured draft calls, blind judge):
53 % of bridges stated more than the record held. The rule now asks for an
opening that names anchor + concept and ends with a colon (2 % of bridges).

Pinned here:
1. the prompt rule (both MODE A and the transfer probe share it);
2. ``choice_grounding.answer_is_offered_opening`` — a FACT: the answer is
   exactly an offered bridge opening (normalised, the same containment form the
   G-3 cut uses); typed words beside it, an edited opening, a direct/denial
   choice or a sentence-shaped bridge are not;
3. the session seam — such an answer raises before anything is appended or
   reconciled (the reconcile bridge is never awaited);
4. the router seam — 422 ``answer_is_opening``, a structured body.

Synthetic data only.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from test_b6_a_offered_choice_not_a_denial import _send, db  # noqa: E402,F401

OPENING = "Mengenprognosen in der Personaleinsatzplanung als Schichtleiter:"
SENTENCE_BRIDGE = (
    "Als Schichtleiter habe ich die Personaleinsatzplanung auch an der wöchentlichen "
    "Mengenprognose ausgerichtet."
)
DENIAL = "Mit Prognosen habe ich bisher nicht gearbeitet, aber durch die Schichtplanung kenne ich Mengenvorgaben."


def _offered(**extra):
    out = {
        OPENING: {"level": "partial", "names_without_claiming": [], "ask": "Hast du …?"},
        DENIAL: {"level": "denial", "names_without_claiming": ["Prognosen"], "ask": "Kennst du …?"},
    }
    out.update(extra)
    return out


# 1. the rule -----------------------------------------------------------------

def test_the_bridge_rule_asks_for_an_opening_never_a_claim():
    from applire.prompts.interview import (
        DENIAL_PROBE_QUESTION_SYSTEM_PROMPT,
        QUESTION_SYSTEM_PROMPT,
        _CHOICE_COVERAGE_RULES,
    )

    rules = _CHOICE_COVERAGE_RULES
    assert "a QUESTION, never an answer" in rules
    assert "ends with a colon" in rules
    assert OPENING in rules  # the generic example is the opening shape
    assert "modest first-person hypothesis" not in rules
    assert "a BRIDGE's \"text\" is the one exception" in rules
    for prompt in (QUESTION_SYSTEM_PROMPT, DENIAL_PROBE_QUESTION_SYSTEM_PROMPT):
        assert rules in prompt


# 2. the fact ------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    OPENING,
    OPENING.rstrip(":"),
    "  " + OPENING.replace(" ", "  ") + "  ",
    OPENING.replace(":", " :"),
])
def test_an_unchanged_opening_is_the_offered_opening(answer):
    from applire.services.choice_grounding import answer_is_offered_opening

    assert answer_is_offered_opening(answer, _offered()) is True


@pytest.mark.parametrize("answer", [
    OPENING + " Ja, im Wochenrhythmus nach der Prognose des Vertriebs.",
    "Nein. " + OPENING,
    "Mengenprognosen in der Schichtplanung als Schichtleiter:",  # edited
    DENIAL,  # a denial-level choice is a statement, not an opening
    "Ja.",
    "",
])
def test_an_answer_with_own_words_or_another_choice_is_not(answer):
    from applire.services.choice_grounding import answer_is_offered_opening

    assert answer_is_offered_opening(answer, _offered()) is False


def test_a_sentence_bridge_or_a_colon_choice_at_another_level_is_not_an_opening():
    from applire.services.choice_grounding import answer_is_offered_opening

    direct_colon = "Meine Begleitung der ISO-9001-Audits als Produktionsleiter:"
    offered = _offered(**{
        SENTENCE_BRIDGE: {"level": "partial", "names_without_claiming": []},
        direct_colon: {"level": "direct", "names_without_claiming": []},
    })
    assert answer_is_offered_opening(SENTENCE_BRIDGE, offered) is False
    assert answer_is_offered_opening(direct_colon, offered) is False


@pytest.mark.parametrize("offered", [None, {}, [], "x", {OPENING: "not a dict"}])
def test_malformed_offers_are_never_an_opening(offered):
    from applire.services.choice_grounding import answer_is_offered_opening

    assert answer_is_offered_opening(OPENING, offered) is False


# 3. the session seam ----------------------------------------------------------

@pytest.mark.asyncio
async def test_session_turn_refuses_an_unchanged_opening_before_any_write(db):  # noqa: F811
    from applire.exceptions import AnswerIsOfferedOpening

    with pytest.raises(AnswerIsOfferedOpening):
        await _send(db, OPENING, _offered())


@pytest.mark.asyncio
async def test_session_turn_reconciles_a_finished_opening(db):  # noqa: F811
    got = await _send(db, OPENING + " Ja, wöchentlich nach der Vertriebsprognose.", _offered())
    assert got == [{"text": OPENING, "names_without_claiming": [], "states_no_to": []}]


# 4. the router seam -----------------------------------------------------------

@pytest.mark.asyncio
async def test_router_answers_422_answer_is_opening():
    from fastapi import HTTPException

    from applire.exceptions import AnswerIsOfferedOpening
    from applire.routers import session as router
    from applire.schemas.session import SessionMessageRequest

    with patch.object(router, "send_message", new=AsyncMock(side_effect=AnswerIsOfferedOpening())):
        with pytest.raises(HTTPException) as exc:
            await router.post_message(
                session_id=__import__("uuid").uuid4(),
                body=SessionMessageRequest(message=OPENING),
                db=None, provider=None, user=type("U", (), {"id": None})(),
            )
    assert exc.value.status_code == 422
    assert exc.value.detail["error_code"] == "answer_is_opening"
    assert exc.value.detail["turn_state"] == "not_saved"
