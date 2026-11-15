# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q3: the attested scale line (ADR-070) the writer is told to carry in its
role — marcus' deputy cover of ~90 people — was never drafted in 4 of 6 bench CVs
(base 956bad2f, luna, 2026-11-15) while the letters stated it. A blocking v2 finding
names it to the corrector; presence is a figure fact over the owning role's text."""
from __future__ import annotations

from applire.services.cv_v2 import ComposeNotes, V2CheckContext, compose_v2, v2_checks

_QUOTE = ("In Urlaubs- und Krankheitsvertretung des Betriebsleiters mehrfach den "
          "Gesamtstandort mit rund 90 Mitarbeitenden geführt, jeweils 2 bis 4 Wochen am Stück.")
_VAULT = {
    "personal_info": {"name": "Marcus"},
    "work_experience": [
        {"id": "w1", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
         "start_date": "2017-04", "team_size": 38,
         "responsibilities": ["Leitung von zwei Fertigungsbereichen mit 38 Mitarbeitenden.", _QUOTE]},
        {"id": "w2", "company": "Rasselstein", "role": "Schichtleiter", "start_date": "2011-08",
         "end_date": "2017-03", "responsibilities": ["Führung einer Schicht mit 14 Mitarbeitenden."]},
    ],
    "skills": [], "education": [],
}
_LEDGER = [{
    "concept": "Team scope ~120", "status": "partial", "claimable": True,
    "bar": {"kind": "team_size", "value": 120.0,
            "attested": {"unit": "site headcount covered as deputy",
                         "entry": "Produktionsleiter @ Weberit Kunststofftechnik GmbH", "quote": _QUOTE}},
}]


def _findings(w1_bullets, ledger=_LEDGER):
    prose = {"summary": "Production leader.", "work": [
        {"id": "w1", "bullets": w1_bullets, "projects": []},
        {"id": "w2", "bullets": ["Led a shift of 14 employees."], "projects": []}], "skills": []}
    notes = ComposeNotes()
    composed = compose_v2(prose, _VAULT, notes=notes)
    ctx = V2CheckContext(profile_json=_VAULT, keyword_ledger=ledger, job_dict={}, language="en")
    return {f.check: f for f in v2_checks(prose, composed, notes, ctx)}


def test_a_missing_attested_line_is_a_blocking_finding_naming_role_and_quote():
    f = _findings(["Led two manufacturing areas with 38 employees."])["cv.scope_evidence_missing"]
    assert f.blocking is True
    assert "under w1 (Weberit Kunststofftechnik GmbH)" in f.text and "rund 90 Mitarbeitenden" in f.text


def test_the_figure_in_its_role_satisfies_it_in_any_language():
    found = _findings(["Led two manufacturing areas with 38 employees.",
                       "Led the whole site of around 90 employees as deputy plant manager for 2-4 weeks."])
    assert "cv.scope_evidence_missing" not in found


def test_no_attested_line_no_finding():
    ledger = [{**_LEDGER[0], "bar": {"kind": "team_size", "value": 120.0}}]
    assert "cv.scope_evidence_missing" not in _findings(["Led two areas."], ledger)


def test_a_small_figure_of_the_quote_does_not_satisfy_it():
    """"jeweils 2 bis 4 Wochen": a "2" in another bullet of the role is not the scale."""
    found = _findings(["Led 2 manufacturing areas with 38 employees for 4 years."])
    assert "cv.scope_evidence_missing" in found
