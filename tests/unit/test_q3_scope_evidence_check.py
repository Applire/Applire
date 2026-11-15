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


# ── MD-Q7 F2 (adversarial 1b, probe p2.py): parsed numbers, a people word, once ──
def _one(quote, cv_bullets, ledger_kind="team_size", entry="w1"):
    vault = {"personal_info": {"name": "M"}, "work_experience": [{
        "id": "w1", "company": "Weberit", "role": "Produktionsleiter", "start_date": "2019-01",
        "responsibilities": ["Allgemein.", quote]}], "skills": [], "education": []}
    prose = {"summary": "x", "work": [{"id": "w1", "bullets": cv_bullets, "projects": []}], "skills": []}
    notes = ComposeNotes()
    composed = compose_v2(prose, vault, notes=notes)
    ledger = [{"status": "partial", "claimable": True, "concept": "Team scope ~120",
               "bar": {"kind": ledger_kind, "attested": {"quote": quote, "unit": "headcount", "entry": entry}}}]
    ctx = V2CheckContext(profile_json=vault, keyword_ledger=ledger, job_dict={}, language="en")
    return "cv.scope_evidence_missing" in {f.check for f in v2_checks(prose, composed, notes, ctx)}


def test_locale_forms_of_one_number_satisfy_it_md_q7_f2():
    q = "Stellvertretung der Werkleitung für rund 1.200 Mitarbeitende, jeweils 4 Wochen."
    assert not _one(q, ["Deputised for the plant manager for about 1,200 employees."])
    assert not _one(q, ["Deputised for the plant manager for about 1200 employees."])
    assert not _one("Stellvertretung für rund 90,5 Mitarbeitende.", ["Deputy for roughly 90.5 employees."])
    assert not _one("Vertretung für rund 90 Mitarbeitende.", ["Deputy for roughly ninety employees."])


def test_a_year_a_percentage_or_a_duration_is_never_the_scale_md_q7_f2():
    # p2.py b: the year is not the scale; the CV carries the headcount without it.
    assert not _one("2021 vertrat ich die Werkleitung für rund 90 Mitarbeitende.",
                    ["Deputised for the plant manager covering about 90 employees."])
    # p2.py d/j: "4 Wochen" is a duration — a "4" never satisfies the 1,200.
    assert _one("Stellvertretung für rund 1.200 Mitarbeitende, 4 Wochen.", ["Deputy, 4 weeks."])
    # p2.py c: "90 %" is a percentage, not the 90 people.
    assert _one("Stellvertretung der Werkleitung für rund 90 Mitarbeitende, jeweils 2 bis 4 Wochen.",
                ["Cut scrap by 90 % across lines."])


def test_a_quote_without_a_people_figure_yields_nothing_md_q7_f2():
    assert not _one("Seit 2021 vertrete ich die Werkleitung jeweils 4 Wochen.", ["Deputy."])


def test_it_blocks_once_then_stays_advisory_md_q7_f2():
    """A later draft that still misses the line gets the finding as advisory, so no
    CV exhausts its rounds on it; the same draft re-checked stays blocking."""
    prose1 = {"summary": "Production leader.", "work": [
        {"id": "w1", "bullets": ["Led two manufacturing areas with 38 employees."], "projects": []},
        {"id": "w2", "bullets": ["Led a shift of 14 employees."], "projects": []}], "skills": []}
    prose2 = {**prose1, "summary": "Production leader, revised."}
    ctx = V2CheckContext(profile_json=_VAULT, keyword_ledger=_LEDGER, job_dict={}, language="en")

    def scope(prose):
        notes = ComposeNotes()
        composed = compose_v2(prose, _VAULT, notes=notes)
        return {f.check: f for f in v2_checks(prose, composed, notes, ctx)}["cv.scope_evidence_missing"]

    assert scope(prose1).blocking is True
    assert scope(prose1).blocking is True  # the same draft, re-checked
    assert scope(prose2).blocking is False


def test_the_cited_entry_owns_the_line_when_two_roles_hold_it_md_q7_f2():
    vault = {"personal_info": {"name": "M"}, "work_experience": [
        {"id": "w1", "company": "A", "role": "Leiter", "start_date": "2019-01",
         "responsibilities": ["Vertretung für rund 90 Mitarbeitende."]},
        {"id": "w2", "company": "B", "role": "Meister", "start_date": "2015-01", "end_date": "2018-12",
         "responsibilities": ["Vertretung für rund 90 Mitarbeitende."]}], "skills": [], "education": []}
    prose = {"summary": "x", "work": [
        {"id": "w1", "bullets": ["Deputy for about 90 employees."], "projects": []},
        {"id": "w2", "bullets": ["Shift lead."], "projects": []}], "skills": []}
    ledger = [{"status": "partial", "claimable": True, "concept": "Team scope ~120",
               "bar": {"kind": "team_size", "attested": {"quote": "Vertretung für rund 90 Mitarbeitende.",
                                                         "unit": "headcount", "entry": "Meister @ B"}}}]
    notes = ComposeNotes()
    composed = compose_v2(prose, vault, notes=notes)
    ctx = V2CheckContext(profile_json=vault, keyword_ledger=ledger, job_dict={}, language="en")
    f = {x.check: x for x in v2_checks(prose, composed, notes, ctx)}["cv.scope_evidence_missing"]
    assert "under w2 (B)" in f.text
