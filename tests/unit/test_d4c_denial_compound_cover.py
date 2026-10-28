# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-4c (Hazelnut build 5, Marcus delivery run): the letter's honest "IFS, BRC und
direkte Lebensmittelproduktion kenne ich nicht" matched the JD term
"Lebensmittel", which is no whole word of the recorded denial "direkte Produktion
für Lebensmittelkunden" — so the review asked "Stimmt das?" and "Ja" would have
written the denied thing into the vault. A single-word term that opens a compound
word of a recorded denial is covered by that denial (document side only; the
ledger floor's predicate is unchanged). Synthetic Marcus case."""

from __future__ import annotations

from applire.services.ats_audit import _keyword_coverage, _norm, grounding_vault_index, non_claim_names
from applire.services.profile.reconcile.stance import denials_covering, is_denied_concept

DENIALS = ["IFS", "BRC", "direkte Produktion für Lebensmittelkunden", "Verbundverpackungen"]


def test_a_compound_part_of_a_denial_is_covered_by_it():
    assert denials_covering("Lebensmittel", ["Lebensmittel"], DENIALS, "") == [
        "direkte Produktion für Lebensmittelkunden"
    ]


def test_the_attested_bare_word_releases_it_and_the_ledger_floor_is_unchanged():
    assert denials_covering("Lebensmittel", ["Lebensmittel"], DENIALS, "Lebensmittel") == []
    assert is_denied_concept("Lebensmittel", DENIALS, "") is False  # floor predicate as before
    assert denials_covering("Leb", ["Leb"], DENIALS, "") == []  # too short to open a compound


def _vault() -> dict:
    return {
        "personal_info": {"name": "Stefan Brandt"},
        "work_experience": [{"id": "w1", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
                             "start_date": "2017", "responsibilities": ["Zwei Fertigungsbereiche mit 38 Mitarbeitenden."]}],
        "skills": [{"id": "s1", "name": "Spritzguss", "status": "confirmed"}],
        "metadata": {"denied_concepts": [
            {"concept": c, "date": "2026-10-28", "source": "interview", "statement": "Nein.", "denial_level": "direct"} for c in DENIALS
        ]},
    }


def test_the_letters_honest_denial_is_never_asked_true():
    letter = _norm(
        "Rheinwerk fertigt Kunststoff- und Verbundverpackungen für Konsumgüter- und Lebensmittelkunden – "
        "dieses Umfeld reizt mich. IFS, BRC und direkte Lebensmittelproduktion kenne ich nicht."
    )
    ledger = [{"concept": "Lebensmittel", "status": "gap", "claimable": False, "surface_forms": ["Lebensmittel"],
               "sources": ["keyword"], "fit_weight": 1.0, "evidence": ""}]
    nc = non_claim_names("Leiter Operations (m/w/d)", ["Rheinwerk Verpackungen GmbH"]).with_employer_clause()
    vault = _vault()
    cov = _keyword_coverage(letter, ["Lebensmittel"], ledger, None, grounding_vault_index(vault), nc,
                            document_language="de")
    assert cov.present_unsupported == ["Lebensmittel"]
    # the review reads this map: covered by a recorded denial → no "add to profile" (ask_true)
    assert cov.present_unsupported_denials == {"Lebensmittel": ["direkte Produktion für Lebensmittelkunden"]}


def test_without_the_compound_probe_the_denial_did_not_cover_it(monkeypatch):
    """The baseline the fix rescues (the delivery run's empty map)."""
    from applire.services.profile.reconcile import stance

    monkeypatch.setattr(stance, "_denial_compound_probes", lambda *a, **k: [])
    assert denials_covering("Lebensmittel", ["Lebensmittel"], DENIALS, "") == []
