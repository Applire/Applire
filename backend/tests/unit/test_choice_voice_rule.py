# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""787:L72 — an interview answer choice ended in an instruction to the candidate
("…; bitte ergänze Zeitraum, Ablauf und Ergebnis.") or a promise ("… kann ich
genauer beschreiben"); selecting it put that text into the candidate's testimony.

Prompt-first, category B (rule absent): nothing told the model a choice is the
candidate's own voice, while the same prompt asks the QUESTION to "invite specific
examples". Replay of the 8 captured drafts with an instruction chip (gpt-5.6-luna,
n=5/arm, thinking off): imperative chips 20 -> 0, promise chips 26 -> 1, level mix
unchanged (denial 37/106 -> 35/96). The rule lives in the shared coverage block, so
Mode A and the transfer probe both carry it — this pins that.
"""
from applire.prompts.interview import (
    DENIAL_PROBE_QUESTION_SYSTEM_PROMPT,
    QUESTION_SYSTEM_PROMPT,
    _CHOICE_COVERAGE_RULES,
)


def test_the_voice_rule_is_in_the_shared_coverage_block():
    assert "VOICE (787:L72)" in _CHOICE_COVERAGE_RULES
    assert "in the first person" in _CHOICE_COVERAGE_RULES
    assert "inviting detail is the QUESTION's job" in _CHOICE_COVERAGE_RULES


def test_both_choice_drafting_prompts_carry_it():
    for prompt in (QUESTION_SYSTEM_PROMPT, DENIAL_PROBE_QUESTION_SYSTEM_PROMPT):
        assert "VOICE (787:L72)" in prompt
