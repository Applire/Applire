# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 6, WP-L — RULING G-1 (ADR-075 amended 2026-11-04): the gap ladder's FACTS.

Which requirements are gaps, how they are counted (RULING L-1 = B: one recorded statement
= one gap; one unrecorded key-tier not-claimable term = one gap), which one is most urgent
(weight → posting mention count → first position → ledger order) and how many are named
(≤ 2 → all, ≥ 3 → the first) are computed in code (ADR-062 clause 1). Whether a transfer
exists and the sentence are the writer's. Synthetic data throughout.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest  # noqa: E402

from applire.services import cross_document as xd  # noqa: E402
from applire.services.cross_document import (  # noqa: E402
    KeyGap,
    gaps_to_name,
    key_requirement_gaps,
    letter_constraint_only_limits,
    named_gap_concepts,
    posting_mentions,
    render_gap_ladder_block,
)
from applire.services.ats_audit import _norm  # noqa: E402


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


def _row(concept, status="gap", weight=1.0, claimable=False, **over):
    row = {
        "concept": concept, "surface_forms": [], "status": status,
        "claimable": claimable, "fit_weight": weight, "sources": ["required"],
    }
    row.update(over)
    return row


STATEMENT = "Mit Boomi und Atmosphere habe ich nicht gearbeitet; Schnittstellen habe ich mit Camel gebaut."
DENIED = [
    {"concept": "Boomi", "statement": STATEMENT},
    {"concept": "Atmosphere", "statement": STATEMENT},
    {"concept": "Segelfliegen", "statement": "Segelfliegen kann ich nicht."},
]
JD = (
    "Director Integration Platform. You own the Boomi landscape, Boomi Atmosphere runtimes, "
    "Kafka event streaming and Terraform. Kafka experience is a must; Kafka Connect a plus. "
    "Nice to have: OpenShift."
)


def _ledger():
    return [
        _row("Boomi", status="denied"),
        _row("Atmosphere", status="denied"),
        _row("Kafka"),
        _row("Terraform"),
        _row("OpenShift", weight=0.5),                       # nice-to-have → never a gap
        _row("Camel", status="direct", claimable=True),      # claimable → never a gap
        _row("ESB", status="partial", claimable=True, adjacent_evidence="Camel"),  # adjacent
    ]


# ── what a gap is ────────────────────────────────────────────────────────────


def test_a_gap_is_a_key_tier_not_claimable_row_recorded_or_not():
    gaps = key_requirement_gaps(_ledger(), DENIED, JD)
    labels = {g.label for g in gaps}
    assert labels == {"Boomi", "Kafka", "Terraform"}, gaps


@pytest.mark.parametrize("row, why", [
    (_row("X", weight=0.5), "nice-to-have (L-1 = A stays)"),
    (_row("X", weight=0.0), "keyword-only"),
    (_row("X", status="partial", claimable=True, adjacent_evidence="Y"), "adjacent partial (DEL-2)"),
    (_row("X", status="direct", claimable=True), "claimable"),
    (_row("X", status="gap", claimable=True), "claimable wins over a gap status (defensive)"),
    (_row("X", bar={"kind": "scope"}), "scope row (ADR-069)"),
    (_row("X", aim_only=True), "aim-only (D-12)"),
    (_row("X", status="partial"), "a partial is not a gap/denied status"),
    (_row("", status="gap"), "no concept"),
    ("junk", "malformed"),
])
def test_each_exclusion(row, why, monkeypatch):
    from applire.services import keyword_ledger as kl

    if isinstance(row, dict) and row.get("bar"):
        monkeypatch.setattr(kl, "is_scope_entry", lambda e: bool(e.get("bar")))
    assert key_requirement_gaps([row], [], "X X X") == [], why


def test_the_key_tier_bound_is_inclusive():
    from applire.services.keyword_ledger import REQUIRED_WEIGHT

    assert key_requirement_gaps([_row("X", weight=REQUIRED_WEIGHT)], [], "X")[0].label == "X"
    assert key_requirement_gaps([_row("X", weight=REQUIRED_WEIGHT - 0.01)], [], "X") == []


# ── the count (L-1 = B, MD6-4) ───────────────────────────────────────────────


