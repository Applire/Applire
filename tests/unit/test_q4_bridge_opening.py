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
    # MD-Q7 F5: the model names concept + anchor and writes no bridge text.
    assert 'Do NOT write its "text"' in rules
    assert '"concept": the asked concept exactly as the GAP CLUSTER block' in rules
    assert '"anchor": the anchor in the profile\'s own words' in rules
    assert "modest first-person hypothesis" not in rules
    assert 'a BRIDGE has no "text" of its own' in " ".join(rules.split())
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


# 5. MD-Q7 F5 (adversarial adv-1b) — the bridge opening is BUILT by code ------

F5_PROBE = "Ich habe als QA Engineer Azure-Systeme qualifiziert und 5 Mitarbeitende geführt:"
F5_CLUSTER = {"id": "c1", "label": "Azure", "gaps": ["Azure"], "jd_skills": ["Azure"]}
F5_PROFILE = {
    "skills": [{"name": "GxP-Validierung", "category": "technical"}],
    "work_experience": [{
        "company": "Veltrup Diagnostik GmbH", "role": "QA Engineer",
        "responsibilities": ["Computersystemvalidierung im Labor verantwortet"],
    }],
}


def _build(choices, lang="de", cluster=F5_CLUSTER, profile=F5_PROFILE):
    from applire.services.choice_grounding import build_bridge_openings

    return build_bridge_openings(choices, cluster, profile, lang)


def test_f5_the_probe_a_model_written_claim_at_partial_level_is_dropped():
    assert _build([{"text": F5_PROBE, "level": "partial"}]) is None


def test_f5_with_concept_and_anchor_the_text_is_codes_never_the_models():
    out = _build([{"text": F5_PROBE, "level": "partial", "concept": "azure", "anchor": "QA Engineer",
                   "ask": "Hast du als QA Engineer Azure-Systeme qualifiziert?"}])
    assert [c["text"] for c in out] == ["Zu Azure bei QA Engineer:"]
    assert out[0]["ask"] == "Hast du als QA Engineer Azure-Systeme qualifiziert?"
    en = _build([{"text": "", "level": "partial", "concept": "Azure", "anchor": "QA Engineer"}], lang="en")
    assert en[0]["text"] == "On Azure in QA Engineer:"


@pytest.mark.parametrize("concept, anchor", [
    ("Kubernetes", "QA Engineer"),                     # not an asked concept
    ("Azure", "Azure Architect"),                      # not the profile's words
    ("Azure", "Veltrup Diagnostik"),                   # the employer's name
    ("Azure", "Computersystemvalidierung im Labor verantwortet " * 3),  # a sentence
    ("Azure", "QA Engineer: 5 Mitarbeitende"),         # a colon smuggles a claim
    ("", "QA Engineer"),
    ("Azure", None),
])
def test_f5_a_bridge_without_an_asked_concept_and_a_profile_anchor_is_dropped(concept, anchor):
    assert _build([{"text": "", "level": "partial", "concept": concept, "anchor": anchor}]) is None


def test_f5_an_anchor_from_a_bullet_that_ends_in_a_full_stop_is_kept():
    """Replay finding (Luna, 2026-11-15): 77/270 bridges named a bullet anchor
    verbatim and were dropped by a space-bounded match on "…Fertigung."."""
    profile = {"work_experience": [{"company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
                                    "responsibilities": ["Mitarbeit bei der Einführung von SAP in der Fertigung."]}]}
    cluster = {"label": "Produktionsdigitalisierung", "gaps": ["Maschinendatenerfassung"], "jd_skills": []}
    out = _build([{"text": "", "level": "partial", "concept": "Maschinendatenerfassung",
                   "anchor": "Einführung von SAP in der Fertigung"}], cluster=cluster, profile=profile)
    assert out[0]["text"] == "Zu Maschinendatenerfassung bei Einführung von SAP in der Fertigung:"
    # still word-bounded: a fragment of a word is not an anchor
    assert _build([{"text": "", "level": "partial", "concept": "Maschinendatenerfassung",
                    "anchor": "führung von SAP"}], cluster=cluster, profile=profile) is None


def test_f5_other_levels_pass_unchanged_and_a_dropped_bridge_leaves_them():
    direct = {"text": "Ich habe Computersystemvalidierung verantwortet.", "level": "direct"}
    denial = {"text": "Azure habe ich bisher nicht genutzt, aber …", "level": "denial"}
    assert _build([direct, {"text": F5_PROBE, "level": "partial"}, denial]) == [direct, denial]


def test_f5_the_named_concept_and_anchor_survive_a_language_review_that_drops_them():
    from applire.services.interview_graph import _carry_levels_by_index

    pre = [{"text": "", "level": "partial", "concept": "Azure", "anchor": "QA Engineer", "ask": "?"}]
    carried = _carry_levels_by_index(pre, [{"text": "translated", "level": "partial", "ask": "?"}])
    assert carried[0]["concept"] == "Azure" and carried[0]["anchor"] == "QA Engineer"
    bare = _carry_levels_by_index(pre, ["translated"])
    assert bare[0]["concept"] == "Azure" and bare[0]["anchor"] == "QA Engineer"


