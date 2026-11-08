# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 6, WP-L fix round 3: RULING D-6 = A (founder, re-probe 2026-11-05).

G-1 keeps exactly one named gap at three or more. Inside the posting's
requirements section a mention in the responsibilities never lifts a gap: mentions
and first position are counted WITHIN the section; gaps outside the section keep
L-1's order (whole-posting mentions, then first position) after them. No
recognisable requirements heading → L-1 unchanged.

Evidence shape (synthetic): the re-probe's fresh heal1 ledger named "SLAs" — one
responsibilities mention plus one must-have mention — over the must-have
platforms that the section names once each.
Synthetic data throughout.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cross_document import (  # noqa: E402
    gaps_to_name,
    key_requirement_gaps,
)


def _row(concept, status="gap", weight=1.0, **over):
    row = {
        "concept": concept, "surface_forms": [], "status": status,
        "claimable": False, "fit_weight": weight, "sources": ["required"],
    }
    row.update(over)
    return row


JD = """Head of Integration (m/f/d)
Halvard Pharma AG · Basel

Your responsibilities
- Run the platform as a product: availability, SLAs and capacity planning
- Report SLAs and capacity planning to the board every quarter

Your profile (must have)
- Hands-on experience with Boomi and its Flow runtime
- Deep experience with Google Cloud integration services
- Experience running 24/7 platforms under SLAs

Nice to have
- Event streaming with Pulsar
"""


def _ledger():
    return [
        _row("SLAs"),
        _row("Capacity planning"),
        _row("Google Cloud", status="denied"),
        _row("Boomi", status="denied"),
    ]


def test_a_responsibilities_mention_never_lifts_a_must_have_gap():
    gaps = key_requirement_gaps(_ledger(), [], JD)
    # As built (D-2 only): SLAs led on 3 whole-posting mentions. D-6: once in the
    # section, like Boomi and Google Cloud — first position there decides.
    assert [g.label for g in gaps_to_name(gaps)] == ["Boomi"]
    assert [g.label for g in gaps] == ["Boomi", "Google Cloud", "SLAs", "Capacity planning"]
    assert [g.mentions for g in gaps[:3]] == [1, 1, 1]


def test_mentions_inside_the_section_still_rank():
    jd = JD.replace("Experience running 24/7 platforms under SLAs",
                    "Experience running 24/7 platforms under SLAs; SLAs reporting to QA")
    gaps = key_requirement_gaps(_ledger(), [], jd)
    assert gaps[0].label == "SLAs" and gaps[0].mentions == 2


def test_gaps_outside_the_section_keep_l1_after_it():
    ledger = [_row("Pulsar"), _row("Capacity planning"), _row("Boomi")]
    gaps = key_requirement_gaps(ledger, [], JD)
    # Boomi (section) first; then L-1 over the whole posting: Capacity planning is
    # mentioned twice, Pulsar once — mentions decide before position.
    assert [g.label for g in gaps] == ["Boomi", "Capacity planning", "Pulsar"]
    assert [g.in_requirements for g in gaps] == [True, False, False]
    assert gaps[1].mentions == 2


def test_no_requirements_heading_leaves_l1_unchanged():
    jd = JD.replace("Your profile (must have)", "More about the work")
    gaps = key_requirement_gaps(_ledger(), [], jd)
    assert not any(g.in_requirements for g in gaps)
    assert [g.label for g in gaps][:2] == ["SLAs", "Capacity planning"]
    assert gaps[0].mentions == 3


def test_a_recorded_statement_counts_only_its_section_members():
    statement = "Mit SLAs, Kapazitätsplanung und Boomi habe ich nicht gearbeitet."
    denied = [{"concept": c, "statement": statement} for c in ("Capacity planning", "Boomi")]
    ledger = [_row("Capacity planning", status="denied"), _row("Boomi", status="denied"),
              _row("Google Cloud"), _row("SLAs")]
    gaps = key_requirement_gaps(ledger, denied, JD)
    recorded = next(g for g in gaps if g.recorded)
    # Capacity planning's two responsibilities mentions do not count for the group.
    assert recorded.label == "Boomi" and recorded.mentions == 1
    assert recorded.first_position < next(g for g in gaps if g.label == "Google Cloud").first_position
    assert gaps_to_name(gaps)[0].label == "Boomi"
