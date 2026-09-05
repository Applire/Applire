# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#525 — the ledger's coverage demand names the vault entry that OWNS the term.

The coverage block hands the reviewer a term and a bare evidence sentence with
no owner, and the reviewer then demands the term be surfaced. A corrector
obeying that demand has nothing in the demand itself that says WHICH employer
the term belongs to — which is how a coverage-demanded ``SAP MM`` got welded
onto Rasselstein when it belongs to Weberit (2026-08-11, cover letter round 80).

``services/letter_figure_guard.py`` already solves the same question for
FIGURES: ``figure_ownership_facts`` reports, per figure, every position that
owns a vault unit carrying it, and leaves the judgement ("which employer is this
sentence about") to the reviewer. #525's finding is that the ledger never
threaded the same fact for TERMS. This is that fact, at the three sites where a
ledger entry is constructed or re-evidenced, rendered on the one block that
makes the demand.

ADR-062 classification: FACT — which position a vault evidence unit belongs to
is settled by the profile's own structure (``EvidenceUnit.owner_ids``), not by
reading prose for meaning.
"""
from __future__ import annotations

from applire.services.keyword_ledger import (
    annotate_evidence_owners,
    build_keyword_ledger,
    reevaluate_gap_ledger_against_vault,
    render_verified_coverage_block,
    upgrade_ledger_for_concepts,
)

_VAULT = {
    "personal_info": {"name": "Marcus Weber"},
    "professional_summary": {"de": "Erfahrener Operations-Leiter.", "en": None},
    "work_experience": [
        {
            "id": "w-weberit",
            "company": "Weberit Kunststofftechnik GmbH",
            "position": "Produktionsleiter",
            "responsibilities": [
                "Tägliche Arbeit mit SAP PP und SAP MM in der Fertigungssteuerung."
            ],
            "achievements": [],
        },
        {
            "id": "w-rasselstein",
            "company": "Rasselstein GmbH",
            "position": "Schichtleiter",
            "responsibilities": ["Schichtführung in der Bandbeschichtung."],
            "achievements": [],
        },
    ],
    "skills": [{"name": "Lean Management", "category": "technical"}],
    "metadata": {"denied_concepts": []},
}


def _entry(concept: str, forms: list[str] | None = None, **over) -> dict:
    return {
        "concept": concept,
        "surface_forms": forms or [concept],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "direct",
        "evidence": "Tägliche Arbeit mit SAP PP und SAP MM.",
        "claimable": True,
        **over,
    }


# ── the fact itself ────────────────────────────────────────────────────────


def test_owner_is_the_position_whose_evidence_carries_the_term():
    out = annotate_evidence_owners([_entry("SAP MM")], _VAULT)
    assert out[0]["evidence_owners"] == ["Weberit Kunststofftechnik GmbH"]


def test_a_term_no_position_owns_carries_no_owner_field():
    """Role-agnostic evidence (summary, skills, education) belongs to no
    position, so there is no attribution question — the field stays ABSENT
    rather than empty, exactly as ``figure_ownership_facts`` omits such a
    figure instead of reporting "nobody owns it"."""
    out = annotate_evidence_owners([_entry("Lean Management")], _VAULT)
    assert "evidence_owners" not in out[0]


def test_a_term_absent_from_the_vault_carries_no_owner_field():
    out = annotate_evidence_owners([_entry("Kubernetes")], _VAULT)
    assert "evidence_owners" not in out[0]


def test_a_term_two_positions_carry_names_both_in_profile_order():
    vault = {
        **_VAULT,
        "work_experience": [
            {**_VAULT["work_experience"][0]},
            {
                **_VAULT["work_experience"][1],
                "responsibilities": ["Auch hier SAP MM im Tagesgeschäft."],
            },
        ],
    }
    out = annotate_evidence_owners([_entry("SAP MM")], vault)
    assert out[0]["evidence_owners"] == [
        "Weberit Kunststofftechnik GmbH",
        "Rasselstein GmbH",
    ]


def test_a_surface_form_resolves_the_owner_too():
    out = annotate_evidence_owners(
        [_entry("Materialwirtschaft", forms=["Materialwirtschaft", "SAP MM"])], _VAULT
    )
    assert out[0]["evidence_owners"] == ["Weberit Kunststofftechnik GmbH"]


def test_annotate_is_pure_and_tolerant():
    rows = [_entry("SAP MM")]
    snapshot = [dict(r) for r in rows]
    annotate_evidence_owners(rows, _VAULT)
    assert rows == snapshot
    assert annotate_evidence_owners([], _VAULT) == []
    assert annotate_evidence_owners(rows, None) == rows


# ── seam 1: build_keyword_ledger ───────────────────────────────────────────


def test_seam_build_keyword_ledger_stamps_the_owner():
    ledger = build_keyword_ledger(
        [
            {
                "concept": "SAP MM",
                "status": "direct",
                "evidence": "Tägliche Arbeit mit SAP PP und SAP MM.",
                "surface_forms": ["SAP MM"],
            }
        ],
        ["SAP MM"],
        [],
        [],
        profile_json=_VAULT,
    )
    row = next(e for e in ledger if e["concept"] == "SAP MM")
    assert row["evidence_owners"] == ["Weberit Kunststofftechnik GmbH"]


# ── seam 2: reevaluate_gap_ledger_against_vault ────────────────────────────


def test_seam_reevaluate_against_vault_stamps_the_owner():
    stale = [_entry("SAP MM", status="gap", evidence="", claimable=False)]
    out, changed = reevaluate_gap_ledger_against_vault(stale, _VAULT)
    assert changed is True
    assert out[0]["evidence_owners"] == ["Weberit Kunststofftechnik GmbH"]


# ── seam 3: upgrade_ledger_for_concepts ────────────────────────────────────


def test_seam_upgrade_for_concepts_stamps_the_owner_when_given_the_vault():
    stale = [_entry("SAP MM", status="gap", evidence="", claimable=False)]
    out, changed = upgrade_ledger_for_concepts(
        stale,
        ["SAP MM"],
        "Ich arbeite bei Weberit täglich mit SAP MM.",
        profile_json=_VAULT,
    )
    assert changed is True
    assert out[0]["evidence_owners"] == ["Weberit Kunststofftechnik GmbH"]


def test_seam_upgrade_for_concepts_without_a_vault_is_unchanged():
    """Back-compat: every existing caller passes no profile and gets exactly
    today's row shape — no owner field invented from nothing."""
    stale = [_entry("SAP MM", status="gap", evidence="", claimable=False)]
    out, _changed = upgrade_ledger_for_concepts(stale, ["SAP MM"], "an answer")
    assert "evidence_owners" not in out[0]


# ── the render ─────────────────────────────────────────────────────────────


def test_coverage_block_prints_the_owner():
    block = render_verified_coverage_block(
        annotate_evidence_owners([_entry("SAP MM")], _VAULT)
    )
    assert "Weberit Kunststofftechnik GmbH" in block
    line = next(ln for ln in block.splitlines() if ln.strip().startswith("- SAP MM"))
    assert "Weberit Kunststofftechnik GmbH" in line


def test_coverage_block_owner_line_is_omitted_when_no_position_owns_the_term():
    block = render_verified_coverage_block([_entry("Lean Management")])
    line = next(ln for ln in block.splitlines() if ln.strip().startswith("- Lean Management"))
    assert "owned by" not in line


def test_coverage_block_states_the_narrow_rule_only_when_an_owner_is_present():
    with_owner = render_verified_coverage_block(
        annotate_evidence_owners([_entry("SAP MM")], _VAULT)
    )
    without = render_verified_coverage_block([_entry("Lean Management")])
    assert "belongs under THAT position" in with_owner
    assert "belongs under THAT position" not in without


def test_the_owner_fact_survives_the_api_schema():
    """An undeclared ledger key is silently stripped by
    ``GapAnalysisResponse.model_validate`` — the exact way ``adjacent_evidence``
    never reached a single API response for months. Pinned so this fact cannot
    be computed into a void (prompt-first step 2b)."""
    from applire.schemas.gap import KeywordLedgerEntry

    row = annotate_evidence_owners([_entry("SAP MM")], _VAULT)[0]
    validated = KeywordLedgerEntry.model_validate(row)
    assert validated.evidence_owners == ["Weberit Kunststofftechnik GmbH"]
    assert KeywordLedgerEntry.model_validate(_entry("Lean Management")).evidence_owners == []


def test_a_legacy_row_gains_its_owner_through_the_generation_refresh():
    """Delivery-point property, pinned. `evidence_owners` is stamped at the
    ledger's CONSTRUCTION sites, so a ledger persisted before #525 carries none.
    The generation seams run `refresh_ledger_against_vault`, which goes through
    `reevaluate_gap_ledger_against_vault` — and that annotates unconditionally,
    not only when it upgraded something. So the writer's coverage demand names
    the owner even for an analysis that predates the field, and even when
    nothing about the ledger's statuses changed."""
    from applire.services.keyword_ledger import refresh_ledger_against_vault

    legacy = [_entry("SAP MM")]  # claimable already, nothing to upgrade
    assert "evidence_owners" not in legacy[0]

    refreshed, changed = refresh_ledger_against_vault(legacy, _VAULT, seam="test")

    assert changed is False, "no status moved — the annotation must not fake a change"
    assert refreshed[0]["evidence_owners"] == ["Weberit Kunststofftechnik GmbH"]
    assert "Weberit Kunststofftechnik GmbH" in render_verified_coverage_block(refreshed)
