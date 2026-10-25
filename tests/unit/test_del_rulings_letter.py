# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Founder rulings DEL-1..3 (Hazelnut build 1, 2026-10-18) on the letter's limits.

The delivery letter (logged rows 69-78) wrote three interview non-answers in as
admissions ("Eine konkrete Investitionssumme kann ich nicht nennen") and carried
investment/leadership gap sentences the reviewer had demanded as "honest transfer or
de-emphasis". The three rulings, each pinned here:

* DEL-1 (v1 AND v2): a statement that only withholds detail is not a limit.
* DEL-2 (ADR-076 positioning contract): de-emphasis = the letter leaves the lacked
  requirement out; a lack is stated ONLY where the candidate's own recorded denial
  names it.
* DEL-3 (ADR-075 amended): which denials reach the writer's obligation is an INPUT
  FILTER in code, matched on this posting's ledger — the rest stay constraints.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest  # noqa: E402

from applire.prompts import cover_letter as writer  # noqa: E402
from applire.prompts import review_cover_letter as reviewer  # noqa: E402
from applire.services import cross_document as xd  # noqa: E402


def _flat(text: str) -> str:
    return " ".join(text.split())


IFS = ("Nein, mit IFS oder BRC habe ich keine Erfahrung — das ist eine ehrliche Lücke. "
       "Zehn Jahre ISO-9001-Auditpraxis bringe ich mit.")
INVEST = ("Bei Investitionen war ich beim MES-Projekt beteiligt. Eine konkrete "
          "Investitionssumme dazu kann ich nicht nennen.")
SPAN = "Die dauerhafte Spanne von 120 wäre für mich der nächste Schritt."
DENIED = [
    {"concept": "IFS", "statement": IFS},
    {"concept": "Investitionssumme", "statement": INVEST},
    {"concept": "Führungsspanne", "statement": SPAN},
]
LEDGER = [
    {"concept": "IFS", "status": "denied", "fit_weight": 0.5},
    # the posting asks for investment planning, but the candidate never DENIED it:
    # a floored gap row is not a denial row (#486)
    {"concept": "Investitionsplanung", "status": "gap", "fit_weight": 1.0},
    {"concept": "Lean-Produktion", "status": "direct", "fit_weight": 1.0},
]


# ── DEL-3: the input filter ─────────────────────────────────────────────────

def test_only_a_denial_of_this_postings_ledger_row_is_owed():
    assert xd.select_jd_relevant_limits(DENIED, LEDGER) == [IFS]
    assert xd.letter_constraint_only_limits(DENIED, LEDGER) == [INVEST, SPAN]


def test_owed_and_constraint_only_partition_every_statement():
    owed = xd.select_jd_relevant_limits(DENIED, LEDGER)
    rest = xd.letter_constraint_only_limits(DENIED, LEDGER)
    assert sorted(owed + rest) == sorted(xd.collect_stated_limits(DENIED))
    assert not set(owed) & set(rest)


def test_a_denial_whose_concept_is_no_ledger_row_is_never_owed():
    # same statement, concept renamed so it matches no ledger row
    denied = [{"concept": "IFS Food", "statement": IFS}]
    assert xd.select_jd_relevant_limits(denied, LEDGER) == []
    assert xd.letter_constraint_only_limits(denied, LEDGER) == [IFS]


def test_no_ledger_means_nothing_is_owed():
    assert xd.letter_constraint_only_limits(DENIED, None) == [IFS, INVEST, SPAN]


def test_a_jd_denial_past_the_cap_stays_a_constraint():
    denied = [{"concept": f"C{i}", "statement": f"C{i} habe ich nie gemacht."} for i in range(4)]
    ledger = [{"concept": f"C{i}", "status": "denied", "fit_weight": 1.0 - i / 10} for i in range(4)]
    assert len(xd.select_jd_relevant_limits(denied, ledger)) == 3
    assert xd.letter_constraint_only_limits(denied, ledger) == ["C3 habe ich nie gemacht."]


def test_the_letter_copy_of_the_stated_limits_is_constraint_only():
    block = xd.render_stated_limits_block([INVEST], for_letter=True)
    flat = _flat(block)
    assert "CONSTRAINT ONLY" in block
    assert "the letter NEVER mentions them" in flat
    assert "never write a disclosure, a gap or a 'cannot claim' sentence" in flat
    assert INVEST in block
    # the transfer framing the writer read as an invitation is gone from this copy
    assert "an honest denial names the adjacent strengths" not in flat


