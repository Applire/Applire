# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-28 (Hazelnut build 5, delivery run 6): the letter twin of D-20. The VERIFIED
COVERAGE CHECK lists claimable posting terms that are literally absent. The letter
reviewers turned each one into "surface <term>", even where the letter already
stated the evidence, and the corrector then pasted the word in. One letter read
"I provide technical leadership by …, and technical strategy by …"; another read
"an agentic system—an AI agent and LLM-based solution—that …".

On a letter draft the block now says that a term is delivered by its evidence. A
term whose evidence the letter states raises no issue. A demand asks for missing
evidence, never for the word, a label or a gloss. Replay of the four captured
letter reviewer calls on gpt-6-luna, n=5 each: feedback naming the posting term
went from 17/20 to 3/20. Synthetic data only."""

from __future__ import annotations

from applire.services.keyword_ledger import (
    LETTER_COVERAGE_BY_EVIDENCE,
    coverage_reviewer_prompt_fn,
    render_verified_coverage_block,
)

_LEDGER = [{"concept": "Technical leadership", "status": "direct", "claimable": True,
            "surface_forms": ["Technical leadership"], "sources": ["keyword"], "fit_weight": 1.0,
            "evidence": ""}]
_LETTER = {"body": {"paragraphs": ["At Nordwerk I lead five engineers and set the platform roadmap."]}}
_CV = {"summary": "Engineering lead.", "skills": ["Python"], "work": []}


def _flat(s: str) -> str:
    return " ".join(s.split())


def test_a_letter_draft_is_told_the_evidence_delivers_the_term():
    prompt = coverage_reviewer_prompt_fn(lambda source, draft: "BASE", _LEDGER)("", _LETTER)
    assert "Technical leadership" in prompt
    assert LETTER_COVERAGE_BY_EVIDENCE in prompt
    p = _flat(prompt)
    assert "counts as delivered when the letter already states the candidate's evidence" in p
    assert "ask for that EVIDENCE, never for the word" in p
    assert 'as a label ("I provide <term> by …")' in p


def test_a_cv_draft_keeps_its_own_placement_rule_only():
    prompt = coverage_reviewer_prompt_fn(lambda source, draft: "BASE", _LEDGER)("", _CV)
    assert "WHERE A TERM IS SURFACED" in prompt
    assert LETTER_COVERAGE_BY_EVIDENCE not in prompt


def test_the_grounding_waiver_and_the_must_line_stay():
    block = render_verified_coverage_block(
        [{"concept": "Technical leadership", "surface_forms": ["Technical leadership"]}], letter=True)
    assert "You MUST set approved=false" in block
    assert "Grounding strictly outranks coverage" in block
    assert block.index("Grounding strictly outranks coverage") < block.index(LETTER_COVERAGE_BY_EVIDENCE)
    assert LETTER_COVERAGE_BY_EVIDENCE not in render_verified_coverage_block(
        [{"concept": "Technical leadership", "surface_forms": ["Technical leadership"]}])
