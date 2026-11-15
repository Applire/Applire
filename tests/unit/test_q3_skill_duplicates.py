# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q3 (b): near-duplicate skill chips (bench 2026-11-15, base 956bad2f, luna).

* "Work preparation" shipped twice (marcus cross_de2en run 1): the CV language pass
  translates a bilingual twin into its sibling's EXACT wording, relying on "the
  document's own dedup" that v2 no longer runs — compose_v2 now drops an identical
  entry (identity fact).
* "SAP" beside "SAP PP"/"SAP MM" through four rounds (marcus cross_de2en run 0): the
  advisory finding named only "SAP" — it now names the pair.
* The writer and corrector prompts carry the product-and-modules shape and the
  one-entry rule for coverage demands.
"""
from __future__ import annotations

from applire.services.cv_v2 import (
    ComposeNotes, V2CheckContext, compose_v2, unique_skill_entries, v2_checks,
)


def test_identical_entries_collapse_to_the_first():
    assert unique_skill_entries(
        ["Work preparation", "SAP", "work  preparation", "Work preparation", "SAP PP"]
    ) == ["Work preparation", "SAP", "SAP PP"]
    # near duplicates are not identity: they stay (the model's judgement)
    assert unique_skill_entries(["SAP", "SAP PP", "React", "React Native"]) == [
        "SAP", "SAP PP", "React", "React Native"]


_VAULT = {
    "personal_info": {"name": "Marcus"},
    "work_experience": [{"id": "w1", "company": "Weberit", "role": "Produktionsleiter",
                         "start_date": "2019-01",
                         "responsibilities": ["Einführung von SAP PP und SAP MM in der Fertigung."]}],
    "skills": [{"name": "SAP", "status": "confirmed"}, {"name": "Arbeitsvorbereitung", "status": "confirmed"}],
    "education": [],
}


def test_compose_v2_ships_an_identical_entry_once():
    prose = {"summary": "x", "work": [{"id": "w1", "bullets": ["Introduced SAP PP and SAP MM."], "projects": []}],
             "skills": ["Work preparation", "SAP", "Work preparation"]}
    out = compose_v2(prose, _VAULT)
    assert out.skills == ["Work preparation", "SAP"]


def test_the_duplicate_finding_names_the_entry_it_repeats():
    prose = {"summary": "x", "work": [{"id": "w1", "bullets": ["Introduced SAP PP and SAP MM."], "projects": []}],
             "skills": ["SAP", "SAP PP", "SAP MM", "Arbeitsvorbereitung"]}
    notes = ComposeNotes()
    composed = compose_v2(prose, _VAULT, notes=notes)
    ctx = V2CheckContext(profile_json=_VAULT, keyword_ledger=[], job_dict={}, language="en")
    f = {x.check: x for x in v2_checks(prose, composed, notes, ctx)}["cv.skill_duplicate"]
    assert "“SAP” beside “SAP PP”, “SAP MM”" in f.text
    assert f.blocking is False


def test_the_prompts_carry_the_one_entry_shapes():
    from applire.prompts.cv_tailoring import SYSTEM_PROMPT, V2_REFINEMENT_ADDENDUM, V2_SYSTEM_PROMPT

    assert '"SAP (PP, MM)", never "SAP", "SAP PP" and "SAP MM"' in V2_SYSTEM_PROMPT
    assert "SAP (PP, MM)" not in SYSTEM_PROMPT  # v1 sits under its reviewer (test_545)
    flat = " ".join(V2_REFINEMENT_ADDENDUM.split())
    assert "is met by that entry; show it in a bullet, never add a second entry for it" in flat


# ── MD-Q7 F3 (adversarial 1b, probe p1.py): merged chips are read as modules ──
def _module_findings(vault_skills, chips, bullets=("Arbeit in der Fertigung.",)):
    vault = {"personal_info": {"name": "M"}, "work_experience": [{
        "id": "w1", "company": "W", "role": "R", "start_date": "2019-01", "responsibilities": list(bullets)}],
        "skills": [{"name": s, "status": "confirmed"} for s in vault_skills], "education": []}
    prose = {"summary": "x", "work": [{"id": "w1", "bullets": ["Did it."], "projects": []}], "skills": chips}
    notes = ComposeNotes()
    composed = compose_v2(prose, vault, notes=notes)
    ctx = V2CheckContext(profile_json=vault, keyword_ledger=[], job_dict={}, language="en")
    return {f.check: f for f in v2_checks(prose, composed, notes, ctx)}


def test_a_merged_chip_with_an_invented_module_is_a_blocking_finding_md_q7_f3():
    for vault_skills, chip in ((["SAP", "SAP MM"], "SAP (PP, MM)"), (["Excel"], "Excel (VBA)"),
                               (["Java", "JavaScript"], "Java (Script)"), (["AWS"], "AWS (Lambda)"),
                               (["SQL"], "SQL (NoSQL)")):
        f = _module_findings(vault_skills, [chip]).get("cv.skill_module_unbacked")
        assert f is not None and f.blocking is True and chip in f.subjects, chip


def test_a_merged_chip_of_recorded_modules_passes_md_q7_f3():
    assert "cv.skill_module_unbacked" not in _module_findings(["SAP", "SAP MM", "SAP PP"], ["SAP (PP, MM)"])
    assert "cv.skill_module_unbacked" not in _module_findings(["SAP PP/MM"], ["SAP (PP, MM)"])


def test_the_oracle_reads_a_merged_chip_as_its_modules_md_q7_f3():
    from applire.services.oracle.matchers.grounding import ground_skill_claim
    from applire.services.oracle.matchers.vault import build_vault_index

    idx = build_vault_index({"skills": [{"name": "SAP"}, {"name": "SAP MM"}], "work_experience": []})
    assert ground_skill_claim("SAP (PP, MM)", idx) is None  # was: grounded on "SAP MM"
    idx = build_vault_index({"skills": [{"name": "SAP"}, {"name": "SAP MM"}, {"name": "SAP PP"}],
                             "work_experience": []})
    assert ground_skill_claim("SAP (PP, MM)", idx) is not None


def test_the_ats_keyword_check_reads_the_modules_md_q7_f3():
    from applire.services.ats_audit import _norm, _with_module_chip_forms, keyword_present

    t = _with_module_chip_forms(_norm("Skills: SAP (PP, MM), Excel (VBA)"), ["SAP (PP, MM)", "Excel (VBA)"])
    assert all(keyword_present(k, t) for k in ("SAP PP", "SAP MM", "SAP PP/MM", "Excel VBA"))
    assert not keyword_present("SAP SD", t)


def test_distinct_skills_are_not_offered_for_joining_md_q7_f3():
    from applire.services.cv_v2 import product_module_joins

    assert product_module_joins(["SAP", "SAP PP", "SAP MM"]) == ["SAP (PP, MM)"]
    for pair in (["Scrum", "Scrum Master"], ["Lean", "Lean Six Sigma"], ["Python", "Python 3"],
                 ["Java", "Java EE"], ["React", "React Native"], ["AWS", "AWS Lambda"]):
        assert product_module_joins(pair) == [], pair
    f = _module_findings(["Scrum", "Scrum Master"], ["Scrum", "Scrum Master"],
                         bullets=("Scrum Master im Team, Scrum eingeführt.",)).get("cv.skill_duplicate")
    if f is not None:
        assert "(" not in f.text.split("two distinct skills both stay.")[-1]


def test_the_cv_audit_seam_reads_the_modules_md_q7_f3():
    """Seam: ``_audit_cv_text`` hands the module forms to the keyword coverage."""
    from applire.services.ats_audit import _audit_cv_text

    prose = {"summary": "x", "work": [{"id": "w1", "bullets": ["Did it."], "projects": []}],
             "skills": ["SAP (PP, MM)"]}
    composed = compose_v2(prose, _VAULT)
    report = _audit_cv_text("Marcus\nSkills: SAP (PP, MM)", composed, ["SAP PP", "SAP SD"])
    assert "SAP PP" in report.keywords.present
    assert "SAP SD" in report.keywords.missing


# ── MD-Q9 R5 (re-check at 7017f7ab): a module counts only under its own base ──
_R5 = [(["SAP", "AWS Lambda"], "SAP (Lambda)"), (["Oracle", "SAP PP"], "Oracle (PP)"),
       (["Excel", "SQL Server"], "Excel (Server)")]


def test_a_module_held_under_another_base_does_not_count_md_q9_r5():
    from applire.services.oracle.matchers.grounding import ground_skill_claim
    from applire.services.oracle.matchers.vault import build_vault_index

    for vault_skills, chip in _R5:
        f = _module_findings(vault_skills, [chip]).get("cv.skill_module_unbacked")
        assert f is not None and f.blocking is True and chip in f.subjects, chip
        idx = build_vault_index({"skills": [{"name": s} for s in vault_skills], "work_experience": []})
        assert ground_skill_claim(chip, idx) is None, chip


def test_a_chip_with_nested_parentheses_never_grounds_on_a_part_md_q9_r5():
    """Re-check probe: "SAP (PP (MM))" is not a merged chip and grounded on the
    near-dupe arm over a vault holding "SAP" and "SAP MM" (PP invented)."""
    from applire.services.oracle.matchers.grounding import ground_skill_claim, module_chip_parts
    from applire.services.oracle.matchers.vault import build_vault_index

    assert module_chip_parts("SAP (PP (MM))") is None
    idx = build_vault_index({"skills": [{"name": "SAP"}, {"name": "SAP MM"}], "work_experience": []})
    assert ground_skill_claim("SAP (PP (MM))", idx) is None
    assert ground_skill_claim("SAP", idx) is not None  # the bare product still grounds


def test_the_base_s_own_initials_are_its_abbreviation_not_a_module_md_q9_r5():
    from applire.services.oracle.matchers.grounding import ground_skill_claim
    from applire.services.oracle.matchers.vault import build_vault_index

    idx = build_vault_index({"skills": [{"name": "Key Performance Indicator"}], "work_experience": []})
    assert ground_skill_claim("Key Performance Indicator (KPI)", idx) is not None
    assert "cv.skill_module_unbacked" not in _module_findings(
        ["Key Performance Indicator"], ["Key Performance Indicator (KPI)"])
