# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#545 / ADR-076 clause 9 — the terminal review owns the whole-document questions.

CI pins the WORDING and the structure only. Whether the model actually finds the tells is a
prompt EFFECT and is evidenced by a real-provider replay (ADR-062 clause 7), never by these
tests — see `Documents/Runs/Stracciatella/rc-2026-09-04/C2-cvfam/report.md`.

What CI *can* prove, and what these tests are for:

* the PROSE door is byte-for-byte unchanged (the door split moved text, it did not edit it);
* the new checks reach the TERMINAL door and only that door;
* the prompt does not contradict itself about the new checks' severity (ADR-062 clause 4 —
  ADR-083's Context item 1 is the measurement that the model obeys the exclusion, not the
  check, when the two disagree);
* `repetition` stays `minor` on both doors (ADR-082's decision, not ADR-083 clause 3's);
* both size gates still hold, and the terminal doors get their own ratchet so the next
  append meets the same question.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts import review_cover_letter as letter  # noqa: E402
from applire.prompts import review_cv_tailoring as cv  # noqa: E402


def _flat(text: str) -> str:
    """Collapse the prompt's own line wrapping before matching a phrase.

    These prompts are hand-wrapped at ~95 columns, so every phrase longer than a few
    words straddles a newline. A literal `in` assertion against the raw constant would
    pass or fail on where the wrap happens to fall — the test would be pinning the
    formatter, not the rule.
    """
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# The door split moved text; it did not edit it
# ---------------------------------------------------------------------------


def test_the_cv_prose_door_is_assembled_from_the_same_pieces_in_the_same_order():
    """`_CHECKS` + `_MANDATE_PROSE` must rejoin exactly as the former single blob read —
    including the blank line between them, which a naive split silently eats."""
    assert cv.REVIEW_SYSTEM_PROMPT == (
        cv._AUDITOR_INTRO + cv._SHAPE_NOTE_PROSE + cv._CHECKS + "\n" + cv._MANDATE_PROSE
        + cv._SCHEMA_AND_CLOSER
    )
    assert cv._CHECKS.rstrip().endswith("never a forbidden claim.")
    assert cv._MANDATE_PROSE.startswith("WHAT IS BLOCKING IN THIS PASS:")


def test_the_letter_prose_door_is_assembled_from_the_same_pieces_in_the_same_order():
    assert letter.REVIEW_SYSTEM_PROMPT == (
        letter._GROUNDING_INTRO + letter._CHECKS + "\n" + letter._MINOR_PROSE
        + letter._SCHEMA_AND_CLOSER
    )
    assert letter._MINOR_PROSE.startswith("WHAT IS `minor` HERE.")


def test_the_new_checks_are_absent_from_both_prose_doors():
    """A drafting round has no composed document, no render measure and no letter beside
    it — the clause-9 questions are unanswerable there, and #385's lesson is that a check
    aimed at what a shape cannot carry can only fail falsely and exhaust the loop."""
    for prompt in (cv.REVIEW_SYSTEM_PROMPT, letter.REVIEW_SYSTEM_PROMPT):
        assert "TERMINAL-ROUND CHECKS" not in prompt
        assert "CLAIM BALANCE" not in prompt
        assert "VOICE — does this read as written by a person?" not in prompt


# ---------------------------------------------------------------------------
# The checks reach the terminal doors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prompt,balance_no,voice_no",
    [(cv.TERMINAL_REVIEW_SYSTEM_PROMPT, "8.", "9."),
     (letter.TERMINAL_REVIEW_SYSTEM_PROMPT, "6.", "7.")],
)
def test_both_terminal_doors_carry_two_named_numbered_checks(prompt, balance_no, voice_no):
    """ADR-083 clause 1: a mandate is carried by NAMED CHECKS, not by a role sentence —
    an open role mandate measured 0/5 against one named check's 5/5."""
    assert f"{balance_no} CLAIM BALANCE" in prompt
    assert f"{voice_no} VOICE" in prompt


@pytest.mark.parametrize(
    "prompt", [cv.TERMINAL_REVIEW_SYSTEM_PROMPT, letter.TERMINAL_REVIEW_SYSTEM_PROMPT]
)
def test_the_claim_balance_check_names_the_under_direction_and_the_scoped_limit_class(prompt):
    """ADR-076 clause 4's 2026-08-17 amendment re-pointed the ADR-059 containment cost at
    clause 9, because clause 5 is scoped to claimable-backed material and a contained
    concept is by definition not claimable-backed. If this check does not name the class,
    that promise is unfulfillable a second time."""
    flat = _flat(prompt)
    assert "over AND under" in flat
    assert "no IFS/BRC experience, but ten years of ISO-9001 audit practice" in flat


@pytest.mark.parametrize(
    "prompt", [cv.TERMINAL_REVIEW_SYSTEM_PROMPT, letter.TERMINAL_REVIEW_SYSTEM_PROMPT]
)
def test_the_voice_check_carries_the_panels_own_tells(prompt):
    """The 2026-08-16 blind panel named these; they are the check's examples so it is not
    asking the model to invent a definition of 'sounds like a tool'."""
    low = _flat(prompt).lower()
    assert "mechanical uniformity" in low
    assert "requirement list" in low
    assert "[measure] from x to y" in low


