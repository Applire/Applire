# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WP-Q6 (a), Sorbet quality-1 wave 2a — no answer idea states a no
(ADR-064 amended 2026-11-15, founder ruling Q6-1 = A).

The honest bridge ("Mit OEE habe ich bisher nicht gearbeitet, aber durch …")
was a one-click denial. Replayed on Luna over 136 captured interview_draft
calls: all 91 drafted honest nos were delivered and 62 were false by CV +
dossier; only 5 of those 62 had evidence in the call's own input. The slot now
keeps its button question and inserts only "Zu <Konzept>:" built by code.

Pinned here:
1. the prompt rule (both generator prompts share it): no choice states a no;
2. ``build_bridge_openings`` turns a denial-level draft into a partial-level
   opening from the cluster's own spelling of its concept, at most one, never
   on a concept already answered;
3. the opening guards apply to it unchanged (refused unchanged, cut as prefix);
4. both generator paths (MODE A and the transfer probe) deliver it.

Synthetic data only.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

from test_b6_a_offered_choice_not_a_denial import _send, db  # noqa: E402,F401

CLUSTER = {
    "id": "c-dig", "label": "Produktionsdigitalisierung",
    "gaps": ["OEE", "MES", "Maschinendatenerfassung"], "jd_skills": ["OEE", "MES-Systeme"],
}
PROFILE = {
    "skills": [{"name": "Shopfloor-Management"}, {"name": "Feinplanung/Fertigungssteuerung"}],
    "work_experience": [{
        "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
        "responsibilities": ["Einführung von Shopfloor-Management und KVP-Routinen."],
    }],
}
# The captured shape (Q4 F5 replay, Stefan): the old honest bridge as drafted.
CAPTURED_NO = (
    "Mit OEE habe ich bisher nicht gearbeitet, aber durch meine Verantwortung für zwei "
    "Fertigungsbereiche kenne ich Ausschussquote und Termintreue als zentrale Produktionskennzahlen."
)
ASK = "Kennst du OEE vielleicht aus deiner Verantwortung für zwei Fertigungsbereiche?"


def _build(choices, lang="de", cluster=CLUSTER, profile=PROFILE):
    from applire.services.choice_grounding import build_bridge_openings

    return build_bridge_openings(choices, cluster, profile, lang)


# 1. the rule ------------------------------------------------------------------

def test_the_rule_offers_no_no_and_the_honest_slot_writes_no_text():
    from applire.prompts.interview import (
        DENIAL_PROBE_QUESTION_SYSTEM_PROMPT,
        QUESTION_SYSTEM_PROMPT,
        _CHOICE_COVERAGE_RULES,
    )

    rules = " ".join(_CHOICE_COVERAGE_RULES.split())
    assert "HONEST QUESTION" in rules and "HONEST BRIDGE" not in rules
    assert "habe ich bisher nicht" not in rules, "no example no is left to copy"
    assert "not even an earlier no of theirs" in rules
    assert '"denied_terms": [] on every choice.' in rules
    assert 'Do NOT write its "text" (leave it ""): Applire writes the opening the candidate finishes' in rules
    for prompt in (QUESTION_SYSTEM_PROMPT, DENIAL_PROBE_QUESTION_SYSTEM_PROMPT):
        assert _CHOICE_COVERAGE_RULES in prompt
        assert '"concept": "bridge and honest question only"' in prompt


def test_the_user_prompt_hint_names_the_honest_question_not_a_no():
    from applire.prompts.interview import build_question_prompt

    prompt = build_question_prompt(CLUSTER, PROFILE, [], gap_category="C")
    assert "the one honest question on exactly that concept" in prompt
    assert "whose no names" not in prompt


# 2. the build -----------------------------------------------------------------

def test_the_captured_honest_no_becomes_an_opening_with_its_ask_kept():
    out = _build([{"ask": ASK, "text": CAPTURED_NO, "level": "denial", "denied_terms": ["OEE"], "concept": "oee"}])
    assert out == [{"ask": ASK, "text": "Zu OEE:", "level": "partial", "denied_terms": [], "concept": "OEE"}]
    en = _build([{"ask": "?", "text": "", "level": "denial", "concept": "MES"}], lang="en")
    assert en[0]["text"] == "On MES:"


