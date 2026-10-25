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
    out = _prompt()
    assert "Still unevidenced" not in out and "only valid scope for a denial" not in out
    assert "That is a literal check, not a verdict:" in out
    # Category C (ruling V-7) carries the same literal-check statement, and no denial.
    c = _prompt("C")
    assert "Still unevidenced" not in c and "That is a literal check, not a verdict" in c
    assert "Offer NO denial choice" in c
    # R2-2 / I-3: Category B (unevidenced concepts) offers no denial choice either,
    # and a PARTIAL chip marks the leftover concepts as "not yet in the profile".
    assert "Offer NO denial choice" in out
    assert "does not yet document the concepts left over" in out
    assert "never that the candidate lacks them" in out
    assert "No word-for-word mention yet: Produktionsverantwortung" in c


def test_the_coverage_rule_counts_evidence_in_any_wording():
    flat = " ".join(_CHOICE_COVERAGE_RULES.split())
    # R2-2: the evidence-in-any-wording sentence survives the rewrite; the
    # "Evidence in ANY wording counts" phrase became the Teamleiter / Scrum Master
    # parenthesis inside the PARTIAL bullet.
    assert 'a "Teamleiter" role is evidence of team leadership' in flat
    assert 'a "Scrum Master" skill of agile methods' in flat
    assert "never one the profile summary or the earlier answers evidence in other words" in flat
