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
    cov = _cov(
        "Rheinwerk fertigt Kunststoff- und Verbundverpackungen für Konsumgüter- und Lebensmittelkunden – "
        "dieses Umfeld reizt mich. IFS, BRC und direkte Lebensmittelproduktion kenne ich nicht."
    )
    # ruling D-4c-b: the sentence also names ledger-denied terms (IFS, BRC) → the
    # candidate stating their own limit: no card at all (class S)
    assert cov.present_unsupported == []
    assert cov.present_unsupported_in_denial == ["Lebensmittel"]
    assert cov.present_unsupported_denials == {"Lebensmittel": ["direkte Produktion für Lebensmittelkunden"]}


_LEDGER = [
    {"concept": c, "status": st, "claimable": False, "surface_forms": [c], "sources": ["keyword"],
     "fit_weight": 1.0, "evidence": ""}
    for c, st in (("Lebensmittel", "gap"), ("IFS", "denied"), ("BRC", "denied"))
]
_NC = non_claim_names("Leiter Operations (m/w/d)", ["Rheinwerk Verpackungen GmbH"]).with_employer_clause()


def _cov(letter: str):
    return _keyword_coverage(_norm(letter), ["Lebensmittel", "IFS", "BRC"], _LEDGER, None,
                             grounding_vault_index(_vault()), _NC, document_language="de")


def test_a_real_claim_of_the_denied_thing_keeps_its_remove_only_card():
    """The Oracle grades this claim unverifiable/S (no close evidence) — the
    keyword card is its only signal, so it stays (A remove_only, never ask_true)."""
    cov = _cov("Bei Weberit habe ich die Lebensmittelproduktion geleitet. IFS und BRC kenne ich nicht.")
    assert cov.present_unsupported == ["Lebensmittel"]
    assert cov.present_unsupported_in_denial == []
    assert cov.present_unsupported_denials == {"Lebensmittel": ["direkte Produktion für Lebensmittelkunden"]}


def test_the_claim_sentences_are_only_the_ones_the_flag_read():
    """D-4c(a): the employer clause carries the term too, but only the claim
    sentence is listed — the surface counts and locates these."""
    cov = _cov("Rheinwerk fertigt Verpackungen für Lebensmittelkunden – dieses Umfeld reizt mich. "
               "Bei Weberit habe ich die Lebensmittelproduktion geleitet.")
    assert cov.present_unsupported == ["Lebensmittel"]
    assert cov.present_unsupported_sentences == {
        "Lebensmittel": ["bei weberit habe ich die lebensmittelproduktion geleitet."]
    }


def test_without_the_compound_probe_the_denial_did_not_cover_it(monkeypatch):
    """The baseline the fix rescues (the delivery run's empty map)."""
    from applire.services.profile.reconcile import stance

    monkeypatch.setattr(stance, "_denial_compound_probes", lambda *a, **k: [])
    assert denials_covering("Lebensmittel", ["Lebensmittel"], DENIALS, "") == []