def test_a_pre_q6_draft_without_concept_uses_its_one_declared_term():
    out = _build([{"ask": ASK, "text": CAPTURED_NO, "level": "denial", "denied_terms": ["OEE"]}])
    assert out[0]["text"] == "Zu OEE:"
    # two declared terms name no single concept: dropped, never guessed
    assert _build([{"text": CAPTURED_NO, "level": "denial", "denied_terms": ["OEE", "MES"]}]) is None


@pytest.mark.parametrize("concept", ["Kubernetes", "", None, "OEE-Kennzahlen"])
def test_a_concept_that_is_no_cluster_term_is_dropped(concept):
    assert _build([{"text": CAPTURED_NO, "level": "denial", "concept": concept}]) is None


@pytest.mark.parametrize("field", ["covered", "declined"])
def test_a_concept_the_record_shows_answered_is_not_asked_again(field):
    cluster = {**CLUSTER, "outcome": {field: ["OEE"]}}
    assert _build([{"text": "", "level": "denial", "concept": "OEE"}], cluster=cluster) is None


def test_at_most_one_honest_opening_and_no_duplicate_text():
    out = _build([
        {"text": "", "level": "denial", "concept": "OEE"},
        {"text": "", "level": "denial", "concept": "MES"},
        {"text": "", "level": "partial", "concept": "OEE", "anchor": "Produktionsleiter"},
        {"text": "", "level": "partial", "concept": "OEE", "anchor": "Produktionsleiter"},
    ])
    assert [c["text"] for c in out] == ["Zu OEE:", "Zu OEE bei Produktionsleiter:"]


def test_no_delivered_choice_is_a_denial_after_the_filter():
    from applire.services.choice_grounding import filter_ungrounded_choices

    built = _build([
        {"text": "", "level": "partial", "concept": "MES", "anchor": "Produktionsleiter"},
        {"ask": ASK, "text": CAPTURED_NO, "level": "denial", "denied_terms": ["OEE"], "concept": "OEE"},
    ])
    assert {c["level"] for c in built} == {"partial"}
    delivered = filter_ungrounded_choices(built, CLUSTER, PROFILE, "C")
    assert delivered == ["Zu MES bei Produktionsleiter:", "Zu OEE:"]


# 3. the guards apply unchanged -------------------------------------------------

def _offered_from_build():
    from applire.services.choice_grounding import filter_ungrounded_choices, record_offered_choices

    built = _build([{"ask": ASK, "text": CAPTURED_NO, "level": "denial", "concept": "OEE"}])
    state: dict = {}
    record_offered_choices(state, built, filter_ungrounded_choices(built, CLUSTER, PROFILE, "C"))
    return state["offered_choice_terms"]


def test_the_recorded_offer_is_a_partial_opening_naming_nothing():
    assert _offered_from_build() == {"Zu OEE:": {"level": "partial", "names_without_claiming": [], "ask": ASK,
                                                "concept": "OEE"}}


def test_the_unchanged_opening_is_refused_and_a_finished_one_is_cut():
    from applire.services.choice_grounding import answer_is_offered_opening, strip_offered_opening

    offered = _offered_from_build()
    assert answer_is_offered_opening("Zu OEE:", offered)
    assert strip_offered_opening("Zu OEE: habe ich bisher nicht gearbeitet.", offered) == (
        "habe ich bisher nicht gearbeitet.", True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["Zu OEE:", "Zu OEE: ja"])
async def test_the_session_turn_refuses_the_opening_alone(db, answer):  # noqa: F811
    from applire.exceptions import AnswerIsOfferedOpening

    with pytest.raises(AnswerIsOfferedOpening):
        await _send(db, answer, _offered_from_build())


@pytest.mark.asyncio
async def test_the_reconciler_reads_the_persons_own_no(db):  # noqa: F811
    kw = await _send(db, "Zu OEE: Damit habe ich bisher nicht gearbeitet.", _offered_from_build(), full=True)
    assert kw["answer"] == "Damit habe ich bisher nicht gearbeitet."


# 4. both generator paths ---------------------------------------------------------

