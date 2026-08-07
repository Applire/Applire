# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#289 — proximity-vs-contribution framing: drift-guard tests for the wording.

Charter run #5 (2026-07-25), Finding 7: the CV rendered "supporting €19bn
revenue" — a vault-grounded figure, correctly owned by the candidate's employer
and role, so the #254 figure guard and the Oracle both passed it. The blind
hiring manager called it *"a vague, unsubstantiated causal association rather
than a measured contribution"*.

Prompt-first triage (category B — never asked, but emittable): the writer's
rule 6 CLAIM STRENGTH forbids UPGRADING a verb ("supported" -> "led") and
ENLARGING a figure. Run 5 did neither: the verb was mirrored and the number was
exact. Nothing anywhere asked the model to state the candidate's RELATIONSHIP to
an organisation-scale figure, so the relationship was left implicit and the
reader supplied causation.

"Does this sentence overstate agency" is an ADR-062 clause-1 judgement, so the
rule lives in the prompt and the check lives in the reviewer — never in a
deterministic verb list. These tests pin the wording only; whether the model
COMPLIES needs a real-provider charter run (ADR-062 clause 7).

The inverse defect (#283 — grounded figures dropped, leaving prose vaguer than
the truth) is pinned too: neither the writer rule nor the reviewer check may be
readable as licence to remove a figure.
"""
from __future__ import annotations

import re


def _rule_slice(prompt: str, start_marker: str, end_marker: str) -> str:
    assert start_marker in prompt, f"missing rule marker {start_marker!r}"
    assert end_marker in prompt, f"missing rule marker {end_marker!r}"
    body = prompt.split(start_marker, 1)[1].split(end_marker, 1)[0]
    return body.lower()


# ── writer: applire.prompts.cv_tailoring (single-call path) ─────────────────


def test_writer_claim_strength_rule_states_the_agency_ceiling():
    """The narrow rule: prose may assert agency at most as strong as the vault
    statement's own verb/role."""
    from applire.prompts.cv_tailoring import SYSTEM_PROMPT

    rule6 = _rule_slice(SYSTEM_PROMPT, "6. CLAIM STRENGTH.", "7. SKILLS.")
    assert "agency" in rule6
    assert "organisation-scale" in rule6
    # the relationship must be STATED, not left to the reader
    assert "relationship" in rule6


def test_writer_rule_names_the_context_framing_and_forbids_implied_causation():
    """Issue #289 "Expected": state the real relationship, or frame the figure
    explicitly as context — never leave it implying causation."""
    from applire.prompts.cv_tailoring import SYSTEM_PROMPT

    rule6 = _rule_slice(SYSTEM_PROMPT, "6. CLAIM STRENGTH.", "7. SKILLS.")
    assert "setting" in rule6 or "scale of the operation" in rule6
    assert "causation" in rule6 or "causal" in rule6


def test_writer_rule_is_framing_only_and_never_licences_dropping_a_figure():
    """#283 is the inverse failure on the same axis. The fix for one must not
    manufacture the other."""
    from applire.prompts.cv_tailoring import SYSTEM_PROMPT

    rule6 = _rule_slice(SYSTEM_PROMPT, "6. CLAIM STRENGTH.", "7. SKILLS.")
    assert "never a reason to drop the figure" in rule6
    assert "opposite defect" in rule6


def test_writer_agency_rule_lives_only_in_the_claim_strength_rule():
    """ADR-067 clause 8: claim-strength calibration is stated ONCE, coherently.
    A second HOME for it elsewhere in the prompt — a rule 10, or a clause bolted
    onto rule 2 or 3 — is the accretion pathology this prompt was rebuilt to
    remove, and the "tension patched far from where it is created" smell."""
    from applire.prompts.cv_tailoring import SYSTEM_PROMPT

    rule6 = _rule_slice(SYSTEM_PROMPT, "6. CLAIM STRENGTH.", "7. SKILLS.")
    everywhere = len(re.findall(r"organisation-scale", SYSTEM_PROMPT, re.I))
    inside_rule6 = len(re.findall(r"organisation-scale", rule6, re.I))
    assert everywhere >= 1
    assert everywhere == inside_rule6


# ── writer: applire.prompts.cv_segmented (segmented path parity) ────────────


def test_segmented_core_rules_carry_the_same_agency_ceiling():
    """ADR-066/ADR-067: one logical operation, one contract — the two writer
    paths may not diverge on a truthfulness rule."""
    from applire.prompts.cv_segmented import _CORE_RULES

    low = _CORE_RULES.lower()
    assert "agency" in low
    assert "organisation-scale" in low
    assert "never drop the figure" in low


# ── reviewer: applire.prompts.review_cv_tailoring ──────────────────────────


def test_reviewer_oversell_check_covers_proximity_framing():
    """The reviewer already holds the CANDIDATE PROFILE verbatim as its source
    of truth (``build_review_prompt``), so the vault's own statement for each
    claim is already in its hands — the seam closes with an instruction to READ
    it for agency, not with a new fact block."""
    from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT

    check5 = _rule_slice(
        REVIEW_SYSTEM_PROMPT,
        "5. OVERSTATED CLAIM STRENGTH",
        "6. KEYWORD LEDGER",
    )
    assert "agency" in check5
    assert "organisation-scale" in check5
    # it must be aimed at the correctly-OWNED figure, the case every existing
    # control already passes
    assert "owned" in check5
    assert "profile statement" in check5


def test_reviewer_asks_for_reframing_never_for_removal():
    """The reviewer is the loop that can over-correct into #283. Pin the
    boundary."""
    from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT

    check5 = _rule_slice(
        REVIEW_SYSTEM_PROMPT,
        "5. OVERSTATED CLAIM STRENGTH",
        "6. KEYWORD LEDGER",
    )
    assert "framing" in check5
    assert "never ask for the figure to be removed" in check5


def test_reviewer_source_material_is_still_the_whole_profile():
    """The load-bearing precondition for the check above: no new reviewer input
    is wired, because the vault's own statements are already there."""
    from applire.prompts.review_cv_tailoring import build_review_prompt

    prompt = build_review_prompt(
        '{"work_experience": [{"company": "Acme", "responsibilities": ["x"]}]}',
        {"work": [{"id": "w1", "bullets": ["b"]}]},
    )
    assert "CANDIDATE PROFILE (source of truth)" in prompt
    assert '"work_experience"' in prompt