def test_the_cv_copy_of_the_stated_limits_is_unchanged():
    """CV writer rule 3a keeps its own block — the rulings are the LETTER's."""
    block = xd.render_stated_limits_block([INVEST])
    assert "CONSTRAINT ONLY" not in block
    assert "kann ich nicht nennen" not in block.split("  - ")[0]
    assert "These are the ONLY limits the vault holds." in _flat(block)


# ── DEL-1: withholding is not a limit ────────────────────────────────────────

_DEL1 = "is NOT a limit"


@pytest.mark.parametrize("text", [
    xd.render_required_limits_block([IFS]),
    xd.render_stated_limits_block([INVEST], for_letter=True),
    writer.SYSTEM_PROMPT,
    writer.build_system_prompt("de", pipeline="v2"),
    writer.build_system_prompt("en", pipeline="v2"),
    reviewer.COVER_LETTER_REFINEMENT_PROMPT,
], ids=["required-block", "letter-stated-limits", "writer-v1", "writer-v2-de", "writer-v2-en",
        "corrector"])
def test_del1_reaches_every_letter_writer_surface(text):
    flat = _flat(text)
    assert "kann ich nicht nennen" in flat
    assert _DEL1 in flat
    assert "never write the withholding into the letter" in flat


def test_del1_reaches_the_reviewer_and_the_review_constraint():
    from applire.services.cover_letter import _STATED_LIMITS_CONSTRAINT

    assert "writing it in IS a finding (DEL-1)" in _flat(reviewer.REVIEW_SYSTEM_PROMPT)
    assert _DEL1 in _STATED_LIMITS_CONSTRAINT


# ── DEL-2: de-emphasis leaves the requirement out ────────────────────────────

def test_the_unaddressed_block_no_longer_offers_a_named_gap_de_emphasis():
    block = xd.render_unaddressed_hard_requirements_block([
        {"concept": "Investitionsplanung", "evidence": "Budget ca. 6 Mio. €"},
        {"concept": "Arbeitsvorbereitung", "evidence": "", "adjacent_evidence": "Feinplanung"},
    ])
    flat = _flat(block)
    assert "honest de-emphasis that names the gap" not in flat
    assert "Silence is not one of the options" not in flat
    assert "DE-EMPHASIS, which means the letter leaves the requirement out" in flat
    assert "A requirement listed here that the letter leaves out is NOT a finding" in flat
    assert "only where the entry says the candidate was ASKED AND SAID NO" in flat


@pytest.mark.parametrize("text", [
    writer.SYSTEM_PROMPT, writer.build_system_prompt("de", pipeline="v2"),
], ids=["v1", "v2"])
def test_the_writer_ladder_names_a_gap_only_from_a_recorded_denial(text):
    flat = _flat(text)
    # RULING L-1 = A (2026-10-24): the gap is named only in rung 2's fused form
    # and only where the candidate's denial names it; with neither a scoped claim
    # nor a transfer the letter leaves it out (was: a named gap as rung 3).
    assert "the gap named, if at all, only in (b)'s fused form and only where a REQUIRED: STATED LIMITS entry names it" in flat
    assert "the letter LEAVES IT OUT — never a bare admission" in flat
    assert "only where NEITHER holds AND their own recorded denial names it" not in flat
    assert "the letter LEAVES IT OUT" in flat
    assert "Silence is still never an option" not in flat


def test_the_reviewer_stops_demanding_named_gap_transfer_sentences():
    for prompt in (reviewer.REVIEW_SYSTEM_PROMPT, reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT):
        flat = _flat(prompt)
        assert "A requirement lacked WITHOUT a recorded denial owes no sentence" in flat
        assert "never demand one (DEL-2)" in flat
        assert "an honest gap only when the candidate's own recorded denial names it" in flat
        assert "the ledger marks it so or a STATED LIMIT disclaims it" not in flat
    # the voice check (terminal door only) protects the RECORDED admission alone
    assert "never touch an admission of a recorded denial" in _flat(
        reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT)


def test_the_corrector_never_writes_a_lack_the_candidate_did_not_state():
    flat = _flat(reviewer.COVER_LETTER_REFINEMENT_PROMPT)
    assert "NEVER WRITE A LACK THE CANDIDATE DID NOT STATE" in flat
    assert "ONLY where their own recorded denial names it" in flat
    assert "surface the adjacent strength on its own merits or leave the requirement out" in flat
