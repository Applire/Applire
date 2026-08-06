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
    DENIAL_PROBE_QUESTION_SYSTEM_PROMPT,
    QUESTION_SYSTEM_PROMPT,
)


def _collapsed(text: str) -> str:
    """Collapse line-wrapping/backslash-continuation whitespace for
    substring assertions that would otherwise be defeated by the prompt's
    hand-wrapped multi-line formatting."""
    return " ".join(text.split())


# ── QUESTION_SYSTEM_PROMPT — Choice coverage rules (3a) ──────────────────────


def test_prompt_has_a_coverage_rules_block():
    assert "Choice coverage rules" in QUESTION_SYSTEM_PROMPT


def test_prompt_names_the_three_levels():
    low = _collapsed(QUESTION_SYSTEM_PROMPT).lower()
    assert "direct" in low
    assert "partial" in low
    assert "denial" in low


def test_prompt_denial_is_evidence_conditioned_and_never_softened():
    # ADR-064 clause 6 as amended 2026-08-05 (#347): the denial choice is no
    # longer unconditional — it mirrors the DIRECT choice's evidence
    # condition and is scoped to the unevidenced constituents. The
    # never-softened half of the original rule is unchanged.
    low = _collapsed(QUESTION_SYSTEM_PROMPT).lower()
    assert "always present" not in low
    assert "same evidence condition as the direct choice" in low
    assert "never softened into a hedge" in low


def test_prompt_denial_scope_forbids_broader_areas_and_evidenced_concepts():
    low = _collapsed(QUESTION_SYSTEM_PROMPT).lower()
    assert "never paraphrase them into a broader area" in low
    assert "never deny, name, or sweep over a concept" in low


def test_prompt_all_evidenced_cluster_offers_no_denial_choice():
    low = _collapsed(QUESTION_SYSTEM_PROMPT).lower()
    assert "offer no denial choice" in low
    assert "nothing honest left for a denial to say" in low


def test_prompt_conversation_answers_count_as_evidence():
    # #347 record 30: the offered denial contradicted the candidate's answer
    # from one turn earlier. Both generator prompts must bind choices to the
    # conversation, not only to the profile summary.
    for prompt in (QUESTION_SYSTEM_PROMPT, DENIAL_PROBE_QUESTION_SYSTEM_PROMPT):
        low = _collapsed(prompt).lower()
        assert "earlier answers in this conversation are evidence" in low


def test_prompt_gives_the_denial_vs_hedge_example():
    collapsed = _collapsed(QUESTION_SYSTEM_PROMPT)
    assert "\"I haven't worked with X\" is a denial." in collapsed
    assert "\"I have limited exposure to X\" is NOT a" in collapsed


def test_prompt_states_the_truthfulness_interaction_explicitly():
    # The interaction the brief calls out as most likely to be got wrong: a
    # coverage rule read in isolation would fabricate a "yes" to fill the
    # DIRECT slot on a genuine gap. The prompt must rule this out in words.
    low = _collapsed(QUESTION_SYSTEM_PROMPT).lower()
    assert "does not relax the truthfulness rules" in low
    assert "unchanged and still bind" in low
    assert 'partial + denial only' in low
    assert 'never invent a direct-level "yes"' in low


def test_coverage_block_sits_after_the_truthfulness_rules():
    # The interaction must be legible as an addition ON TOP of the
    # (unmodified) truthfulness rules, not a replacement for them.
    i_truth = QUESTION_SYSTEM_PROMPT.index("Choice truthfulness rules")
    i_cov = QUESTION_SYSTEM_PROMPT.index("Choice coverage rules")
    assert i_truth < i_cov


def test_existing_truthfulness_rules_are_untouched():
    # Pin the four pre-existing truthfulness bullets verbatim (constraint:
    # "the existing truthfulness rules are unchanged and still bind").
    assert (
        "A choice may ASSERT experience only with skills, tools, or employers that appear in the "
        "candidate profile summary below." in QUESTION_SYSTEM_PROMPT
    )
    assert "Never invent specific projects, systems, employers, or metrics." in QUESTION_SYSTEM_PROMPT
    assert "Ground the NARRATIVE, not just the noun." in QUESTION_SYSTEM_PROMPT
    assert (
        "Never draft an affirmative claim for a concept the profile shows no evidence for."
        in QUESTION_SYSTEM_PROMPT
    )


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


def test_direct_caveat_is_folded_into_the_first_coverage_bullet():
    # Finding 3: the DIRECT-only-when-evidenced caveat must appear in the
    # SAME bullet as "one at the DIRECT level" — not only two bullets later,
    # where a model weighting the first, assertive instruction over a later
    # correction could fabricate a direct-level claim to satisfy coverage.
    low = _collapsed(QUESTION_SYSTEM_PROMPT).lower()
    i_direct = low.index("one at the direct level")
    i_partial = low.index("one at the partial level")
    i_caveat = low.index("evidences it", i_direct)
    assert i_direct < i_caveat < i_partial


def test_direct_caveat_still_present_later_too():
    # Belt and braces: the pre-existing later bullet is kept, not replaced.
    low = _collapsed(QUESTION_SYSTEM_PROMPT).lower()
    assert low.count("evidences it") >= 2
