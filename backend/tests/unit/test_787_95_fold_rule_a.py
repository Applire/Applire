# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""787:95 rule (a): a recompute's leftover concept whose OWN ledger row names a
carried member in other words is that topic again under a renamed JD term (the
Nougat K-1 case: "Kunststoffverpackungen" came back as a new card next to a
carried "Verpackungsindustrie"). It folds into the carried card; a FINISHED card
records it as folded and stays finished (founder default until ruled)."""

from applire.services.gap_coverage import fold_into, fold_target, folded_members, refresh_cluster_from_ledger


def _row(concept, forms, status="gap"):
    return {"concept": concept, "surface_forms": forms, "status": status, "fit_weight": 1.0, "claimable": False, "sources": ["required"]}


LEDGER = [
    _row("Kunststoffverpackungen", ["Kunststoffverpackungen", "Verpackungsindustrie"]),
    _row("Verpackungsindustrie", ["Verpackungsindustrie"]),
    _row("IFS", ["IFS"]),
    _row("Exportkontrolle", ["Exportkontrolle"]),
]
OPEN = {"id": "cluster-verpackung", "label": "Verpackung", "gaps": ["Verpackungsindustrie"], "outcome": {"asked": 0, "covered": [], "declined": [], "session_ids": []}, "coverage": "open"}
OTHER = {"id": "cluster-food", "label": "Lebensmittel", "gaps": ["IFS"], "outcome": {"asked": 0, "covered": [], "declined": [], "session_ids": []}, "coverage": "open"}


def test_a_renamed_term_finds_its_carried_topic():
    assert fold_target("Kunststoffverpackungen", [OTHER, OPEN], LEDGER) is OPEN


def test_a_new_subject_finds_none():
    assert fold_target("Exportkontrolle", [OTHER, OPEN], LEDGER) is None
    assert fold_target("Unknown", [OPEN], LEDGER) is None  # no row, no fact


def test_an_open_card_takes_it_as_a_member():
    new = fold_into(OPEN, "Kunststoffverpackungen", LEDGER, [])
    assert new["gaps"] == ["Verpackungsindustrie", "Kunststoffverpackungen"]
    assert folded_members(new) == []


def test_a_finished_card_records_it_and_stays_finished():
    done = {**OPEN, "outcome": {**OPEN["outcome"], "left_open": True}}
    new = fold_into(done, "Kunststoffverpackungen", LEDGER, [])
    assert new["gaps"] == ["Verpackungsindustrie"] and new["folded"] == ["Kunststoffverpackungen"]
    assert fold_into(new, "Kunststoffverpackungen", LEDGER, [])["folded"] == ["Kunststoffverpackungen"]


def test_a_folded_member_follows_the_orphan_rule_on_refresh():
    done = {**OPEN, "outcome": {**OPEN["outcome"], "left_open": True}, "folded": ["Kunststoffverpackungen", "Gone"]}
    assert refresh_cluster_from_ledger(done, LEDGER, [])["folded"] == ["Kunststoffverpackungen"]
