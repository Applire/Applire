# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-20 (Hazelnut build 5, delivery run 4): the CV terminal review hit its 4-round
cap on two controls that undo each other. The VERIFIED COVERAGE CHECK demanded a
claimable posting term ("Technical strategy") without saying where; the corrector
put it into the skills list; the skills-shape chain removed it as a posting phrase
(``cv.skill_untied``); the next round demanded it again as absent. On a CV draft the
demand now names the place — the sentence that carries the evidence, never a
skills-list entry. A letter has no skills list and keeps the block as it was.
Synthetic data only."""

from __future__ import annotations

from applire.services.keyword_ledger import coverage_reviewer_prompt_fn, render_verified_coverage_block

_LEDGER = [{"concept": "Technical strategy", "status": "direct", "claimable": True,
            "surface_forms": ["Technical strategy"], "sources": ["keyword"], "fit_weight": 1.0,
            "evidence": ""}]
_PLACE = "WHERE A TERM IS SURFACED"


def test_a_cv_draft_demand_names_the_place_and_rules_out_the_skills_list():
    fn = coverage_reviewer_prompt_fn(lambda source, draft: "BASE", _LEDGER)
    prompt = fn("", {"summary": "Engineering lead.", "skills": ["Python"], "work": []})
    assert "Technical strategy" in prompt
    assert _PLACE in prompt
    assert "Never ask for a term as a skills-list entry" in prompt


def test_a_letter_draft_keeps_the_block_without_the_skills_rule():
    fn = coverage_reviewer_prompt_fn(lambda source, draft: "BASE", _LEDGER)
    prompt = fn("", {"body": {"paragraphs": ["I lead engineering teams."]}})
    assert "Technical strategy" in prompt
    assert _PLACE not in prompt


def test_nothing_absent_means_no_block_at_all():
    fn = coverage_reviewer_prompt_fn(lambda source, draft: "BASE", _LEDGER)
    assert fn("", {"summary": "I set the technical strategy.", "skills": [], "work": []}) == "BASE"


def test_the_renderer_default_is_unchanged():
    entries = [{"concept": "Technical strategy", "surface_forms": ["Technical strategy"]}]
    assert _PLACE not in render_verified_coverage_block(entries)
    assert _PLACE in render_verified_coverage_block(entries, skills_list=True)
