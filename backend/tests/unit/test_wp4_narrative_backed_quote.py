# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-100 amended 2026-11-04 (WP4, Interview #787) — the #260 narrative stamp
reads the row's verified quote.

`_annotate_narrative_backed` asked "does a responsibility / achievement / story
contain the requirement's TERM?" by a literal match. A requirement the vault
narrates in another language than the posting therefore read as a keyword
LIABILITY, and ADR-089 (ruling A-1) never counts a liability as `covered`: the
11-04 E2E tier kept six interviewed requirements open on their clusters after
the recompute had graded them `direct` with a German quote. The quote arm: a row
is narrative-backed when its `backing.quote` passes `quote_in_vault` in a
NARRATIVE unit (a Fact; ADR-100 cl. 1/3).

Synthetic data only (an invented German lab-automation vault).
"""
from __future__ import annotations

from applire.services import gap_coverage
from applire.services.keyword_ledger import (
    _annotate_narrative_backed,
    build_keyword_ledger,
    keyword_liabilities,
    reevaluate_gap_ledger_against_vault,
)

_BULLET = "Aufbau einer Messdatenstrecke von den Pipettierrobotern in die Auswertungsdatenbank."
_TEAM = "Führung eines Teams von vier Technikerinnen in zwei Schichten."


def _vault(**over):
    vault = {
        "personal_info": {"name": "Mara Testfall"},
        "work_experience": [
            {
                "id": "w-1",
                "company": "Probelabor Nord GmbH",
                "role": "Laborautomatisierung",
                "start_date": "2020-01",
                "is_current": True,
                "responsibilities": [_BULLET, _TEAM],
                "achievements": [],
                "technologies": ["Python"],
            }
        ],
        "skills": [{"name": "Datenpipelines", "status": "confirmed"}],
        "metadata": {"denied_concepts": []},
    }
    vault.update(over)
    return vault


def _row(concept, quote, *, status="direct", sources=("required",)):
    row = {
        "concept": concept,
        "surface_forms": [concept],
        "sources": list(sources),
        "status": status,
        "claimable": status in ("direct", "partial"),
        "evidence": "summary",
    }
    if quote is not None:
        row["backing"] = {"quote": quote, "instrument": "quote"}
    return row


def test_a_verified_quote_in_a_responsibility_makes_a_translated_requirement_narrative_backed():
    [row] = _annotate_narrative_backed([_row("Data pipelines", _BULLET)], _vault())
    assert row["narrative_backed"] is True
    assert keyword_liabilities([row]) == []


def test_the_same_row_without_backing_stays_a_liability_literal_check_unchanged():
    [row] = _annotate_narrative_backed([_row("Data pipelines", None)], _vault())
    assert row["narrative_backed"] is False
    assert [e["concept"] for e in keyword_liabilities([row])] == ["Data pipelines"]


def test_a_quote_that_resolves_only_in_the_skills_list_is_not_narrative():
    # "Datenpipelines" is a whole typed-value unit (skills[0]) — a Fact for
    # backing, but a bare list entry is exactly what #260 says is not a story.
    [row] = _annotate_narrative_backed([_row("Data pipelines", "Datenpipelines")], _vault())
    assert row["narrative_backed"] is False


def test_a_paraphrased_quote_does_not_count():
    [row] = _annotate_narrative_backed(
        [_row("Data pipelines", "Aufbau einer Datenstrecke von Robotern in die Datenbank.")], _vault()
    )
    assert row["narrative_backed"] is False


def test_a_quote_naming_a_denied_concept_backs_no_narrative():
    vault = _vault(metadata={"denied_concepts": [{
        "concept": "Messdatenstrecke", "statement": "Eine Messdatenstrecke habe ich nie selbst aufgebaut.",
        "source": "interview", "date": "2026-11-04", "denial_level": "direct",
    }]})
    # the fixture must index, or the arm is None and the assertion passes vacuously
    from applire.services.keyword_ledger import _narrative_quote_arm

    assert _narrative_quote_arm(vault) is not None
    [row] = _annotate_narrative_backed([_row("Data pipelines", _BULLET)], vault)
    assert row["narrative_backed"] is False


def test_a_literal_narrative_hit_still_backs_without_a_quote():
    [row] = _annotate_narrative_backed([_row("Python", None)], _vault(
        work_experience=[{**_vault()["work_experience"][0], "responsibilities": ["Skripte in Python für die Auswertung."]}]
    ))
    assert row["narrative_backed"] is True


def test_no_profile_keeps_the_pre_260_default():
    [row] = _annotate_narrative_backed([_row("Data pipelines", _BULLET)], None)
    assert row["narrative_backed"] is True


# --- seam 1: build_keyword_ledger's final pass ------------------------------


def test_seam_build_keyword_ledger_stamps_the_quote_arm():
    ledger = build_keyword_ledger(
        [{"concept": "Team leadership", "status": "direct", "evidence": "leads a team",
          "surface_forms": ["Team leadership"], "evidence_quote": _TEAM}],
        ["Team leadership"], [], [],
        profile_json=_vault(),
    )
    row = next(e for e in ledger if e["concept"] == "Team leadership")
    assert row["claimable"] is True
    assert row["narrative_backed"] is True
    assert keyword_liabilities(ledger) == []


# --- seam 2: reevaluate_gap_ledger_against_vault's restamp ------------------


def test_seam_reevaluate_restamps_a_carried_row_with_its_quote():
    stale = {**_row("Team leadership", _TEAM), "narrative_backed": False}
    ledger, _changed = reevaluate_gap_ledger_against_vault([stale], _vault())
    row = next(e for e in ledger if e["concept"] == "Team leadership")
    assert row["narrative_backed"] is True


# --- seam 3: the gap-analysis merge (services/gap.py::_apply_floors_to_merged)


def test_seam_gap_merge_floors_restamp_a_carried_row_with_its_quote():
    from applire.services.gap import _apply_floors_to_merged

    stale = {**_row("Team leadership", _TEAM), "narrative_backed": False}
    out = _apply_floors_to_merged([stale], [0], denied_concepts=[], profile_json=_vault())
    assert out[0]["narrative_backed"] is True


# --- the reader that motivated it: ADR-089 cluster coverage -----------------


def test_reader_cluster_member_backed_by_a_translated_narrative_reads_covered():
    [row] = _annotate_narrative_backed([_row("Team leadership", _TEAM)], _vault())
    facts = gap_coverage.classify_members(["Team leadership"], [row], [])
    assert facts == {"Team leadership": "covered"}
