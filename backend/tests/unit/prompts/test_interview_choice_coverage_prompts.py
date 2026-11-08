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

"""ADR-064 Task 3 — text-contains pins for the interview prompt changes.

These are TEXT-CONTAINS assertions only: they pin that the rule text is
present in the system prompt strings. They prove nothing about how an LLM
actually behaves when given the prompt — that is a real-LLM-run concern,
out of scope here (see task-3-report.md).

Two prompts are touched:

1. QUESTION_SYSTEM_PROMPT (Mode-A generator) — carried four *truthfulness*
   bullets and zero *coverage* bullets, so the model drafted 2-3 variations
   of the single most plausible (usually affirmative) answer. This pins the
   new "Choice coverage rules" block, including the interaction most likely
   to be got wrong: on a genuine gap the spanning set is PARTIAL + DENIAL
   only, never an invented DIRECT-level "yes" — the pre-existing
   truthfulness rules are unchanged and still bind.

2. DENIAL_PROBE_QUESTION_SYSTEM_PROMPT (F4 finding-fix, 2026-07-29 — was
   FOLLOW_UP_QUESTION_SYSTEM_PROMPT until this fix) — pins that the model is
   told to generalise to the broader SKILL AREA instead of re-asking about
   the same named form the candidate just denied. The ADR-064 transfer probe
   later split onto its own dedicated generator/prompt
   (``question_generator_with_profile(..., denial_probe=True)`` ->
   ``build_denial_probe_question_prompt`` /
   ``DENIAL_PROBE_QUESTION_SYSTEM_PROMPT``); a "the candidate DENIED a
   specific named form" ``follow_up_hint`` is issued EXCLUSIVELY through
   that path now (services/session.py::_ask_denial_probe). These three tests
   used to assert against FOLLOW_UP_QUESTION_SYSTEM_PROMPT, whose OWN hint
   (the "be more specific" retry) never mentions a denial — the rule they
   pinned could never fire on that prompt's actual call path. Re-pointed to
   the prompt that actually generates this question.
"""

from applire.prompts.interview import (
    _COVERED_IN_OTHER_WORDS_RULE,
    DENIAL_PROBE_QUESTION_SYSTEM_PROMPT,
    QUESTION_SYSTEM_PROMPT,
)


def _collapsed(text: str) -> str:
    """Collapse line-wrapping/backslash-continuation whitespace for
    substring assertions that would otherwise be defeated by the prompt's
    hand-wrapped multi-line formatting."""
    return " ".join(text.split())


# ── QUESTION_SYSTEM_PROMPT — Choice coverage rules (3a) ──────────────────────


def test_prompt_has_an_answer_ideas_block():
    # UAT-2 (founder UAT 2026-11-08, ADR-064 amended): the coverage block was
    # rebuilt as the "Answer ideas" block — bridges, one honest bridge, no
    # restatement; the R2-2 "no denial level" / "leave the concept out" rules
    # it replaces are gone (the founder ruled them an over-correction).
    assert "Answer ideas (ADR-064 amended 2026-11-08, founder ruling UAT-2" in QUESTION_SYSTEM_PROMPT
    assert "Choice coverage rules" not in QUESTION_SYSTEM_PROMPT


def test_prompt_names_the_three_levels():
    low = _collapsed(QUESTION_SYSTEM_PROMPT).lower()
    assert "direct" in low
    assert "partial" in low
    assert "denial" in low


def test_prompt_offers_one_honest_bridge_scoped_to_the_asked_concept():
    # UAT-2 (founder UAT 2026-11-08, ADR-064 amended): the coverage block was
    # rebuilt as the "Answer ideas" block — bridges, one honest bridge, no
    # restatement; the R2-2 "no denial level" / "leave the concept out" rules
    # it replaces are gone (the founder ruled them an over-correction).
    flat = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert 'HONEST BRIDGE (level "denial", ONE per question at most' in flat
    assert '"X habe ich bisher nicht …, aber durch Y …"' in flat
    # UAT-2d (MD-UAT-5): X exactly as the cluster spells it, nothing added; a
    # qualified no is a bridge; the honest bridge's ask is a soft question.
    assert "X is the asked concept exactly as the GAP CLUSTER block spells it, with NOTHING added" in flat
    assert "a no that is only true with a qualifier (\"not independently\", \"not formally\") is not a no — offer it as a BRIDGE instead" in flat
    assert '"Kennst du Mengenvorgaben vielleicht aus der Schichtplanung?"' in flat
    assert "Nicht direkt" not in flat
    assert 'Its "denied_terms" lists X exactly as the GAP CLUSTER block names it' in flat
    assert "never offer a choice at the denial level" not in flat.lower()


