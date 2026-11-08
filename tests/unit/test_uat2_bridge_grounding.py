# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.
"""UAT-2 (founder UAT 2026-11-08, ADR-064 amended) — bridges and the honest
bridge, at the seams that used to throw them away.

Fate map (Sonnet 5.5 replay of the founder's turn, n=5): the filter dropped a
bridge for NAMING the asked concept (term evidence), V-7 dropped every denial
chip on a C question, offered_choices_in_answer excluded denial-level
choices, and the G-3 gate dropped every denial on a chip-only answer — so a
sent honest bridge recorded nothing for the L-2 letter ladder. Synthetic data
only; the shapes follow the ruling's own generic example.
"""

import copy

import pytest

from applire.services.choice_grounding import (
    choice_presentation,
    filter_ungrounded_choices,
    offered_choices_in_answer,
    record_offered_choices,
)
from applire.services.interview_graph import _carry_levels_by_index
from applire.services.profile.reconcile.stance import offered_choice_gate

CLUSTER = {
    "id": "cluster-planning",
    "label": "Planung und Prognose",
    "gaps": ["Prognosen", "Kapazitätsplanung"],
    "jd_skills": ["Excel"],
    "jd_context": "Erfahrung mit Prognosen und Kapazitätsplanung",
}
PROFILE = {
    "skills": [{"name": "Excel"}, {"name": "Personaleinsatzplanung"}],
    "work_experience": [
        {
            "id": "w-1",
            "company": "Nordlicht Logistik GmbH",
            "role": "Schichtleiter Distributionszentrum",
            "technologies": [],
            "responsibilities": [
                "Personaleinsatzplanung für 25 Mitarbeitende im Zweischichtbetrieb",
                "Steuerung der wöchentlichen Mengenvorgaben im Wareneingang",
            ],
            "achievements": [],
        }
    ],
}

BRIDGE = {
    "text": (
        "Als Schichtleiter habe ich die Personaleinsatzplanung auch an der "
        "wöchentlichen Mengenprognose ausgerichtet, also mit Prognosen gearbeitet."
    ),
    "ask": "Hast du als Schichtleiter die Personaleinsatzplanung auch an Prognosen ausgerichtet?",
    "level": "partial",
    "denied_terms": [],
}
HONEST = {
    "text": (
        "Mit Prognosen habe ich bisher nicht gearbeitet, aber durch die "
        "Personaleinsatzplanung kenne ich die wöchentlichen Mengenvorgaben."
    ),
    "ask": "Nicht direkt – aber kennst du Mengenvorgaben aus der Personaleinsatzplanung?",
    "level": "denial",
    "denied_terms": ["Prognosen"],
}


# --- the filter -------------------------------------------------------------


def test_a_partial_bridge_naming_the_asked_concept_is_kept():
    assert filter_ungrounded_choices([BRIDGE], CLUSTER, PROFILE, "C") == [BRIDGE["text"]]


def test_the_same_sentence_as_direct_is_still_dropped():
    # The bridge exemption is level-scoped: "direct" keeps the literal check.
    assert filter_ungrounded_choices([{**BRIDGE, "level": "direct"}], CLUSTER, PROFILE, "C") is None


def test_a_bridge_smuggling_a_foreign_skill_is_still_dropped():
    # Only the ASKED concepts are exempt — a JD skill that is no constituent
    # and not in the profile is not.
    chip = {**BRIDGE, "text": BRIDGE["text"] + " Dafür habe ich SAP IBP eingesetzt."}
    cluster = {**CLUSTER, "jd_skills": ["SAP IBP"]}
    assert filter_ungrounded_choices([chip], cluster, PROFILE, "C") is None


def test_the_honest_bridge_on_the_asked_concept_is_kept_on_c():
    assert filter_ungrounded_choices([HONEST], CLUSTER, PROFILE, "C") == [HONEST["text"]]


def test_at_most_one_honest_bridge_per_question():
    second = {
        **HONEST,
        "text": (
            "Kapazitätsplanung habe ich bisher nicht gemacht, aber durch die "
            "Personaleinsatzplanung kenne ich die Schichtbesetzung."
        ),
        "denied_terms": ["Kapazitätsplanung"],
    }
    assert filter_ungrounded_choices([HONEST, second], CLUSTER, PROFILE, "C") == [HONEST["text"]]


@pytest.mark.parametrize(
    "chip",
    [
        # (a) a no on something the cluster did not ask (G-1 scope)
        {**HONEST, "text": HONEST["text"].replace("Prognosen", "Budgets"), "denied_terms": ["Budgets"]},
        # (b) a pure denial — no "aber durch Y"
        {**HONEST, "text": "Mit Prognosen habe ich bisher nicht gearbeitet."},
        # (c) no declaration at all
        {**HONEST, "denied_terms": []},
        # (d) a pivot with nothing after it
        {**HONEST, "text": "Mit Prognosen habe ich bisher nicht gearbeitet, aber …"},
    ],
    ids=["foreign-scope", "pure-denial", "undeclared", "empty-remainder"],
)
def test_a_denial_chip_that_is_not_one_honest_bridge_is_dropped_on_c(chip):
    assert filter_ungrounded_choices([chip], CLUSTER, PROFILE, "C") is None


# --- carry + presentation ---------------------------------------------------


def test_the_language_review_carries_a_missing_ask_by_index():
    reviewed = [{"text": BRIDGE["text"], "level": "partial"}, {"text": HONEST["text"]}]
    out = _carry_levels_by_index([BRIDGE, HONEST], reviewed)
    assert [c.get("ask") for c in out] == [BRIDGE["ask"], HONEST["ask"]]


