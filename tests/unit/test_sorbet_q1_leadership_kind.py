# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q1 cluster (b), ruling Q-2: the CV's per-role fact line names the
KIND of leadership the person's own text names, or only the count.

Platform-1 delivery run (#832): Katrin's vault said "Fachliche Anleitung von zwei
Werkstudierenden" and "fachliche Führung …"; the delivered CV printed
"Führungsverantwortung für 3 Personen" — leadership she never claimed. Stefan's
"Direkte Führung von 38 Mitarbeitenden" names no kind, so the line now says only
"38 Personen im Verantwortungsbereich" until the interview's one ask is answered.

Seams: the reader (role_facts.leadership_kind), the single writer
(cv._apply_role_facts), the shared phrase producer (templates.filters.role_facts,
used by every template and the .docx), and the planner helper Q2 asks from.
"""
from __future__ import annotations

import pytest

from applire.schemas.cv import TailoredCVData
from applire.services.cv import _apply_role_facts
from applire.services.profile.role_facts import (
    entries_needing_leadership_kind,
    leadership_kind,
)
from applire.templates.filters import role_facts


def _e(*bullets: str, **extra) -> dict:
    return {"id": extra.pop("id", "w1"), "company": "X GmbH", "role": "Controller",
            "responsibilities": list(bullets), **extra}


@pytest.mark.parametrize("bullet, kind", [
    ("Fachliche Anleitung von zwei Werkstudierenden.", "functional"),
    ("Seit zwei Jahren fachliche Führung von zwei Werkstudierenden.", "functional"),
    ("Ich führe das Projektteam fachlich.", "functional"),
    ("Teamleitung ohne Personalverantwortung für fünf Entwickler.", "functional"),
    ("Functionally led four analysts across two sites.", "functional"),
    ("Dotted-line responsibility for the plant controllers.", "functional"),
    ("No direct reports; guided three juniors.", "functional"),
    ("Disziplinarische Führung von 12 Mitarbeitenden.", "disciplinary"),
    ("Personalverantwortung für acht Mitarbeitende.", "disciplinary"),
    ("Line manager of six engineers.", "disciplinary"),
    ("Managed five direct reports.", "disciplinary"),
])
def test_the_kind_is_read_from_the_persons_own_words(bullet, kind):
    assert leadership_kind(_e(bullet)) == kind


@pytest.mark.parametrize("bullets", [
    ("Direkte Führung von 38 Mitarbeitenden über drei Schichtleiter.",),
    ("Kommentierung für Geschäftsführung und Beirat; fachliche Abstimmung mit dem Vertrieb.",),
    ("Fachliche Verantwortung für das Reporting.",),
    ("Fachliche Abstimmung der Monatsberichte mit dem Vertriebsleiter.",),
    ("Bericht an die Geschäftsführung, ohne Personalverantwortung.",),
    ("Leite das Team fachlich und disziplinarisch.",),  # both kinds → no single kind
    ("Fachliche Anleitung von zwei Werkstudierenden.", "Disziplinarische Führung von 4 Mitarbeitenden."),
    (),
])
def test_neither_or_both_kinds_is_no_kind(bullets):
    assert leadership_kind(_e(*bullets)) is None


def test_the_title_is_never_read_for_the_kind():
    assert leadership_kind(_e("Monatsabschluss.", role="Disziplinarischer Teamleiter")) is None


@pytest.mark.parametrize("kind, lang, n, line", [
    ("functional", "de", 3, "Fachliche Anleitung von 3 Personen"),
    ("functional", "de", 1, "Fachliche Anleitung von 1 Person"),
    ("disciplinary", "de", 38, "Disziplinarische Führung von 38 Mitarbeitenden"),
    (None, "de", 38, "38 Personen im Verantwortungsbereich"),
    (None, "de", 1, "1 Person im Verantwortungsbereich"),
    ("functional", "en", 3, "Functional guidance of 3 people"),
    ("disciplinary", "en", 38, "Line management of 38 employees"),
    (None, "en", 14, "14 people in area of responsibility"),
    ("unknown-kind", "de", 5, "5 Personen im Verantwortungsbereich"),
])
def test_the_line_names_the_kind_or_only_the_count(kind, lang, n, line):
    assert role_facts({"team_size": n, "leadership_kind": kind}, lang) == line


def test_the_retired_phrase_is_never_produced():
    for lang in ("de", "en"):
        for kind in ("functional", "disciplinary", None):
            out = role_facts({"team_size": 12, "leadership_kind": kind}, lang)
            assert "Führungsverantwortung" not in out and "Responsible for a team" not in out


def _tailored(*ids_and_drafted_kinds) -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "Test"},
        "work_history": [
            {"id": i, "company": "X", "role": "R", "bullets": ["b"], "leadership_kind": k}
            for i, k in ids_and_drafted_kinds
        ],
    })


def test_the_single_writer_carries_the_vault_kind_and_never_the_drafts():
    profile = {"work_experience": [
        _e("Fachliche Anleitung von zwei Werkstudierenden.", id="w1", team_size=3),
        _e("Direkte Führung von 38 Mitarbeitenden.", id="w2", team_size=38),
        _e("Disziplinarische Führung des Teams.", id="w3", team_size=None),
    ]}
    # The draft claims "disciplinary" everywhere — none of it may survive.
    out = _apply_role_facts(
        _tailored(("w1", "disciplinary"), ("w2", "disciplinary"), ("w3", "disciplinary"),
                  ("w-unmatched", "disciplinary")),
        profile,
    )
    got = {w.id: (w.team_size, w.leadership_kind) for w in out.work_history}
    assert got == {
        "w1": (3, "functional"),
        "w2": (38, None),
        "w3": (None, None),          # no headcount → no kind on the line
        "w-unmatched": (None, None),
    }
    assert role_facts(out.work_history[0], "de") == "Fachliche Anleitung von 3 Personen"
    assert role_facts(out.work_history[1], "de") == "38 Personen im Verantwortungsbereich"


def test_the_planner_helper_lists_headcounts_with_no_kind():
    profile = {"work_experience": [
        _e("Fachliche Anleitung von zwei Werkstudierenden.", id="k", team_size=3),
        _e("Direkte Führung von 38 Mitarbeitenden.", id="s", team_size=38),
        _e("Schichtführung.", id="z", team_size=0),
        _e("Kein Team.", id="n"),
    ]}
    assert entries_needing_leadership_kind(profile) == [
        {"entry_id": "s", "company": "X GmbH", "role": "Controller", "team_size": 38},
    ]


def test_the_planner_helper_contract_shape_is_pinned():
    """MD-Q1: WP-Q2's planner builds its question from exactly these keys —
    entry_id, company, role, team_size (an int) — in entry order."""
    from applire.schemas.profile import MasterProfileData

    raw = {"work_experience": [
        _e("Direkte Führung von 38 Mitarbeitenden.", id="a", team_size=38, company="Weberit"),
        _e("Führung einer Schicht.", id="b", team_size=14, company="Rasselstein", role="Meister"),
    ]}
    for profile in (raw, MasterProfileData.model_validate(raw)):
        got = entries_needing_leadership_kind(profile)
        assert [list(g) for g in got] == [["entry_id", "company", "role", "team_size"]] * 2
        assert [g["entry_id"] for g in got] == ["a", "b"]
        assert got[0] == {"entry_id": "a", "company": "Weberit", "role": "Controller", "team_size": 38}
        assert all(type(g["team_size"]) is int for g in got)
