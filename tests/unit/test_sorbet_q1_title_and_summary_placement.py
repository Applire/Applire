# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q1 cluster (c): no title that is not the person's word, and no
profile-summary text as a role bullet.

Platform-1 rerun, Stefan (luna), attributed per round:

* the gap analysis graded the posting's "Bereichsleiter" ``partial`` against
  "Verantwortung für zwei Fertigungsbereiche" — the ledger made it CLAIMABLE,
  the terminal reviewer's VERIFIED COVERAGE demanded it (rec 158, 172) and the
  corrector wrote "Verantwortung als Bereichsleiter" (rec 173). The ledger now
  floors a claimable row whose concept IS a title the vault never holds
  (``enforce_title_stance``, both seams: build and the document-time refresh).
* the under-claim finding handed the corrector the gap analysis's summary
  "14 Jahre Erfahrung als Produktions- und Betriebsleiter …"; the corrector
  pasted it as the Weberit role's first bullet (rec 169; replay 3/5). The finding
  now says a career summary belongs to no single entry (replay 0/5).
"""
from __future__ import annotations

from applire.services.cv_gap_hints import UnderclaimedConcept, _issue_text
from applire.services.keyword_ledger import (
    TITLE_UNHELD_EVIDENCE,
    build_keyword_ledger,
    enforce_title_stance,
    refresh_ledger_against_vault,
)
from applire.services.profile.reconcile.position_terms import unheld_title_term

STEFAN = {
    "professional_summary": {"de": "Produktionsleiter mit langjähriger Erfahrung in der Fertigung."},
    "work_experience": [
        {"id": "w1", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
         "start_date": "2017-04", "is_current": True,
         "responsibilities": [
             "Verantwortung für zwei Fertigungsbereiche (Spritzguss, Montage) mit 38 Mitarbeitenden.",
             "Direkte Führung von 38 Mitarbeitenden über drei Schichtleiter.",
         ]},
        {"id": "w2", "company": "Rasselstein GmbH", "role": "Fertigungsmeister / Schichtleiter",
         "start_date": "2011-01", "end_date": "2017-03",
         "responsibilities": ["Führung einer Schicht mit 14 Mitarbeitenden."]},
    ],
    "skills": [{"name": "Shopfloor-Management", "category": "domain"}],
}


def _row(concept: str, **extra) -> dict:
    return {"concept": concept, "surface_forms": [concept], "sources": ["required"],
            "fit_weight": 1.0, "status": "partial", "claimable": True,
            "evidence": "Verantwortung für zwei Fertigungsbereiche", **extra}


# ── the fact ────────────────────────────────────────────────────────────────


def test_a_title_the_vault_never_holds_is_unheld():
    assert unheld_title_term("Bereichsleiter", STEFAN) == "bereichsleiter"
    assert unheld_title_term("Head of Department", STEFAN) == "head of department"
    assert unheld_title_term("Stellvertretender Werkleiter", STEFAN)


def test_a_title_the_vault_holds_is_not_unheld():
    assert unheld_title_term("Produktionsleiter", STEFAN) is None
    assert unheld_title_term("Schichtleiter", STEFAN) is None  # role w2's own title


def test_the_persons_own_summary_holds_a_title_too():
    p = {**STEFAN, "professional_summary": {"de": "Erfahrener Bereichsleiter. Seit 14 Jahren in der Fertigung."}}
    assert unheld_title_term("Bereichsleiter", p) is None


def test_a_duty_phrase_or_a_skill_is_never_a_title_term():
    for term in ("Führung und Entwicklung von Schicht- und Bereichsleitern",
                 "Erfahrung als Bereichsleiter", "Führungserfahrung", "Shopfloor-Management",
                 "SAP PP", ""):
        assert unheld_title_term(term, STEFAN) is None, term


# ── the ledger floor, both seams ─────────────────────────────────────────────


def test_the_floor_demotes_only_a_claimable_unheld_title():
    rows = [_row("Bereichsleiter", jd_phrase="Sie führen Bereichsleiter."),
            _row("Schichtleiter"), _row("Shopfloor-Management"),
            _row("Werkleiter", status="gap", claimable=False, evidence="")]
    out = enforce_title_stance(rows, STEFAN)
    bl = out[0]
    assert (bl["status"], bl["claimable"], bl["title_unheld"]) == ("gap", False, True)
    assert bl["evidence"] == TITLE_UNHELD_EVIDENCE and "jd_phrase" not in bl
    assert out[1:] == rows[1:]


def test_SEAM_build_keyword_ledger_floors_the_title():
    ledger = build_keyword_ledger(
        [{"concept": "Bereichsleiter", "status": "partial",
          "evidence": "Verantwortung für zwei Fertigungsbereiche (Spritzguss, Montage) mit 38 Mitarbeitenden."},
         {"concept": "Schichtleiter", "status": "direct",
          "evidence": "Direkte Führung von 38 Mitarbeitenden über drei Schichtleiter."}],
        ["Bereichsleiter", "Schichtleiter"], [], [],
        profile_json=STEFAN,
    )
    by = {r["concept"]: r for r in ledger}
    assert by["Bereichsleiter"]["claimable"] is False and by["Bereichsleiter"]["title_unheld"]
    assert by["Schichtleiter"]["claimable"] is True


def test_SEAM_refresh_floors_a_persisted_claimable_title():
    ledger, changed = refresh_ledger_against_vault(
        [_row("Bereichsleiter"), _row("Schichtleiter")], STEFAN, seam="test")
    assert changed
    assert [r["claimable"] for r in ledger] == [False, True]


def test_the_floor_leaves_the_row_once_the_person_holds_the_title():
    held = {**STEFAN, "work_experience": [
        {**STEFAN["work_experience"][0], "role": "Produktionsleiter / Bereichsleiter"},
        STEFAN["work_experience"][1],
    ]}
    assert enforce_title_stance([_row("Bereichsleiter")], held)[0]["claimable"] is True


# ── the corrector's under-claim finding ──────────────────────────────────────


def test_the_finding_says_a_career_summary_belongs_to_no_single_entry():
    text = _issue_text(UnderclaimedConcept(
        concept="Produktionsmanagement",
        evidence="14 Jahre Erfahrung als Produktions- und Betriebsleiter sowie Verantwortung für zwei Fertigungsbereiche.",
        fit_weight=1.0, surface_forms=("Produktionsmanagement",), tag_only=True,
    ))
    assert "It may restate the profile's professional summary" in text
    assert ("a career-wide duration, a job title or any sentence of that summary belongs to "
            "no single work entry and is never copied into a work entry's bullet") in text
