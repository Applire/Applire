# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Founder ruling D1-2 = A (783:L12, Hazelnut build 2) — the candidate's interview answer
is evidence on both documents.

The CV writer's STATED LIMITS block and the letter call a capability named INSIDE a denial
statement as something the candidate DOES have a strength; v1's CV reviewer said denial
text "NEVER grounds anything". The v2 reviewer doors (prose + terminal) keep the half that
protects the candidate — a denial never affirms what it denies — and drop the contradiction.
v1 is byte-identical (R-3 / R-10).
"""

from applire.prompts import review_cv_tailoring as r


def test_v1_doors_keep_the_v1_boundary():
    for prompt in (r.REVIEW_SYSTEM_PROMPT, r.TERMINAL_REVIEW_SYSTEM_PROMPT):
        assert r._V1_DENIAL_BOUNDARY in prompt
        assert r._V2_DENIAL_BOUNDARY not in prompt


def test_both_v2_doors_carry_the_aligned_boundary_and_never_the_old_one():
    for prompt in (r.V2_REVIEW_SYSTEM_PROMPT, r.V2_TERMINAL_REVIEW_SYSTEM_PROMPT):
        assert prompt.count(r._V2_DENIAL_BOUNDARY) == 1
        assert "NEVER grounds anything" not in prompt


def test_the_v2_boundary_still_forbids_affirming_the_denied_concept():
    text = r._V2_DENIAL_BOUNDARY
    assert "never grounds the concept it\ndenies" in text
    assert "never affirmed by its own denial" in text
    # and the DO NOT CLAIM half of the same paragraph is untouched
    assert "a DO NOT CLAIM concept in the skills list is a fabrication" in r.V2_REVIEW_SYSTEM_PROMPT


def test_v2_differs_from_v1_only_in_the_boundary():
    assert r._CHECKS.replace(r._V1_DENIAL_BOUNDARY, r._V2_DENIAL_BOUNDARY) == r._V2_CHECKS
    assert r.V2_REVIEW_SYSTEM_PROMPT.replace(r._V2_DENIAL_BOUNDARY, r._V1_DENIAL_BOUNDARY).count(
        r._CHECKS
    ) == 1