def test_prompt_no_other_limit_than_the_honest_bridge():
    # UAT-2 (founder UAT 2026-11-08, ADR-064 amended): the coverage block was
    # rebuilt as the "Answer ideas" block — bridges, one honest bridge, no
    # restatement; the R2-2 "no denial level" / "leave the concept out" rules
    # it replaces are gone (the founder ruled them an over-correction).
    flat = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert "NO OTHER LIMIT: apart from the honest bridge's no on the asked concept" in flat
    assert '"my profile does not yet document X" is the system speaking' in flat
    assert "The one exception: the candidate's own earlier no" in flat
    assert "NO INVENTED LIMIT" not in flat


def test_prompt_bridge_joins_a_real_anchor_to_the_asked_concept():
    # UAT-2 (founder UAT 2026-11-08, ADR-064 amended): the coverage block was
    # rebuilt as the "Answer ideas" block — bridges, one honest bridge, no
    # restatement; the R2-2 "no denial level" / "leave the concept out" rules
    # it replaces are gone (the founder ruled them an over-correction).
    flat = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert 'BRIDGE (level "partial", one or two): joins ONE real anchor from the profile' in flat
    assert "never by the employer's name" in flat
    assert "never a forced analogy" in flat
    assert 'A bridge is never "direct".' in flat
    # the R2-2 / G-3 A "leave the concept out" rule is withdrawn
    assert "never mentions a concept it does not cover" not in flat


def test_denied_terms_lists_the_honest_bridges_concept():
    # UAT-2 (founder UAT 2026-11-08, ADR-064 amended): the coverage block was
    # rebuilt as the "Answer ideas" block — bridges, one honest bridge, no
    # restatement; the R2-2 "no denial level" / "leave the concept out" rules
    # it replaces are gone (the founder ruled them an over-correction).
    flat = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert '"denied_terms": [] on every choice except the honest bridge (its asked concept) and a restated earlier no' in flat
    assert "names WITHOUT claiming them" not in flat


def test_covered_in_other_words_rule_counts_a_denied_requirement_as_answered():
    # R2-9: a requirement the candidate DENIED is answered too (a no is an
    # answer, never asked again); covered_by_answer lists covered OR denied.
    flat = _collapsed(_COVERED_IN_OTHER_WORDS_RULE)
    assert "DENIED" in flat and "is answered too" in flat
    assert "a no is an answer, and it is never asked again" in flat
    assert "covered OR denied" in flat


def test_prompt_conversation_answers_count_as_evidence():
    # #347 record 30: the offered denial contradicted the candidate's answer
    # from one turn earlier. Both generator prompts must bind choices to the
    # conversation, not only to the profile summary.
    for prompt in (QUESTION_SYSTEM_PROMPT, DENIAL_PROBE_QUESTION_SYSTEM_PROMPT):
        low = _collapsed(prompt).lower()
        assert "earlier answers in this conversation are evidence" in low


def test_prompt_forbids_restatement():
    # UAT-2 (founder UAT 2026-11-08, ADR-064 amended): the coverage block was
    # rebuilt as the "Answer ideas" block — bridges, one honest bridge, no
    # restatement; the R2-2 "no denial level" / "leave the concept out" rules
    # it replaces are gone (the founder ruled them an over-correction).
    flat = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert "NO RESTATEMENT: no choice repeats a fact of the profile summary" in flat
    assert "Every choice names an asked concept in the question's own words." in flat


def test_prompt_states_the_truthfulness_interaction_explicitly():
    # UAT-2 (founder UAT 2026-11-08, ADR-064 amended): the coverage block was
    # rebuilt as the "Answer ideas" block — bridges, one honest bridge, no
    # restatement; the R2-2 "no denial level" / "leave the concept out" rules
    # it replaces are gone (the founder ruled them an over-correction).
    # The interaction most likely to be got wrong survives in the new shape:
    # a bridge or a missing concept never becomes a DIRECT "yes".
    flat = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert "For a concept the profile shows no evidence for, DIRECT is not available." in flat
    assert "only for a concept the profile summary itself evidences, in any words" in flat


def test_answer_ideas_block_sits_after_the_truthfulness_rules():
    i_truth = QUESTION_SYSTEM_PROMPT.index("Choice truthfulness rules")
    i_cov = QUESTION_SYSTEM_PROMPT.index("Answer ideas (ADR-064 amended 2026-11-08")
    assert i_truth < i_cov


