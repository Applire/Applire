# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""783:L100 — a metric entered the vault as a skill through the interview
("LTIF", delivery run 3; "OEE" in four earlier runs) and reached the CV skills line.

Prompt-first, category B (rule absent): the upsert_skill doc said only "a skill".
Captured reconcile calls with upsert_skill names: 6 of 425 a bare metric name.
Replay of three such calls (gpt-5.6-luna, n=5/arm): metric-named skill 8/15 -> 0/15,
the figure kept in the bullet 15/15 in both arms, skills per call not reduced.
"""
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT


def test_the_skill_op_says_a_metric_name_is_not_a_skill():
    i = RECONCILE_SYSTEM_PROMPT.index("- upsert_skill — a skill.")
    j = RECONCILE_SYSTEM_PROMPT.index("REQUIRED: name.", i)
    block = RECONCILE_SYSTEM_PROMPT[i:j]
    assert "capability the person HAS" in block
    assert "LTIF" in block and "is not a skill" in block