@pytest.mark.parametrize(
    "prompt", [cv.TERMINAL_REVIEW_SYSTEM_PROMPT, letter.TERMINAL_REVIEW_SYSTEM_PROMPT]
)
def test_the_voice_check_protects_the_honesty_move_and_forbids_vaguening(prompt):
    """Both panel reviewers called the unprompted gap disclosure the strongest trust
    anchor. A voice check that lets the corrector trim it would trade the one thing that
    argued AGAINST a polish tool for the appearance of not being one."""
    low = _flat(prompt).lower()
    assert "unprompted admission of a gap or a limit" in low
    assert "vaguer than the truth is the worse defect" in low


@pytest.mark.parametrize(
    "prompt", [cv.TERMINAL_REVIEW_SYSTEM_PROMPT, letter.TERMINAL_REVIEW_SYSTEM_PROMPT]
)
def test_both_checks_are_bounded_at_one_finding_per_document(prompt):
    """ADR-083 clause 5's container bound, container = the composite: 1 of 5 arm-C runs
    split one cluster into three blocking issues on one location."""
    assert "AT MOST ONE finding per check" in _flat(prompt)


# ---------------------------------------------------------------------------
# Severity: stated once, contradicted nowhere (ADR-062 clause 4)
# ---------------------------------------------------------------------------


def test_the_cv_terminal_mandate_names_all_three_visibility_only_checks():
    m = _flat(cv._MANDATE_TERMINAL)
    assert "EXCEPT checks 2, 8 and 9" in m
    assert "VISIBILITY ONLY" in m


def test_the_letter_terminal_minor_paragraph_claims_checks_6_and_7():
    assert "Checks 6 and 7 above are `minor`" in _flat(letter._MINOR_TERMINAL)


@pytest.mark.parametrize(
    "prompt", [cv.TERMINAL_REVIEW_SYSTEM_PROMPT, letter.TERMINAL_REVIEW_SYSTEM_PROMPT]
)
def test_the_new_checks_state_their_own_severity_too(prompt):
    """#563's triage item 4 records this reviewer filing `severity: "blocking"` on check 2,
    whose own text says VISIBILITY ONLY — so the mandate paragraph alone is not enough and
    the check must say it as well."""
    flat = _flat(prompt)
    assert "VISIBILITY ONLY" in flat
    assert 'NEVER "blocking"' in flat or "NEVER `blocking`" in flat


def test_repetition_stays_minor_by_definition_on_both_cv_doors():
    """ADR-082's decision — repetition is DETECTED by the ATS audit and never repaired by
    the loop. ADR-083 clause 3 proposes the opposite and is deliberately NOT taken here;
    changing this line is that decision, not this one."""
    assert "repetition" in cv._MANDATE_PROSE
    assert "repetition" in cv._MANDATE_TERMINAL


def test_the_terminal_mandate_no_longer_says_truthfulness_is_the_whole_mandate():
    """The prose door's closer ('You are the check on whether it tells the truth') is
    exactly the sentence ADR-083 measured the model obeying over a check. It cannot stand
    unqualified beside two checks that are not about truth."""
    assert "You are the check on whether it tells the truth." in _flat(cv._MANDATE_PROSE)
    assert "You are the check on whether it tells the truth." not in _flat(cv._MANDATE_TERMINAL)
    assert "whether it represents the candidate" in _flat(cv._MANDATE_TERMINAL)


# ---------------------------------------------------------------------------
# Size gates
# ---------------------------------------------------------------------------


def test_the_writer_prompt_is_still_smaller_than_its_reviewer():
    """The 2026-07-30 audit's calibration, restated here because this change is the one
    that could break it."""
    from applire.prompts.cv_tailoring import SYSTEM_PROMPT

    assert len(SYSTEM_PROMPT) < len(cv.REVIEW_SYSTEM_PROMPT)


def test_the_prose_ratchets_are_untouched_because_the_prose_doors_are():
    assert len(cv.REVIEW_SYSTEM_PROMPT) < 10_700
    assert len(letter.REVIEW_SYSTEM_PROMPT) < 12_500


def test_the_cv_terminal_door_gets_its_own_ratchet():
    """The prose doors have had a ratchet since #580 / the letter's precedent; the
    terminal doors never did, which is how a 2,400-character append could land without
    meeting the question the ratchet asks. Clause 9 landed at 13,743 (was 11,317)."""
    assert len(cv.TERMINAL_REVIEW_SYSTEM_PROMPT) < 14_000, (
        f"CV terminal reviewer prompt is {len(cv.TERMINAL_REVIEW_SYSTEM_PROMPT)} chars — it is "
        "regrowing. Map the new content to an SF-WRITE row and REPLACE, do not append."
    )


def test_the_letter_terminal_door_gets_its_own_ratchet():
    """Clause 9 landed at 15,867 (was 13,928). This is the largest prompt in the family
    and the ceiling is deliberately tight."""
    assert len(letter.TERMINAL_REVIEW_SYSTEM_PROMPT) < 16_100, (
        f"letter terminal reviewer prompt is {len(letter.TERMINAL_REVIEW_SYSTEM_PROMPT)} chars — "
        "it is regrowing. Map the new content to an SF-WRITE row and REPLACE, do not append."
    )


def test_the_terminal_doors_still_forbid_a_length_finding():
    """#525's exhaustion fuel. Clause 6 is enforced deterministically by
    `rank_gate_missing_claimable`; no clause-9 check may reopen the length question."""
    assert "NEVER raise a length" in letter.TERMINAL_REVIEW_SYSTEM_PROMPT
    prompt = cv.build_terminal_review_prompt(
        "profile", {"summary": "s"}, page_count=2, target=2, condensation_exhausted=False
    )
    assert "NEVER raise a" in prompt and "length" in prompt
