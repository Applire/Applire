# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#766 — the job-analysis chain's writer, reviewer and corrector no longer
contradict each other on the rules the before/after row measured.

Contradiction sweep 2026-10-16 (``applire-prompt-first`` step 3), each pinned here
because a later edit to ONE of the three prompts re-opens it silently:

* C1-5 — the writer's schema offered ``'German (C1)'`` as the shape of
  ``language_requirement`` while reviewer check 5 blocks an invented CEFR level
  (glm-5.3-flash, before-arm: the reviewer flagged "German (C1), English (B2)" for
  a posting that says "Sehr gutes Deutsch und gutes Englisch").
* KldB — the writer asked for a "4- or 5-digit" code; the lookup holds none of 5
  digits, so every 5-digit answer was nulled with its label.
* C1-1 — the writer keeps concept terms in the posting's language; the reviewer
  called translation acceptable and the corrector was never told the language.
* C1-2 — the approval bar said "no basis in the source text … nothing else" while
  checks 1b, 2, 2b-2e and 5 block for other reasons, and "populate issues ONLY with
  material defects" contradicted the shared severity contract.
"""
from __future__ import annotations

import json
from pathlib import Path

from applire.prompts.job_analysis import SYSTEM_PROMPT as WRITER
from applire.prompts.review_job_analysis import (
    JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT as REVIEWER,
    build_job_analysis_retry_prompt,
    build_job_analysis_review_prompt,
)

KLDB = Path(__file__).resolve().parents[2] / "backend" / "applire" / "data" / "kldb2020.json"


def test_language_requirement_schema_does_not_invite_an_invented_cefr_level():
    line = next(ln for ln in WRITER.splitlines() if '"language_requirement"' in ln)
    assert "posting's own wording" in line and "never add a CEFR level" in line
    assert "CEFR level" in REVIEWER  # the reviewer check the writer now knows about


def test_kldb_digits_asked_for_exist_in_the_lookup():
    codes = [k for k in json.loads(KLDB.read_text(encoding="utf-8")) if k != "_meta"]
    longest = max(len(c) for c in codes)
    line = next(ln for ln in WRITER.splitlines() if '"berufsbild_code"' in ln)
    assert f"{longest}-digit" in line
    assert f"{longest + 1}-digit" not in line and "5-digit" not in line


def test_reviewer_no_longer_names_translation_as_acceptable_normalisation():
    assert "translating a German requirement phrase into an\nEnglish skill name" not in REVIEWER
    user = build_job_analysis_review_prompt("Wir suchen eine Controllerin (m/w/d).", {"role_title": "Controllerin"})
    assert "translation are NOT defects" not in user


def test_the_corrector_is_told_the_postings_language():
    de = build_job_analysis_retry_prompt({"role_title": "Controller"}, "fix it",
                                         "Wir suchen eine erfahrene Controllerin für unser Werk in Freiburg. "
                                         "Sie verantworten den Monatsabschluss nach HGB.")
    en = build_job_analysis_retry_prompt({"role_title": "Engineer"}, "fix it",
                                         "We are looking for a senior backend engineer to own our payments platform. "
                                         "You will design and operate the services.")
    assert "POSTING LANGUAGE: German" in de and "POSTING LANGUAGE: English" in en
    assert de.index("POSTING LANGUAGE") < de.index("REVIEW FEEDBACK")


def test_the_approval_bar_defers_to_the_checks_and_the_severity_contract():
    bar = REVIEWER.split("APPROVAL BAR", 1)[1].split("VERBATIM GROUNDING RULE", 1)[0]
    assert "checks 1-5" in bar and "with no basis in the source text" not in bar
    assert 'Populate "issues"\nONLY with material defects' not in REVIEWER
    assert '"minor"' in bar
    tail = REVIEWER.split("WHAT IS BLOCKING IN THIS PASS", 1)[1][:400]
    assert "checks 1-5" in tail and "with no basis in the source text" not in tail
    assert REVIEWER.count("education bar") >= 2  # test_uat_education_requirement's pin


def test_gap_prompt_carries_no_developer_prose_or_duplicate_guideline():
    """#766 chain 2: the gap SYSTEM_PROMPT keeps its rules; the incident sentence,
    the issue/ADR tags and the guideline that restated the schema's keyword_gaps
    line are gone (the schema line itself stays)."""
    from applire.prompts.gap_analysis import SYSTEM_PROMPT as GAP

    assert "Key-User" not in GAP and "(#328)" not in GAP and "(ADR-070)" not in GAP
    assert GAP.count("keyword_gaps") == 1 and '"keyword_gaps"' in GAP
    assert "classification_note" in GAP and "PROVENANCE:" in GAP and "ATTESTED PROSE:" in GAP
