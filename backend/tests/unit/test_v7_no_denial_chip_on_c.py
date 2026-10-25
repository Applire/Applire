# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Founder ruling V-7 (787 M2, 2026-10-21): a Category C (exploratory) interview
question offers NO one-click denial chip. The candidate types a denial in their own
words; one click no longer records a denial (ADR-059) of a fact the CV states in other
words. Structural rule on the question type — not a judgement about the chip — so it
is deterministic in `filter_ungrounded_choices`, with the prompt telling the model not
to spend a slot on one. Category B keeps the existing denial-chip mechanics."""
from applire.prompts.interview import build_question_prompt
from applire.services.choice_grounding import filter_ungrounded_choices

CLUSTER = {"id": "c", "label": "Produktionssteuerung", "category": "C",
           "gaps": ["Produktionsverantwortung", "IFS"], "jd_skills": [], "jd_context": ""}
PROFILE = {"skills": [{"name": "Shopfloor-Management"}],
           "work_experience": [{"company": "Weberit", "role": "Produktionsleiter",
                                "responsibilities": ["Shopfloor-Management eingeführt"]}]}
CHIPS = [
    {"text": "Ich habe Shopfloor-Management eingeführt.", "level": "direct", "denied_terms": []},
    {"text": "Mit IFS habe ich nicht gearbeitet.", "level": "denial", "denied_terms": ["IFS"]},
]


def test_a_denial_chip_is_dropped_on_a_category_c_question():
    assert filter_ungrounded_choices(CHIPS, CLUSTER, PROFILE, "C") == [CHIPS[0]["text"]]


def test_a_legitimate_denial_chip_is_kept_on_a_category_b_question():
    assert filter_ungrounded_choices(CHIPS, CLUSTER, PROFILE, "B") == [c["text"] for c in CHIPS]


def test_only_denials_offered_on_c_leaves_the_plain_answer_box():
    assert filter_ungrounded_choices([CHIPS[1]], CLUSTER, PROFILE, "C") is None


def test_the_c_prompt_asks_for_no_denial_choice():
    prompt = build_question_prompt(CLUSTER, PROFILE, [], gap_category="C")
    assert "Offer NO denial choice" in prompt
    assert "only valid scope for a denial" not in prompt
    # R2-2: a PARTIAL chip marks a gap as "not in the profile", never as a lack.
    assert "does not yet document a concept" in prompt
    assert "never that the candidate lacks it" in prompt
