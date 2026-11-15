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
