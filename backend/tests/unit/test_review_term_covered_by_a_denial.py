# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build-2 delivery F2 (SF-REVIEW.18, D2-5's surface): the letter review listed the
red finding "Lebensmittelkunden — nichts in deinem Profil belegt das" with the button
"Stimmt, in mein Profil aufnehmen", although the candidate had told the interview
"Ich habe auch nie direkt für Lebensmittelkunden produziert" (recorded denial
"direkte Produktion für Lebensmittelkunden"). The button invited the user to write the
very claim they had disclaimed into their profile.

The fact "a recorded denial covers this term" is the SAME predicate the ledger floor
uses (``stance.is_denied_concept`` with ``denial_release_corpus`` as the release —
ADR-059), computed at audit time from the vault index and carried on the report as
``keywords.present_unsupported_denials``. The review surface reads it; the
add-evidence door refuses such a finding (409 ``finding_denied``).
"""
from applire.services.ats_audit import _keyword_coverage, _norm, grounding_vault_index
from applire.services.oracle.matchers import build_vault_index
from applire.services.profile.reconcile.stance import denial_release_corpus, denials_covering

DENIAL = "direkte Produktion für Lebensmittelkunden"


def _vault(denials=(DENIAL,), skills=("Lean Management",), role="Produktionsleiter"):
    return {
        "personal_info": {"name": "Ada Probe"},
        "work_experience": [{
            "id": "11111111-1111-1111-1111-111111111111",
            "company": "Weberit Kunststofftechnik GmbH", "role": role,
            "start_date": "2017-01", "end_date": None,
            "responsibilities": ["Zwei Fertigungsbereiche mit 38 Mitarbeitenden geführt."],
        }],
        "skills": [{"name": s} for s in skills],
        "metadata": {"denied_concepts": [
            {"concept": d, "statement": "Ich habe nie direkt für Lebensmittelkunden produziert.",
             "source": "interview", "date": "2026-10-21"} for d in denials
        ]},
    }


_LEDGER = [{
    "concept": "Lebensmittelkunden", "status": "gap", "claimable": False,
    "surface_forms": ["Lebensmittelkunden", "Kunden aus der Lebensmittelindustrie"],
    "sources": ["keyword"],
}]
_LETTER = _norm(
    "Die Position spricht mich an, weil Sie Verpackungen für Konsumgüter- und "
    "Lebensmittelkunden fertigen. Meine Erfahrung in der Fertigung bringe ich ein."
)


# ── the predicate ────────────────────────────────────────────────────────────


def test_a_term_inside_a_denied_compound_is_covered_by_that_denial():
    v = _vault(denials=("IFS", DENIAL))
    assert denials_covering("Lebensmittelkunden", [], ["IFS", DENIAL], denial_release_corpus(v)) == [DENIAL]


def test_a_declared_denial_covers_its_own_term():
    assert denials_covering("IFS", ["IFS-Standard"], ["IFS", DENIAL], "") == ["IFS"]


def test_an_attested_label_outside_the_compound_releases_the_term():
    # "Produktion" is a whole word inside the denial, but the vault attests it as
    # a skill label of its own — the containment branch's release (ADR-059 #207).
    v = _vault(skills=("Produktion",))
    assert denials_covering("Produktion", [], [DENIAL], denial_release_corpus(v)) == []


def test_a_form_pulled_in_by_the_compound_is_released_by_the_entrys_own_attested_name():
    # SF-GAP.10 / E-4: the release is judged at the entry's full width — the form
    # "Produktion" sits inside the denial, the term's own name is attested.
    v = _vault(skills=("Fertigung",))
    assert denials_covering("Fertigung", ["Produktion"], [DENIAL], denial_release_corpus(v)) == []


def test_an_unrelated_term_is_covered_by_nothing():
    assert denials_covering("Payments Platform", ["Payments"], [DENIAL, "IFS"], "") == []


# ── the report fact, through the vault index every audit seam builds ─────────


def test_the_vault_index_carries_the_recorded_denials_and_their_release_corpus():
    v = _vault(denials=("IFS", DENIAL))
    idx = grounding_vault_index(v)
    assert idx.denied_concepts == ("IFS", DENIAL)
    assert idx.denial_release_text == denial_release_corpus(v)


def test_a_present_unsupported_term_names_the_denial_that_covers_it():
    cov = _keyword_coverage(_LETTER, ["Lebensmittelkunden"], _LEDGER,
                            vault_index=grounding_vault_index(_vault()))
    assert cov.present_unsupported == ["Lebensmittelkunden"]
    assert cov.present_unsupported_denials == {"Lebensmittelkunden": [DENIAL]}


def test_without_a_covering_denial_the_map_stays_empty():
    cov = _keyword_coverage(_LETTER, ["Lebensmittelkunden"], _LEDGER,
                            vault_index=build_vault_index(_vault(denials=("SAP MM",))))
    assert cov.present_unsupported == ["Lebensmittelkunden"]
    assert cov.present_unsupported_denials == {}


def test_without_a_vault_index_the_map_stays_empty():
    cov = _keyword_coverage(_LETTER, ["Lebensmittelkunden"], _LEDGER)
    assert cov.present_unsupported_denials == {}
