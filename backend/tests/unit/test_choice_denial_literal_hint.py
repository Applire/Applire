# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""787 M2 (UX audit 2026-10-20, founder ruling UX-1): starter chips offered a
Produktionsleiter "Ich habe bisher keine Erfahrung mit Produktionsverantwortung".

Root cause in the INPUT the prompt builder writes: `constituent_evidence` is a literal
surface check, and the hint stated its result as a verdict — "Still unevidenced:
Produktionsverantwortung … the only valid scope for a denial choice" — about a
profile that holds a Produktionsleiter role and a "Lean Management" skill. The
model obeyed (delivery-3 record 20). The hint now says what the check is (a
word-for-word check), and the coverage rule says evidence in any wording counts.
Replay of 6 captured Marcus drafts (gpt-5.6-luna, n=5/arm, thinking off): calls
with a false denial 29/30 -> 19/30, legitimate denials (Führungsspanne ~120 MA,
IFS, BRC) kept 10/10. Partial — reported as such.
"""
from applire.prompts.interview import _CHOICE_COVERAGE_RULES, build_question_prompt


def _prompt(category="B"):
    cluster = {"id": "c", "label": "Produktionssteuerung", "category": "C",
               "gaps": ["Produktionsverantwortung", "Instandhaltung"], "jd_skills": [], "jd_context": ""}
    profile = {"skills": [{"name": "Instandhaltung"}],
               "work_experience": [{"company": "Weberit", "role": "Produktionsleiter"}]}
    return build_question_prompt(cluster, profile, [], gap_category=category)


def test_the_hint_states_a_literal_check_never_a_verdict():
    c = _prompt("C")
    assert "Still unevidenced" not in c and "only valid scope for a denial" not in c
    assert "That is a literal check, not a verdict:" in c
    assert "Constituent concepts with no word-for-word mention in the profile summary: Produktionsverantwortung." in c
    # UAT-2: a concept stated in other words is evidenced -> DIRECT, and only a
    # concept with no evidence in ANY words gets the one honest bridge.
    flat = " ".join(c.split())
    assert "A concept the profile states in other words is evidenced: a DIRECT choice" in flat
    assert "Only a concept it shows no evidence for in any words gets bridges and the one honest bridge" in flat
    # MD-UAT-1: a B question is a confirmation — no honest bridge, no no.
    b = " ".join(_prompt("B").split())
    assert "CONFIRMATION question" in b
    assert "NO honest bridge and no other no on this question." in b


def test_the_coverage_rule_counts_evidence_in_any_wording():
    flat = " ".join(_CHOICE_COVERAGE_RULES.split())
    # UAT-2: the evidence-in-any-wording parenthesis moved to the DIRECT bullet.
    assert 'a "Teamleiter" role evidences team leadership' in flat
    assert 'a "Scrum Master" skill agile methods' in flat
    assert "only for a concept the profile summary itself evidences, in any words" in flat