def test_the_language_review_keeps_its_own_rewritten_ask():
    reviewed = [{**BRIDGE, "ask": "Hast du die Planung an Prognosen ausgerichtet?"}]
    out = _carry_levels_by_index([BRIDGE], reviewed)
    assert out[0]["ask"] == "Hast du die Planung an Prognosen ausgerichtet?"


def test_presentation_is_index_aligned_and_never_stale():
    state: dict = {}
    plain = {"text": "Ich habe die Mengenvorgaben gesteuert.", "level": "direct"}
    delivered = [BRIDGE["text"], plain["text"], HONEST["text"]]
    record_offered_choices(state, [BRIDGE, plain, HONEST], delivered, unmentioned=["Prognosen"])
    asks, unmentioned = choice_presentation(state, delivered)
    assert asks == [BRIDGE["ask"], None, HONEST["ask"]]
    assert unmentioned == ["Prognosen"]
    # Another question's choices never inherit this lead list.
    assert choice_presentation(state, [BRIDGE["text"]]) == ([BRIDGE["ask"]], None)
    # Choices without any ask (a gate's options) carry no asks at all.
    assert choice_presentation(state, [plain["text"]]) == (None, None)


# --- the fate of a SENT honest bridge ----------------------------------------


def _info(answer: str) -> dict:
    offered = {
        BRIDGE["text"]: {"level": "partial", "names_without_claiming": [], "ask": BRIDGE["ask"]},
        HONEST["text"]: {"level": "denial", "names_without_claiming": ["Prognosen"], "ask": HONEST["ask"]},
    }
    info = {"gap": "Prognosen", "question": "Q?", "answer": answer}
    chosen = offered_choices_in_answer(answer, offered)
    if chosen:
        info["offered_choices"] = chosen
    return info


def test_a_sent_honest_bridge_records_its_no_even_when_the_model_emits_none():
    kept, dropped, _ = offered_choice_gate([], _info(HONEST["text"]), "interview")
    assert kept == ["Prognosen"] and dropped == []


def test_a_sent_honest_bridge_keeps_exactly_its_scope_not_the_models_widening():
    kept, dropped, _ = offered_choice_gate(
        ["Prognosen", "Kapazitätsplanung"], _info(HONEST["text"]), "interview"
    )
    assert kept == ["Prognosen"]
    assert dropped == ["Kapazitätsplanung"]


def test_a_sent_honest_bridge_with_typed_words_adds_its_no_once():
    answer = HONEST["text"] + " Mit Excel-Auswertungen habe ich viel gearbeitet."
    kept, _, _ = offered_choice_gate(["prognosen"], _info(answer), "interview")
    assert kept == ["prognosen"], "the model's own spelling of the same no is not doubled"
    kept, _, _ = offered_choice_gate([], _info(answer), "interview")
    assert kept == ["Prognosen"]


def test_a_sent_bridge_records_no_denial():
    kept, dropped, _ = offered_choice_gate([], _info(BRIDGE["text"]), "interview")
    assert (kept, dropped) == ([], [])


class _Reply:
    def __init__(self, denials=None):
        self.payload = {"ops": [], "ambiguities": [], "denials": list(denials or [])}

    async def aparse_json(self, prompt, **kw):
        if "profile reconciler" not in (kw.get("system") or ""):
            return {"verdict": "unconfirmed"}
        return copy.deepcopy(self.payload)

    async def acomplete(self, prompt, **kw):
        return ""


@pytest.mark.asyncio
async def test_engine_records_the_sent_honest_bridges_no():
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.engine import reconcile

    profile = {
        "personal_info": {"name": "Jonas Feld", "email": "jonas@example.org"},
        "skills": [{"name": "Excel", "category": "technical", "status": "confirmed"}],
        "work_experience": [{
            "id": "w-1", "company": "Nordlicht Logistik GmbH", "role": "Schichtleiter",
            "start_date": "2019-04", "is_current": True,
            "responsibilities": ["Personaleinsatzplanung für 25 Mitarbeitende."],
        }],
        "metadata": {},
    }
    result = await reconcile(
        MasterProfileData.model_validate(profile), _info(HONEST["text"]), "interview", _Reply(), "de"
    )
    assert result.denials == ["Prognosen"]


# --- the language review judges and keeps the ask ---------------------------


def test_the_language_review_judges_and_rewrites_the_ask():
    from applire.prompts.review_question_language import (
        QUESTION_LANGUAGE_REFINEMENT_PROMPT,
        QUESTION_LANGUAGE_REVIEW_SYSTEM_PROMPT,
        build_question_language_review_prompt,
    )

    review = " ".join(QUESTION_LANGUAGE_REVIEW_SYSTEM_PROMPT.split())
    refine = " ".join(QUESTION_LANGUAGE_REFINEMENT_PROMPT.split())
    assert 'Judge only each choice\'s "text" and "ask"' in review
    assert 'any "text" or "ask" still in the wrong language' in review
    assert 'every choice\'s "text" and "ask" into that language' in refine
    assert '{"ask": str, "text": str, "level": str, "denied_terms": [str]}' in refine
    user = build_question_language_review_prompt("German", {"question": "Q?", "choices": [BRIDGE]})
    assert '"text" and "ask" written entirely in German' in user
    assert BRIDGE["ask"] in user, "the ask reaches the reviewer"