def _provider():
    provider = AsyncMock()
    provider.aparse_json.return_value = {
        "question": "Welche Erfahrung hast du mit OEE, MES oder Maschinendatenerfassung?",
        "choices": [
            {"ask": "Hast du als Produktionsleiter MES eingeführt?", "text": "", "level": "partial",
             "denied_terms": [], "concept": "MES", "anchor": "Produktionsleiter"},
            {"ask": ASK, "text": CAPTURED_NO, "level": "denial", "denied_terms": ["OEE"], "concept": "OEE"},
        ],
    }
    return provider


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", [False, True])
async def test_both_generator_paths_deliver_the_honest_opening(probe):
    from applire.services import interview_graph as g

    state = {"mode": "targeted", "critical_gaps": ["c-dig"], "current_gap_index": 0,
             "gap_clusters_by_id": {"c-dig": CLUSTER}, "messages": []}
    extra = {"follow_up_hint": "OEE", "denial_probe": True} if probe else {}
    with patch.object(g, "_review_question_language", new=AsyncMock(side_effect=lambda d, *a, **k: d)):
        out = await g.question_generator_with_profile(
            state, PROFILE, _provider(), gap_category="C", lang="de", **extra
        )
    assert out["choices"] == ["Zu MES bei Produktionsleiter:", "Zu OEE:"]
    assert all(m["level"] == "partial" and m["names_without_claiming"] == []
               for m in state["offered_choice_terms"].values())
    assert CAPTURED_NO not in state["offered_choice_terms"]


# 5. MD-Q11 F8: a clicked opening carries its concept to the reconcile -------------
#
# Luna replay of the captured multi-concept question (E2E run 3, record 189; the
# question names Maschinendatenerfassung, MES, OEE, Industrie 4.0) with "Zu OEE:"
# cut: "nein" → 1/3 denied all FIVE concepts, 2/3 recorded nothing; "nein, aber …"
# → 3/3 denied all five; "ja, intensiv, seit 2022 im MES-Projekt" → 3/3 OEE never
# named. With ``answers_concept`` + the reconcile rule: 9/9 exactly OEE (denial or
# named fact).

@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["Zu OEE: nein", "Zu OEE: ja, intensiv, seit 2022 im MES-Projekt",
                                    "Zu OEE: nein, aber Ausschussquote und Termintreue"])
async def test_f8_the_reconcile_hears_which_concept_the_clicked_opening_asked(db, answer):  # noqa: F811
    kw = await _send(db, answer, _offered_from_build(), full=True)
    assert kw["answer"] == answer.split(": ", 1)[1]
    assert [c.get("answers_concept") for c in kw["offered_choices"]] == ["OEE"]


@pytest.mark.asyncio
async def test_f8_the_bridge_puts_the_concept_into_new_information():
    from applire.services.profile.reconcile import interview_bridge as ib

    captured = {}

    async def _stop(before, new_info, *_a, **_k):
        captured.update(new_info)
        raise RuntimeError("stop")

    record = MagicMock()
    record.profile_json = {}
    with patch.object(ib, "reconcile", new=_stop), pytest.raises(RuntimeError):
        await ib.reconcile_interview_turn(
            MagicMock(), profile_record=record, gap="Digitale Produktion",
            question="… Maschinendatenerfassung, MES-Systemen, OEE oder Industrie 4.0?", answer="nein",
            provider=MagicMock(), session_id="s",
            offered_choices=[{"text": "Zu OEE:", "names_without_claiming": [], "states_no_to": [],
                              "answers_concept": "OEE"}],
        )
    assert captured["offered_choices"] == [{"text": "Zu OEE:", "names_without_claiming": [],
                                            "states_no_to": [], "answers_concept": "OEE"}]
    # next to the answer it frames (held-out replay: the yes named OEE 0/3 → 3/3)
    assert captured["answer_about"] == "OEE"


def test_f8_the_reconcile_rule_scopes_a_bare_no_to_that_concept():
    from applire.prompts import reconcile as r

    src = " ".join(open(r.__file__, encoding="utf-8").read().split())
    assert 'When the NEW INFORMATION carries "answer_about", the person clicked an opening' in src
    assert 'denies exactly that concept — "denials": ["Kanban"] — and nothing else' in src
    rule9 = src[src.index("A DENIAL NAMES AN ITEM"):src.index("A DENIAL WITH AN EXCEPTION")].lower()
    assert "oee" not in rule9, "the example concept is held out of the measured case"
