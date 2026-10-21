# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""783:L101 — "Über die Kosmetik-Verpackungen bei Weberit hinaus habe ich keine
Erfahrung in der Verpackungsindustrie" was recorded as the bare denial
"Verpackungsindustrie", and the letter then volunteered a lack the answer did not state.

Prompt-first, category B (rule 9 had no exception shape). Replay on the current
reconcile prompt, gpt-5.6-luna, n=5/arm: the two captured exception answers
(delivery runs 2 and 3) bare-field denial 10/10 -> 0/10, now "Verpackungsindustrie
außer Kosmetik-Verpackungen …"; three plain-denial controls keep their denials
15/15 (one, "Anlagenbau im engeren Sinn", is now scoped as its answer scopes it).
Residual: "no other X than what I described" (no exception named) stays bare (5/5).
"""
from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT


def _rule() -> str:
    i = RECONCILE_SYSTEM_PROMPT.index("A DENIAL WITH AN EXCEPTION KEEPS THE EXCEPTION")
    return RECONCILE_SYSTEM_PROMPT[i:RECONCILE_SYSTEM_PROMPT.index("10. CERTIFICATIONS", i)]


def test_rule_9_names_a_denial_with_its_exception():
    assert "never bare Y" in _rule()


def test_the_example_is_held_out_of_the_captured_cases():
    rule = _rule().lower()
    assert "verpackung" not in rule and "kosmetik" not in rule
