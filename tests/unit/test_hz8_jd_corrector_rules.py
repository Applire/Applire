# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""787:59 + 787:121 (Hazelnut build 8, WP-I) — the JD corrector and reviewer agree with
the extractor about what a concept term is and which language it is in.

* 787:59 — the corrector re-reads the posting every round. It carried no NOT-A-CONCEPT
  rule and licensed "you may add … a concept term", so a correction round kept (or
  re-minted) degrees and traits the extraction round excludes. Replay (forced correction
  round, KION draft with the delivered non-skills, gpt-5.6-luna n=5 / Sonnet 5.5 n=3 per
  arm): before 9 of 9 non-skills kept in 8/8 runs, after removed in 7/8 runs; 0 grounded
  terms dropped in any after-arm run.
* 787:121 — the reviewer named translation acceptable normalisation while the extractor
  forbids translation (OUTPUT LANGUAGE) and the corrector is told the posting language.
"""
from __future__ import annotations

from applire.prompts import job_analysis, review_job_analysis
from applire.prompts.job_analysis import NOT_A_CONCEPT_TERM_RULE
from applire.prompts.review_job_analysis import (
    JOB_ANALYSIS_REFINEMENT_PROMPT as CORRECTOR,
    JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT as REVIEWER,
    build_job_analysis_review_prompt,
)


def test_extractor_and_corrector_carry_the_same_exclusion_list():
    assert NOT_A_CONCEPT_TERM_RULE.startswith("NOT A CONCEPT TERM")
    assert NOT_A_CONCEPT_TERM_RULE in job_analysis.SYSTEM_PROMPT
    assert NOT_A_CONCEPT_TERM_RULE in CORRECTOR
    # the closed classes the extraction excludes all reach the corrector
    for probe in ("Master's degree", "Willingness to learn", "Fluent English", "Learning budget",
                  "KION GenAI Platform"):
        assert probe in CORRECTOR


def test_corrector_no_longer_licenses_adding_a_term_the_review_did_not_name():
    flat = " ".join(CORRECTOR.split())
    assert "you may add, remove, or correct a concept term" not in flat
    assert "Never add a term the review does not name" in flat


def test_reviewer_never_names_translation_as_acceptable_normalisation():
    flat = " ".join(REVIEWER.split()).lower()
    assert "translating a german requirement phrase" not in flat
    assert "paraphrase, translation, tidying" not in flat
    assert "never ask for a term to be translated" in flat
    user = build_job_analysis_review_prompt("We need Python.", {"required_skills": ["Python"]})
    assert "translation are NOT defects" not in user
    assert "Reasonable normalisation is NOT a defect." in user


def test_corrector_rule_sits_inside_the_rules_list_not_after_the_output_line():
    assert CORRECTOR.index(NOT_A_CONCEPT_TERM_RULE) < CORRECTOR.index("Output ONLY the corrected JSON")
    assert review_job_analysis.NOT_A_CONCEPT_TERM_RULE is NOT_A_CONCEPT_TERM_RULE
