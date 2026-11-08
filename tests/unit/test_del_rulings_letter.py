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


# ── DEL-3: the input filter (narrowed by G-1, 2026-11-04) ────────────────────
# Which statements the letter SAYS is code: only a statement of a NAMED key-requirement
# gap (`gaps_to_name`). Every other statement stays a constraint the letter never states.

JD = "Wir suchen Erfahrung mit IFS und Investitionsplanung."


def _named(ledger, denied, jd=JD):
    return xd.gaps_to_name(xd.key_requirement_gaps(ledger, denied, jd))


def test_only_the_named_gaps_statement_leaves_the_constraint_block():
    ledger = [{"concept": "IFS", "status": "denied", "claimable": False,
               "fit_weight": 1.0, "sources": ["required"]}]
    named = _named(ledger, DENIED)
    assert [g.label for g in named] == ["IFS"]
    assert xd.letter_constraint_only_limits(DENIED, named) == [INVEST, SPAN]


def test_named_and_constraint_only_partition_every_statement():
    ledger = [{"concept": "IFS", "status": "denied", "claimable": False,
               "fit_weight": 1.0, "sources": ["required"]}]
    named = _named(ledger, DENIED)
    rest = xd.letter_constraint_only_limits(DENIED, named)
    said = [g.statement for g in named if g.statement]
    assert sorted(said + rest) == sorted(xd.collect_stated_limits(DENIED))
    assert not set(said) & set(rest)


def test_a_nice_to_have_or_unrelated_denial_is_never_named():
    # IFS only at nice-to-have weight, the other denials match no ledger row
    ledger = [{"concept": "IFS", "status": "denied", "claimable": False,
               "fit_weight": 0.5, "sources": ["nice_to_have"]}]
    named = _named(ledger, DENIED)
    assert named == []
    assert xd.letter_constraint_only_limits(DENIED, named) == [IFS, INVEST, SPAN]


def test_no_named_gap_means_every_statement_is_a_constraint():
    assert xd.letter_constraint_only_limits(DENIED, None) == [IFS, INVEST, SPAN]
    assert xd.letter_constraint_only_limits(DENIED, []) == [IFS, INVEST, SPAN]


def test_a_third_gap_stays_a_constraint():
    denied = [{"concept": f"C{i}", "statement": f"C{i} habe ich nie gemacht."} for i in range(3)]
    ledger = [{"concept": f"C{i}", "status": "denied", "claimable": False,
               "fit_weight": 1.0, "sources": ["required"]} for i in range(3)]
    named = _named(ledger, denied, "C0 C1 C2")
    assert len(named) == 1  # three gaps -> only the most urgent
    assert xd.letter_constraint_only_limits(denied, named) == [
        "C1 habe ich nie gemacht.", "C2 habe ich nie gemacht."]


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


def _ladder_block() -> str:
    ledger = [{"concept": "IFS", "status": "denied", "claimable": False,
               "fit_weight": 1.0, "sources": ["required"]}]
    gaps = xd.key_requirement_gaps(ledger, DENIED, JD)
    return xd.render_gap_ladder_block(gaps, xd.gaps_to_name(gaps), "de")


@pytest.mark.parametrize("text", [
    xd.render_stated_limits_block([INVEST], for_letter=True),
    writer.SYSTEM_PROMPT,
    writer.build_system_prompt("de", pipeline="v2"),
    writer.build_system_prompt("en", pipeline="v2"),
    reviewer.COVER_LETTER_REFINEMENT_PROMPT,
], ids=["letter-stated-limits", "writer-v1", "writer-v2-de", "writer-v2-en",
        "corrector"])
def test_del1_reaches_every_letter_writer_surface(text):
    flat = _flat(text)
    assert "kann ich nicht nennen" in flat
    assert _DEL1 in flat
    assert "never write the withholding into the letter" in flat


def test_del1_reaches_the_reviewer_and_the_review_constraint():
    from applire.services.cover_letter import _STATED_LIMITS_CONSTRAINT

    assert "written in IS a finding (DEL-1)" in _flat(reviewer.REVIEW_SYSTEM_PROMPT)
    assert _DEL1 in _STATED_LIMITS_CONSTRAINT


# ── DEL-2: de-emphasis leaves the requirement out ────────────────────────────

def test_the_unaddressed_block_no_longer_offers_a_named_gap_de_emphasis():
    block = xd.render_unaddressed_hard_requirements_block([
        {"concept": "Arbeitsvorbereitung", "evidence": "", "adjacent_evidence": "Feinplanung"},
    ])
    flat = _flat(block)
    assert "honest de-emphasis that names the gap" not in flat
    assert "Silence is not one of the options" not in flat
    assert "or leave the requirement out" in flat
    assert "A requirement listed here that the letter leaves out is NOT a finding" in flat
    # G-1: a named gap lives in the KEY-REQUIREMENT GAPS content, never in this block
    assert "Gaps the letter names are listed ONLY in the KEY-REQUIREMENT GAPS content" in flat
    assert "ASKED AND STATED" not in flat and "ASKED AND SAID NO" not in flat
    assert "never state it as something they LACK either" in flat


@pytest.mark.parametrize("text", [
    writer.SYSTEM_PROMPT, writer.build_system_prompt("de", pipeline="v2"),
], ids=["v1", "v2"])
def test_the_writer_names_a_gap_only_as_the_ladder_block_lists_it(text):
    flat = _flat(text)
    # G-1 (supersedes RULING L-1 = A's "leaves the limit out"): a gap is stated only where
    # the KEY-REQUIREMENT GAPS block names it, gap + transfer (or the einarbeiten clause)
    # in ONE sentence; every other unmet requirement is left out, never stated as a lack.
    assert "OBLIGATION — THE GAP LADDER (G-1)" in flat
    assert "a KEY-REQUIREMENT GAPS block names the gap(s) this letter states, chosen by code" in flat
    assert "EVERY OTHER UNMET JD HARD REQUIREMENT" in flat
    assert "(3) else the letter LEAVES IT OUT — never stated as a lack" in flat
    assert "never what they cannot claim, name or prove (DEL-2)" in flat
    assert "Silence is still never an option" not in flat
    assert "REQUIRED: STATED LIMITS" not in flat


def test_the_reviewer_stops_demanding_unnamed_gap_sentences():
    for prompt in (reviewer.REVIEW_SYSTEM_PROMPT, reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT):
        flat = _flat(prompt)
        assert "A concept may be named as a gap only when ``gap_positioning`` names it (RULING G-1, DEL-2)" in flat
        assert "NEVER DEMAND A CONCEPT ``named`` DOES NOT HOLD" in flat
        assert "``stated_limits`` is a constraint only" in flat
        assert "the ledger marks it so or a STATED LIMIT disclaims it" not in flat
    # the voice check (terminal door only) protects the gap_positioning sentence
    assert "never touch a ``gap_positioning`` gap's sentence" in _flat(
        reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT)


def test_the_corrector_never_writes_a_lack_the_gap_ladder_does_not_name():
    flat = _flat(reviewer.COVER_LETTER_REFINEMENT_PROMPT)
    assert "NEVER WRITE A LACK THE GAP LADDER DOES NOT NAME" in flat
    assert "ONLY for a gap ``positioning_requested.gap_positioning`` names" in flat
    assert "surface the adjacent strength on its own merits or leave the requirement out" in flat
