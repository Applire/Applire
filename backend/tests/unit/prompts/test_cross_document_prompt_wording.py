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


def test_refinement_prompt_forbids_inventing_a_limit_and_dropping_one():
    """Both directions. The corrector used to be told only "never demote a scoped
    claim to a bare denial"; charter run #8 showed the opposite failure — it
    ADDED a limit nothing in the vault stated, on the candidate's own strongest
    evidence."""
    from applire.prompts.review_cover_letter import COVER_LETTER_REFINEMENT_PROMPT

    lower = COVER_LETTER_REFINEMENT_PROMPT.lower()
    assert "stated_limits" in lower
    assert "never invent a limit" in lower
    assert "strength, not a limit" in lower
    assert "claimable" in lower


def test_cover_letter_system_prompt_has_stated_limit_and_hard_requirement_rules():
    from applire.prompts.cover_letter import SYSTEM_PROMPT

    lower = SYSTEM_PROMPT.lower()
    assert "stated limits" in lower
    assert "never invent a limit" in lower
    assert "hard requirement" in lower
    assert "silence" in lower


def test_build_cover_letter_prompt_threads_stated_limits_block():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={},
        jd_text="We need a backend engineer.",
        pre_gen_inputs={},
        detected_language="en",
        stated_limits_block="=== STATED LIMITS (the candidate's own words, verbatim) ===\nSOME MARKER TEXT",
    )
    assert "SOME MARKER TEXT" in prompt


def test_build_cover_letter_prompt_omits_stated_limits_block_when_absent():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={},
        jd_text="We need a backend engineer.",
        pre_gen_inputs={},
        detected_language="en",
    )
    assert "STATED LIMITS" not in prompt


def test_build_cover_letter_prompt_threads_unaddressed_requirements_block():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={},
        jd_text="We need a backend engineer.",
        pre_gen_inputs={},
        detected_language="en",
        unaddressed_requirements_block=(
            "=== UNADDRESSED HARD REQUIREMENTS (deterministic — #270(c)) ===\n"
            "SOME UNADDRESSED MARKER"
        ),
    )
    assert "SOME UNADDRESSED MARKER" in prompt


def test_build_cover_letter_prompt_omits_unaddressed_requirements_block_when_absent():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    prompt = build_cover_letter_prompt(
        cv_data={},
        jd_text="We need a backend engineer.",
        pre_gen_inputs={},
        detected_language="en",
    )
    assert "UNADDRESSED HARD REQUIREMENTS" not in prompt