def test_truthfulness_rules_keep_the_invention_boundary():
    # UAT-2 (founder UAT 2026-11-08, ADR-064 amended): the coverage block was
    # rebuilt as the "Answer ideas" block — bridges, one honest bridge, no
    # restatement; the R2-2 "no denial level" / "leave the concept out" rules
    # it replaces are gone (the founder ruled them an over-correction).
    # The truthfulness bullets were rewritten for bridges (a bridge names the
    # asked concept as a hypothesis); the invention boundary and the DIRECT
    # narrative rule are kept.
    flat = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert "Never invent an employer, project, system, tool, figure or result." in flat
    assert "Every anchor a choice names — a role, project, activity, skill or employer — appears in the candidate profile summary below or in the candidate's earlier answers." in flat
    assert "Ground the NARRATIVE, not just the noun." in flat


# ── DENIAL_PROBE_QUESTION_SYSTEM_PROMPT — skill-area framing (3b; F4
# finding-fix, 2026-07-29: re-pointed here from FOLLOW_UP_QUESTION_SYSTEM_
# PROMPT — see module docstring) ─────────────────────────────────────────────


def test_denial_probe_prompt_instructs_generalising_to_the_skill_area():
    low = _collapsed(DENIAL_PROBE_QUESTION_SYSTEM_PROMPT).lower()
    assert "skill area" in low
    assert "do not ask about that same named form again" in low


def test_denial_probe_prompt_forbids_a_hard_coded_technology_list():
    # The model must choose the framing/examples itself — no lookup table
    # anywhere, per ADR-065 clause 2.
    low = _collapsed(DENIAL_PROBE_QUESTION_SYSTEM_PROMPT).lower()
    assert "never rely on a fixed list of frameworks or technologies" in low


def test_denial_probe_prompt_illustrates_with_togaf_example_only():
    # One illustrative example is fine (it teaches the PATTERN); it must not
    # read as an enumerable taxonomy of skill areas. M7 finding-fix
    # (2026-07-29): pinned on the example's OWN content — TOGAF generalises
    # to "enterprise architecture frameworks" exactly once — rather than
    # counting the filler phrase "e.g.", which could appear (or stop
    # appearing) anywhere else in the prompt without this guarantee changing
    # at all; the old assertion pinned prose style, not the rule.
    collapsed = _collapsed(DENIAL_PROBE_QUESTION_SYSTEM_PROMPT)
    assert "TOGAF" in collapsed
    assert collapsed.count("enterprise architecture frameworks") == 1


# ── ADR-062 fix (2026-07-29) — level-tagged choice schema, Findings 1 & 3 ────


def test_choices_schema_asks_for_a_level_per_choice():
    # Finding 1: the generator must STATE the level, not leave it to be
    # guessed from wording downstream. Every choice in the schema example
    # carries "level" alongside "text", enumerating the three known values.
    collapsed = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert '"level"' in collapsed
    assert '"direct" | "partial" | "denial"' in collapsed


def test_direct_is_bound_to_evidence_in_its_own_bullet():
    # UAT-2 (founder UAT 2026-11-08, ADR-064 amended): the coverage block was
    # rebuilt as the "Answer ideas" block — bridges, one honest bridge, no
    # restatement; the R2-2 "no denial level" / "leave the concept out" rules
    # it replaces are gone (the founder ruled them an over-correction).
    low = _collapsed(QUESTION_SYSTEM_PROMPT).lower()
    i_direct = low.index('- direct (level "direct"): only for a concept')
    i_caveat = low.index("evidences, in any words", i_direct)
    i_mix = low.index("the mix, at most 3 choices")
    assert i_direct < i_caveat < i_mix


def test_the_mix_caps_the_set_and_names_both_arms():
    flat = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert "THE MIX, at most 3 choices: a concept the profile does not evidence gets one or two bridges and the one honest bridge" in flat
    assert "a concept it evidences gets one direct choice and at most one bridge" in flat


def test_two_texts_per_choice_ask_and_text():
    # UAT-2 (founder UAT 2026-11-08, ADR-064 amended): the coverage block was
    # rebuilt as the "Answer ideas" block — bridges, one honest bridge, no
    # restatement; the R2-2 "no denial level" / "leave the concept out" rules
    # it replaces are gone (the founder ruled them an over-correction).
    # UAT-2b: "text" is inserted on click, "ask" is the button label.
    flat = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert 'TWO TEXTS per choice. "text" is the candidate\'s own first-person sentence' in flat
    assert '"ask" is the button label: a short question TO the candidate (German: du-form) naming the SAME anchor and the SAME concept' in flat
    assert '{"ask": "Question A to the candidate", "text": "First-person sentence A"' in flat