def test_one_recorded_statement_naming_several_concepts_is_one_gap():
    gaps = key_requirement_gaps(_ledger(), DENIED, JD)
    boomi = next(g for g in gaps if g.label == "Boomi")
    assert boomi.concepts == ("Boomi", "Atmosphere")
    assert boomi.statement == STATEMENT and boomi.recorded
    assert len(gaps) == 3


def test_a_denied_by_binding_joins_the_statement():
    ledger = [_row("Boomi", status="denied"), _row("Boomi Flow", status="denied", denied_by="Boomi")]
    gaps = key_requirement_gaps(ledger, DENIED[:1], "Boomi Flow and Boomi")
    assert len(gaps) == 1 and gaps[0].concepts == ("Boomi", "Boomi Flow")


def test_two_different_statements_are_two_gaps():
    denied = [{"concept": "A1", "statement": "A1 nie."}, {"concept": "B1", "statement": "B1 nie."}]
    ledger = [_row("A1", status="denied"), _row("B1", status="denied")]
    assert len(key_requirement_gaps(ledger, denied, "A1 B1")) == 2


def test_an_unrecorded_row_carries_no_statement():
    kafka = next(g for g in key_requirement_gaps(_ledger(), DENIED, JD) if g.label == "Kafka")
    assert kafka.statement is None and not kafka.recorded


# ── the order (L-1 = B) ──────────────────────────────────────────────────────


def test_ranked_by_posting_mentions_then_first_position():
    gaps = key_requirement_gaps(_ledger(), DENIED, JD)
    # Kafka ×3 (whole tokens; "Kafka Connect" counts its own start), Boomi ×2, Terraform ×1
    assert [g.label for g in gaps] == ["Kafka", "Boomi", "Terraform"], gaps
    assert gaps[0].mentions == 3


def test_weight_outranks_mentions():
    ledger = [_row("Alpha", weight=1.0), _row("Beta", weight=1.5)]
    assert [g.label for g in key_requirement_gaps(ledger, [], "Alpha Alpha Alpha Beta")] == ["Beta", "Alpha"]


def test_first_position_breaks_a_mention_tie_then_ledger_order():
    ledger = [_row("Alpha"), _row("Beta"), _row("Gamma"), _row("Delta")]
    gaps = key_requirement_gaps(ledger, [], "Beta then Alpha")
    assert [g.label for g in gaps] == ["Beta", "Alpha", "Gamma", "Delta"]


def test_a_groups_label_is_its_most_mentioned_concept():
    jd = "Atmosphere, Atmosphere and Boomi."
    gap = key_requirement_gaps(_ledger()[:2], DENIED, jd)[0]
    assert gap.label == "Atmosphere"
    assert gap.mentions == 2 and gap.first_position == 0


def test_mentions_are_whole_token_and_fold_singular_plural():
    assert posting_mentions(["AWS"], _norm("laws and AWS"))[0] == 1
    assert posting_mentions(["Gateway"], _norm("Gateways and a Gateway"))[0] == 2
    assert posting_mentions(["Kafka"], _norm("nothing here")) == (0, xd._NO_POSITION)
    assert posting_mentions(["", "Kafka"], _norm("Kafka"))[0] == 1


def test_a_surface_form_counts_but_one_position_is_counted_once():
    row = _row("Kafka", surface_forms=["Kafka", "Apache Kafka"])
    gap = key_requirement_gaps([row], [], "Apache Kafka and Kafka")[0]
    assert gap.mentions == 3  # "apache kafka"@0, "kafka"@7, "kafka"@17 — distinct starts


def test_none_tolerant():
    assert key_requirement_gaps(None, None, None) == []


# ── how many are named (G-1) ─────────────────────────────────────────────────


def _gap(label):
    return KeyGap(label=label, concepts=(label,), statement=None, weight=1.0, mentions=1, first_position=0)


@pytest.mark.parametrize("n, named", [(0, []), (1, ["g0"]), (2, ["g0", "g1"]), (3, ["g0"]), (5, ["g0"])])
def test_two_or_fewer_all_three_or_more_only_the_most_urgent(n, named):
    assert [g.label for g in gaps_to_name([_gap(f"g{i}") for i in range(n)])] == named


def test_named_gap_concepts_is_every_concept_of_a_named_gap():
    gaps = key_requirement_gaps(_ledger(), DENIED, "Boomi")
    named = gaps_to_name(gaps)
    assert named[0].label == "Boomi"
    assert named_gap_concepts(named) == ["Boomi", "Atmosphere"]


