# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 8, WP-L prompt rules (v2 only; v1 stays byte-identical).

* 784:134 — the v2 corrector keeps a figure on what it counted when it merges
  sentences (case B: a career-wide "zehn Jahre" written onto the later role).
* 784:142 — the v2 final review's check 3 says an ENDED ``candidate_record``
  station grounds no present-tense knowledge of the employer today (G-2).

Text pins only: the effect is the real-provider replay in the WP-L report.
"""
from applire.prompts.review_cover_letter import (
    COVER_LETTER_REFINEMENT_PROMPT,
    TERMINAL_REVIEW_SYSTEM_PROMPT,
    V2_COVER_LETTER_REFINEMENT_PROMPT,
    V2_REVIEW_SYSTEM_PROMPT,
    V2_TERMINAL_REVIEW_SYSTEM_PROMPT,
    letter_loop_prompts,
)


def test_v2_corrector_keeps_a_figure_on_what_it_counts():
    rule = "A FIGURE KEEPS WHAT IT COUNTS (784:134)"
    assert V2_COVER_LETTER_REFINEMENT_PROMPT.count(rule) == 1
    assert "never moves into another employer's sentence" in V2_COVER_LETTER_REFINEMENT_PROMPT
    assert rule not in COVER_LETTER_REFINEMENT_PROMPT
    # the rule sits before the preserve line, inside the rules list
    assert V2_COVER_LETTER_REFINEMENT_PROMPT.index(rule) < V2_COVER_LETTER_REFINEMENT_PROMPT.index(
        "- Preserve the language, tone, structure")


def test_v2_final_review_check_3_names_the_ended_station():
    tail = "``candidate_record`` grounds the PAST"
    assert V2_TERMINAL_REVIEW_SYSTEM_PROMPT.count(tail) == 1
    i = V2_TERMINAL_REVIEW_SYSTEM_PROMPT.index("3. INVENTED EMPLOYER FACT")
    j = V2_TERMINAL_REVIEW_SYSTEM_PROMPT.index("4. REQUIRED CONTENT NOT DELIVERED")
    assert i < V2_TERMINAL_REVIEW_SYSTEM_PROMPT.index(tail) < j
    assert tail not in TERMINAL_REVIEW_SYSTEM_PROMPT  # v1 unchanged
    assert tail not in V2_REVIEW_SYSTEM_PROMPT  # the drafting reviewer is not in scope


def test_the_loop_hands_out_the_amended_v2_prompts():
    _rev, terminal, corrector = letter_loop_prompts(True)
    assert terminal is V2_TERMINAL_REVIEW_SYSTEM_PROMPT and corrector is V2_COVER_LETTER_REFINEMENT_PROMPT
    _rev, terminal, corrector = letter_loop_prompts(False)
    assert terminal is TERMINAL_REVIEW_SYSTEM_PROMPT and corrector is COVER_LETTER_REFINEMENT_PROMPT
