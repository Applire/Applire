# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""R2-11 (Hazelnut build 4, UX re-audit 2) — interview vault hygiene.

The round-2 log wrote "… is my daily work" under a closed 2017–2021 role and a
skill named after the question's own term. Replay (luna, captured input):
closed-role voice 6/6 → 0/6, question-term skill 4/6 → 1–2 of 3–6, controls
unchanged. This pins the two clauses and keeps the replay set's phrases out.
"""
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT as P


def _block(start: str, end: str) -> str:
    i = P.index(start)
    return P[i : P.index(end, i)]


def test_a_bullet_is_a_cv_line_in_the_past_tense_for_a_closed_entry():
    block = _block("- add_bullets", "- upsert_skill")
    assert "A bullet is a CV line, not a quote" in block
    assert "end_date takes the past tense" in block


def test_a_skill_name_is_never_only_the_questions_word():
    block = _block("- upsert_skill", "- set_field")
    assert "never a term" in block and "only the question or the gap label uses" in block


def test_no_replay_case_phrase_reaches_the_prompt():
    for phrase in ("daily work", "machine-learning", "PSD2", "BaFin", "Stripe", "webhook", "scoring model"):
        assert phrase not in P, phrase