_LONG_BULLET = (
    "Computersystemvalidierung für zwölf Laborsysteme nach GAMP 5 mit Risikoanalyse, "
    "Testplanung und Abschlussbericht verantwortet"
)


@pytest.mark.parametrize("anchor", [_LONG_BULLET, "Leitung: Qualitätssicherung"])
def test_f5_a_profile_anchor_that_is_a_sentence_or_carries_a_colon_is_dropped(anchor):
    profile = {"work_experience": [{"company": "Veltrup Diagnostik GmbH", "role": "QA Engineer",
                                    "responsibilities": [_LONG_BULLET, "Leitung: Qualitätssicherung"]}]}
    assert len(_LONG_BULLET) > 80
    assert _build([{"text": "", "level": "partial", "concept": "Azure", "anchor": anchor}],
                  profile=profile) is None


# 6. MD-Q7 F9 — the opening is stripped before reconcile; a bare completion is
# asked to be completed; only the CURRENT question's offers count ------------

@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [OPENING + " ja", OPENING + " Ja.", OPENING + " ok", OPENING + "  yes!"])
async def test_f9_the_probe_an_opening_with_a_bare_yes_is_asked_to_be_completed(db, answer):  # noqa: F811
    from applire.exceptions import AnswerIsOfferedOpening

    with pytest.raises(AnswerIsOfferedOpening):
        await _send(db, answer, _offered())


@pytest.mark.asyncio
async def test_f9_the_reconciler_reads_only_what_the_person_wrote_after_the_opening(db):  # noqa: F811
    kw = await _send(
        db, OPENING + " Ja, wöchentlich nach der Vertriebsprognose.",
        _offered(), full=True,
    )
    assert kw["answer"] == "Ja, wöchentlich nach der Vertriebsprognose."
    assert OPENING.lower() not in kw["answer"].lower()


def test_f9_strip_cuts_the_opening_from_the_persons_own_text():
    from applire.services.choice_grounding import is_bare_completion, strip_offered_opening

    rest, had = strip_offered_opening(f"  {OPENING} Ja, wöchentlich.", _offered())
    assert had and rest == "Ja, wöchentlich."
    assert not is_bare_completion(rest)
    assert strip_offered_opening("Ich plane wöchentlich.", _offered()) == ("Ich plane wöchentlich.", False)
    # a denial-level choice is a statement of the person's, never cut
    assert strip_offered_opening(DENIAL + " Stimmt.", _offered())[1] is False
    for bare in ("", " ", "ja", "Ja!", "OK.", "yes"):
        assert is_bare_completion(bare)
    assert not is_bare_completion("ja, seit 2021")


@pytest.mark.asyncio
async def test_f9_an_earlier_questions_opening_is_cut_too(db):  # noqa: F811
    """The kept offers of an earlier question are Applire's text as much as
    this question's: the opening is cut and a bare completion is refused."""
    import test_b6_a_offered_choice_not_a_denial as b6
    from applire.exceptions import AnswerIsOfferedOpening

    real = b6._session_state

    def state(job_id, profile_id, off):
        st = real(job_id, profile_id, off)
        st["current_choices"] = [DENIAL]  # the opening belonged to an earlier question
        return st

    with patch.object(b6, "_session_state", new=state):
        with pytest.raises(AnswerIsOfferedOpening):
            await b6._send(db, OPENING + " ja", _offered())
        kw = await b6._send(db, OPENING + " Ja, seit 2021.", _offered(), full=True)
    assert kw["answer"] == "Ja, seit 2021."


# 7. MD-Q9 R8 — only a LEADING opening in the chip's exact form is cut ---------

R8_OPENING = "Zu MES bei Fertigungsmeister:"
R8_OFFERED = {R8_OPENING: {"level": "partial", "names_without_claiming": [], "ask": "Hast du …?"}}


@pytest.mark.parametrize("answer", [
    # the reviewer's probes (adv-1b re-check at 7017f7ab, R8)
    "Ja. Zu MES bei Fertigungsmeister habe ich Schulungen gehalten.",
    "Ich habe zu MES bei Fertigungsmeister: Schulungen gehalten und 12 Leute geführt.",
    "Mein Zu MES bei Fertigungsmeister ist gut",
    # a leading phrase without the colon is the person's own sentence
    "Zu MES bei Fertigungsmeister habe ich Schulungen gehalten.",
    # not the exact form the chip inserted
    "zu mes bei fertigungsmeister: Schulungen gehalten.",
])
def test_r8_the_persons_own_words_are_never_cut(answer):
    from applire.services.choice_grounding import strip_offered_opening

    assert strip_offered_opening(answer, R8_OFFERED) == (answer, False)


def test_r8_a_leading_opening_in_the_chips_form_is_cut():
    from applire.services.choice_grounding import strip_offered_opening

    assert strip_offered_opening(
        "  Zu MES bei Fertigungsmeister: Schulungen für 12 Leute gehalten.", R8_OFFERED
    ) == ("Schulungen für 12 Leute gehalten.", True)


@pytest.mark.asyncio
async def test_r8_the_reconciler_reads_the_middle_phrase_whole(db):  # noqa: F811
    answer = "Ja. " + OPENING + " habe ich wöchentlich geplant."
    kw = await _send(db, answer, _offered(), full=True)
    assert kw["answer"] == answer
