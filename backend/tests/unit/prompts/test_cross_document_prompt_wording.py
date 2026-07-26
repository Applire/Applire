# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#270 Fix C — drift-guard tests for the cross-document prompt wording.

Pure string/wiring checks, no LLM — asserts the wording the run-5 blocker
needed exists, and that it survives future edits to these prompt modules.
"""
from __future__ import annotations


def test_review_system_prompt_has_cross_document_check_and_never_claim_gap_rule():
    from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT

    lower = REVIEW_SYSTEM_PROMPT.lower()
    assert "cross-document" in lower
    assert "claimable" in lower
    assert "never" in lower and "do-not-claim" in lower
    assert "scoped" in lower


def test_refinement_prompt_forbids_demoting_scoped_claim_to_bare_denial():
    from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT

    lower = COVER_LETTER_REFINEMENT_PROMPT.lower()
    assert "bare denial" in lower
    assert "scoped" in lower


def test_cover_letter_system_prompt_has_scoped_boundary_and_hard_requirement_rules():
    from applire.prompts.cover_letter import SYSTEM_PROMPT

    lower = SYSTEM_PROMPT.lower()
    assert "scoped boundaries" in lower
    assert "hard requirement" in lower
    assert "silence" in lower


def test_build_cover_letter_prompt_threads_scoped_boundary_block():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={},
        jd_text="We need a backend engineer.",
        pre_gen_inputs={},
        detected_language="en",
        scoped_boundary_block="=== SCOPED BOUNDARIES (deterministic — #270) ===\nSOME MARKER TEXT",
    )
    assert "SOME MARKER TEXT" in prompt


def test_build_cover_letter_prompt_omits_scoped_boundary_block_when_absent():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={},
        jd_text="We need a backend engineer.",
        pre_gen_inputs={},
        detected_language="en",
    )
    assert "SCOPED BOUNDARIES" not in prompt
