# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 D-14b. A LinkedIn import lost the end date of a short 2005
role. The extraction had it right ("März 2005 - September 2005" became
2005-03 / 2005-09). Then the extraction REVIEWER called the end date
fabricated, and the corrector set it to null. A control's false positive
deleted a true fact.

Replay of the stored reviewer call (gpt-6-luna, thinking off): the false date
flag 3/5 → 0/6. A truly fabricated end date in the same input is still
flagged, 4/4. Prompt-first: rule B now asks the reviewer to read the entry's
own date line, both ends, and to count a month written in another language as
the same month.
"""
from applire.prompts.review_cv_extraction import CV_EXTRACTION_REVIEW_SYSTEM_PROMPT


def _rule_b() -> str:
    s = CV_EXTRACTION_REVIEW_SYSTEM_PROMPT
    return " ".join(s[s.index("B. FABRICATED DATES"):s.index("Also check for material defects")].split())


def test_rule_b_reads_the_entrys_own_date_line_before_flagging():
    rule = _rule_b()
    assert "find THAT entry's own date line in the source and read both ends of its range" in rule
    assert "A month written in another language is the same month" in rule
    assert "if the extracted date matches it, say nothing about it" in rule


def test_rule_b_still_names_an_invented_component_as_the_defect():
    rule = _rule_b()
    assert "Flag only a component that line does not state" in rule
    assert "If the source gives no date, the field must be null" in rule


def test_the_example_is_held_out_of_the_measured_input():
    assert "März 2005" not in CV_EXTRACTION_REVIEW_SYSTEM_PROMPT
    assert "September 2005" not in CV_EXTRACTION_REVIEW_SYSTEM_PROMPT
