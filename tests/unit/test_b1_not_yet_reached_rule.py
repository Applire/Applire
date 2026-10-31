# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RULING B-1 / interface A1-3 (Hazelnut build 5, U-7): an entry's
``not_yet_reached`` list is the candidate's own testimony that a line states an
aim, not a result — a DATA FACT, so a writer rule may act on it (ADR-062).

Measured before landing (prompt-first, ADR-021 am.): the founder's 10-27
``cv_tailoring`` writer input (record 84), gpt-6-luna, thinking off, with the two
governance lines placed in the project's ``not_yet_reached``, n=5 per arm —
field only, no rule: 1/5 past-tense result + 4/5 the flagged present-tense
mandate wording; field + this rule: 5/5 written as an aim/objective, 0/5 as a
result. A variant naming CV-register labels ("Ziel: …") duplicated the line in
3/5 and was dropped. Both lines are v2-only (the default pipeline): the v1 CV
writer must stay smaller than its reviewer (test_545, 4 characters of headroom)
and the v1 letter prompt sits at its size gate (test_565). The letter line was
not measured — no founder letter input exists.
"""
from applire.prompts import cover_letter, cv_tailoring

_CV = "An entry's \"not_yet_reached\" lists lines the candidate confirmed are not reached yet"


def test_the_v2_cv_writer_carries_the_rule_once_and_v1_is_unchanged():
    assert cv_tailoring.V2_SYSTEM_PROMPT.count(_CV) == 1
    assert "never as a result" in cv_tailoring.V2_SYSTEM_PROMPT
    assert "not_yet_reached" not in cv_tailoring.SYSTEM_PROMPT  # v1 size gate (test_545)


def test_the_v2_letter_writer_carries_it_and_v1_is_unchanged():
    v2 = cover_letter.build_system_prompt("de", pipeline="v2")
    assert v2.count('"not_yet_reached"') == 1 and "an aim, never a result" in v2
    assert "not_yet_reached" not in cover_letter.SYSTEM_PROMPT
