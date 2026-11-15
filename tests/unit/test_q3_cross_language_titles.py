# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q3 (founder question Q3-1, recommendation A): a role title in a
document whose language is not the vault's own is the writer's translation, also
when the vault records German aliases for it.

Bench 2026-11-15 (base 956bad2f, luna, operations_marcus_de.cross_de2en runs 0+1):
the writer translated every title in round 1; compose rejected "Production
Leader" and "Industrial Mechanic" (their aliases "Projektleiter" and
"Industriemechaniker / Vorarbeiter" are German), and the cv.recorded_name finding
("never translate or invent a name") had the terminal corrector revert every
title — the EN CV shipped "Produktionsleiter", "Fertigungsmeister / Schichtleiter",
"Industriemechaniker". Employer, degree and institution keep the check."""
from __future__ import annotations

from applire.services.cv_v2 import ComposeNotes, V2CheckContext, V2Terminal, compose_v2

_VAULT = {
    "personal_info": {"name": "Marcus"},
    "work_experience": [{
        "id": "w1", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
        "role_aliases": ["Projektleiter"], "start_date": "2019-01", "end_date": None,
        "responsibilities": ["Leitung der Serienfertigung mit drei Schichten und 60 Mitarbeitenden.",
                             "Begleitung der jährlichen ISO-9001-Audits als Bereichsverantwortlicher.",
                             # MD-Q7 F1: the alias is used in the candidate's own German
                             # sentence — the record says it is German (marcus shape).
                             "2023 als Projektleiter die Einführung eines MES-Systems verantwortet."],
    }],
    "education": [{"id": "e1", "degree": "Industriemeister Metall (IHK)", "institution": "IHK"}],
    "skills": [],
}
_PROSE = {
    "summary": "Production leader.",
    "work": [{"id": "w1", "role": "Production Manager", "company": "Weberit Plastics",
              "bullets": ["Led series production across three shifts."], "projects": []}],
    "education": [{"id": "e1", "degree": "Master craftsman in metalworking", "institution": "IHK"}],
}


def _compose(language):
    notes = ComposeNotes()
    out = compose_v2(_PROSE, _VAULT, notes=notes, document_language=language)
    return out, notes


def test_a_cross_language_title_with_aliases_is_the_writers_translation():
    out, notes = _compose("en")
    assert out.work_history[0].role == "Production Manager"
    assert not [n for n in notes.names_not_recorded if n[0] == "work w1 role"]


def test_the_same_language_keeps_the_recorded_name_check():
    out, notes = _compose("de")
    assert out.work_history[0].role == "Produktionsleiter"
    assert [n[0] for n in notes.names_not_recorded if n[0] == "work w1 role"] == ["work w1 role"]


def test_employer_and_degree_keep_the_check_across_languages():
    out, notes = _compose("en")
    assert out.work_history[0].company == "Weberit Kunststofftechnik GmbH"
    assert out.education[0].degree == "Industriemeister Metall (IHK)"
    locs = {n[0] for n in notes.names_not_recorded}
    assert {"work w1 company", "education e1 degree"} <= locs


def test_the_terminal_compose_passes_the_document_language():
    """Seam: V2Terminal.compose (every terminal round) hands ctx.language on."""
    t = V2Terminal(ctx=V2CheckContext(profile_json=_VAULT, keyword_ledger=[], job_dict={}, language="en"))
    out = t.compose(_PROSE, _VAULT, None)
    assert out.work_history[0].role == "Production Manager"


def test_the_language_round_compose_passes_the_document_language():
    """Seam: the language-round compose in services/cv.py passes document_language."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "backend/applire/services/cv.py").read_text()
    i = src.index("def _compose_for_language")
    assert "document_language=document_language" in src[i:i + 600]


# ── MD-Q7 F1 (adversarial 1b, probe p3.py): the waiver is per role ───────────
_DE = ["Verantwortlich für die Fertigungsplanung und Schichtleitung.",
       "Einführung von SAP PP in der Fertigung, Leitung des Teams und Abstimmung mit der Qualität."]


def _probe(role, aliases, writer_role, lang):
    vault = {"personal_info": {"name": "M"}, "work_experience": [{
        "id": "w1", "company": "Weberit", "role": role, "role_aliases": aliases,
        "start_date": "2019-01", "responsibilities": _DE}], "skills": [], "education": []}
    prose = {"summary": "x", "work": [{"id": "w1", "role": writer_role, "bullets": ["Led shift planning."],
                                       "projects": []}], "skills": []}
    notes = ComposeNotes()
    out = compose_v2(prose, vault, notes=notes, document_language=lang)
    return out.work_history[0].role, [n[0] for n in notes.names_not_recorded]


def test_a_recorded_alias_whose_language_is_unknown_keeps_the_check_md_q7_f1():
    """p3.py: "Production Manager" is recorded; the writer's "Head of Production" is
    neither recorded name — compose keeps the stored name and raises the finding,
    exactly as in a same-language document."""
    assert _probe("Produktionsleiter", ["Production Manager"], "Head of Production", "en") == (
        "Produktionsleiter", ["work w1 role"])
    assert _probe("Produktionsleiter", ["Production Manager"], "Head of Production", "de") == (
        "Produktionsleiter", ["work w1 role"])


def test_the_recorded_document_language_form_is_accepted_md_q7_f1():
    assert _probe("Produktionsleiter", ["Production Manager"], "Production Manager", "en") == (
        "Production Manager", [])


def test_a_name_in_the_document_language_blocks_the_waiver_md_q7_f1():
    """Own evidence: "Head of Operations" carries an English function word, so an EN
    document must pick a recorded form."""
    assert _probe("Produktionsleiter", ["Head of Operations"], "Production Leader", "en") == (
        "Produktionsleiter", ["work w1 role"])


def test_recorded_name_language_reads_the_record():
    from applire.services.cv_v2 import recorded_name_language

    sentences = ["2023 als Projektleiter die Einführung eines MES-Systems verantwortet.",
                 "Facharbeiter in der Instandhaltung, später Vorarbeiter."]
    assert recorded_name_language("Head of Production", []) == "en"
    assert recorded_name_language("Projektleiter", sentences) == "de"
    assert recorded_name_language("Production Manager", sentences) is None
    assert recorded_name_language("Industriemechaniker / Vorarbeiter", sentences,
                                  {"industriemechaniker": "de"}) == "de"
    assert recorded_name_language("Industriemechaniker / Vorarbeiter", sentences) is None