# ── the writer block ─────────────────────────────────────────────────────────


def test_no_named_gap_renders_nothing():
    assert render_gap_ladder_block([], [], "de") == ""


def test_three_gaps_render_only_the_most_urgent_with_the_count():
    gaps = key_requirement_gaps(_ledger(), DENIED, JD)
    block = render_gap_ladder_block(gaps, gaps_to_name(gaps), "en")
    flat = _flat(block)
    assert block.startswith("=== KEY-REQUIREMENT GAPS")
    assert "this posting has 3 key requirement(s)" in flat
    assert "only the most urgent one is named" in flat
    name_part = block.split("NAME IN THE LETTER:")[1]
    assert "Kafka" in name_part and "Boomi" not in name_part and "Terraform" not in name_part
    assert "not in the profile" in name_part
    assert "i will get up to speed on kafka quickly" in flat  # an example, EN


def test_two_gaps_render_both_and_a_recorded_one_with_its_statement():
    ledger = _ledger()[:3]
    gaps = key_requirement_gaps(ledger, DENIED, JD)
    block = render_gap_ladder_block(gaps, gaps_to_name(gaps), "de")
    name_part = block.split("NAME IN THE LETTER:")[1]
    assert "Kafka" in name_part and "Boomi" in name_part
    assert STATEMENT in name_part
    assert "each is named below" in _flat(block)
    assert "werde ich mich schnell einarbeiten" in _flat(block)  # DE example


def test_the_block_states_the_rule_and_its_bans():
    gaps = key_requirement_gaps(_ledger(), DENIED, JD)
    flat = _flat(render_gap_ladder_block(gaps, gaps_to_name(gaps), "de"))
    for phrase in (
        "in the same sentence", "never a list of lacks", "\"not recorded\"",
        "\"beyond my profile\"", "every further concept a listed statement names",
        "silence there is correct, never a finding", "the einarbeiten clause",
        "no figure, employer, project, certificate or trait adjective",
        "\"fast learner\"", "free wording",
    ):
        assert phrase in flat, phrase


# ── the constraint-only filter (DEL-3 narrowed by G-1) ───────────────────────


def test_a_named_statement_leaves_the_constraint_block_every_other_stays():
    gaps = key_requirement_gaps(_ledger()[:2], DENIED, "Boomi")
    rest = letter_constraint_only_limits(DENIED, gaps_to_name(gaps))
    assert STATEMENT not in rest
    assert "Segelfliegen kann ich nicht." in rest


def test_an_unnamed_recorded_gap_stays_constraint_only():
    gaps = key_requirement_gaps(_ledger(), DENIED, JD)  # Kafka is named, Boomi is not
    rest = letter_constraint_only_limits(DENIED, gaps_to_name(gaps))
    assert STATEMENT in rest


def test_constraint_filter_none_tolerant():
    assert letter_constraint_only_limits(None, None) == []
    assert letter_constraint_only_limits(DENIED, None) == xd.collect_stated_limits(DENIED)


# ── UNADDRESSED HARD REQUIREMENTS: adjacent partials only (MD6-4) ────────────


def test_unaddressed_lists_adjacent_partials_and_never_a_gap_row():
    rows = xd.find_unaddressed_hard_requirements(_ledger(), None)
    assert [r["concept"] for r in rows] == ["ESB"]


def test_unaddressed_block_never_frames_a_denial_per_concept():
    block = xd.render_unaddressed_hard_requirements_block(
        xd.find_unaddressed_hard_requirements(_ledger(), None)
    )
    assert "ASKED AND STATED" not in block
    assert "KEY-REQUIREMENT GAPS" in block


def test_d29_one_home_per_fact_survives_in_the_gap_ladder_rule_on_both_doors():
    """D-29 lived in the deleted REQUIRED block; the gap ladder rule is its one home now —
    rendered into the writer block AND the loop's gap_positioning instruction."""
    from applire.services.cover_letter import build_gap_positioning_entry

    gaps = key_requirement_gaps(_ledger(), DENIED, JD)
    named = gaps_to_name(gaps)
    for text in (render_gap_ladder_block(gaps, named, "en"),
                 build_gap_positioning_entry(gaps, named, "en")["instruction"]):
        flat = _flat(text)
        assert "one home per fact" in flat
        assert "never introduces it again" in flat
