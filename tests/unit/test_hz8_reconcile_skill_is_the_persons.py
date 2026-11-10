# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""787:192 / 787:176 / 787:179 (Hazelnut build 8, WP-I): the reconcile prompt's upsert_skill
definition. A skill is what the person THEMSELVES works with; a tool others used belongs to
that work; the name is the answer's own word (no expansion, translation, wider category, or
re-spelled gap label). Evidence lives in the build-8 WP-I report (held-out answers on persisted
synthetic reconcile inputs); this pins the wording the measurement was made on."""
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT


def _skill_op() -> str:
    start = RECONCILE_SYSTEM_PROMPT.index("- upsert_skill — a skill.")
    return RECONCILE_SYSTEM_PROMPT[start:RECONCILE_SYSTEM_PROMPT.index("- upsert_certification", start)]


def test_a_tool_others_used_is_not_the_persons_skill():
    op = _skill_op()
    assert "they THEMSELVES work with" in op
    assert "never to the person's skills" in op
    assert "a duty the answer describes" in op


def test_the_name_is_the_answers_own_word():
    op = _skill_op()
    assert "never its expansion, translation or wider category" in op
    assert "does not make the gap label" in op
    assert "When the answer names no capability of its own, record none." in op
    # the earlier limits stay
    assert "only the question or the gap label uses" in op
    assert "Never count it from the entries' dates" in op
