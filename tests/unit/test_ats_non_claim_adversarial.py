# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The E2 adversarial pass's probes on WP-R (Nougat pre-tag, 2026-09-26), kept as
regression tests. Each asserts the CORRECT behaviour. Main-session rulings: an
employer-name variant that is a JD keyword / ledger form is not masked (adv #2);
the employer clause also ends at a comma (adv #4); the clause stop list is the
letter producer's own ``_SALUTATION_OPENERS`` (adv #5). Adv #6 (skill-bearing
two-word title) is out of scope (Strawberry). Synthetic personas only."""
from __future__ import annotations

import pytest

from applire.services.ats_audit import _keyword_coverage, _norm, non_claim_names


def _gap(concept: str, *forms: str) -> dict:
    return {
        "concept": concept, "surface_forms": [concept, *forms], "claimable": False,
        "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": "",
    }


# ── P-R1: the employer's name IS the skill (vendor postings) ────────────────
# R-1 masks every whole-word occurrence of the employer name, in CVs and letters.
# When the employer is a product vendor, its name is also the skill the posting
# asks for, and the candidate's unsupported claim of that skill leaves group 1.

def test_r1_vendor_employer_name_hides_an_unsupported_product_claim_on_a_cv():
    names = non_claim_names("Senior Solutions Architect (m/w/d)", ["MongoDB Inc."])
    cv = _norm(
        "Daniel Weber — Senior Solutions Architect. "
        "Designed sharded MongoDB clusters serving 40k requests per second at Finleap."
    )
    cov = _keyword_coverage(cv, ["MongoDB"], [_gap("MongoDB")], non_claim=names)
    # the candidate claims MongoDB in their OWN bullet — no vault evidence (gap)
    assert cov.present_unsupported == ["MongoDB"], cov.present_unsupported


def test_r1_vendor_employer_name_hides_a_compound_product_claim():
    names = non_claim_names("SAP Berater FI/CO (m/w/d)", ["SAP SE"])
    cv = _norm("Fünf Jahre Einführung von SAP S/4HANA Finance bei der Rheinwerk GmbH geleitet.")
    cov = _keyword_coverage(cv, ["SAP S/4HANA"], [_gap("SAP S/4HANA", "S/4HANA")], non_claim=names)
    assert cov.present_unsupported == ["SAP S/4HANA"], cov.present_unsupported


def test_r1_generic_word_employer_masks_the_word_everywhere():
    # "Software AG" → employer variant "software" masks every "software" in the CV
    names = non_claim_names("Senior Backend Engineer (m/w/d)", ["Software AG"])
    cv = _norm("Led software architecture reviews for a 12-person payments team.")
    cov = _keyword_coverage(cv, ["Software architecture"], [_gap("Software architecture")], non_claim=names)
    assert cov.present_unsupported == ["Software architecture"], cov.present_unsupported


# ── P-R2: the R-2 clause runs through a comma into the candidate's claim ─────

def test_r2_clause_runs_past_a_comma_into_the_candidates_claim():
    names = non_claim_names("Senior Backend Engineer (m/f/d)", ["NovaPay GmbH"])
    letter_names = names.with_employer_clause()
    letter = _norm(
        "Dear Sir or Madam, "
        "for NovaPay, seven years of Kubernetes operations are exactly what I bring to the team."
    )
    cov = _keyword_coverage(letter, ["Kubernetes"], [_gap("Kubernetes")], non_claim=letter_names)
    assert cov.present_unsupported == ["Kubernetes"], cov.present_unsupported


# ── P-R3: salutation stop list narrower than the producer's salutation set ───
# services/cover_letter.py::_SALUTATION_OPENERS recognises werte / sg / liebe /
# hello / hi / to whom it may concern; the clause stop knows only sehr geehrte /
# dear / hallo / guten tag. The recipient block's company name opens a clause that
# runs through an EN date (no full stop) and the subject into the opening claim.

def _en_letter(salutation: str) -> str:
    return _norm(
        "Daniel Weber Berlin daniel@example.org "
        "NovaPay GmbH Talstrasse 5 10115 Berlin "
        "26 September 2026 "
        "Application for Senior Backend Engineer (m/f/d) — NovaPay GmbH "
        f"{salutation} "
        "Seven years of Kafka stream processing in production are what I would bring."
    )


def test_r3_baseline_dear_stops_the_clause():
    names = non_claim_names("Senior Backend Engineer (m/f/d)", ["NovaPay GmbH"]).with_employer_clause()
    cov = _keyword_coverage(_en_letter("Dear Ms Schneider,"), ["Kafka"], [_gap("Kafka")], non_claim=names)
    assert cov.present_unsupported == ["Kafka"]



@pytest.mark.parametrize("salutation", [
    "Hello Ms Schneider,", "Hi Anna,", "To whom it may concern,",
    "Liebe Frau Schneider,", "Werte Frau Schneider,",
])
def test_r3_other_producer_salutations_do_not_stop_the_clause(salutation):
    names = non_claim_names("Senior Backend Engineer (m/f/d)", ["NovaPay GmbH"]).with_employer_clause()
    cov = _keyword_coverage(_en_letter(salutation), ["Kafka"], [_gap("Kafka")], non_claim=names)
    assert cov.present_unsupported == ["Kafka"], salutation


# ── pins for the three fixes (beyond the probes) ─────────────────────────────


def test_the_full_vendor_name_with_legal_form_is_still_masked():
    """adv #2 drops only the variant that IS a skill: the recipient block's
    "MongoDB Inc." is still the posting's name, so a header-only MongoDB row clears."""
    from applire.services.ats_audit import mask_non_claim_spans

    names = non_claim_names("Senior Solutions Architect (m/w/d)", ["MongoDB Inc."]).excluding_skills(["MongoDB"])
    assert names.employers == ("mongodb inc.",)
    text = _norm("MongoDB Inc. Palo Alto. Dear Ms Chen, I led data platform migrations.")
    cov = _keyword_coverage(text, ["MongoDB"], [_gap("MongoDB")], non_claim=names)
    assert cov.present == ["MongoDB"] and cov.present_unsupported == []
    assert "mongodb" not in mask_non_claim_spans(text, names)


def test_a_skill_word_never_anchors_an_employer_clause():
    from applire.services.ats_audit import mask_non_claim_spans

    names = (non_claim_names(None, ["Kafka Systems GmbH"]).with_employer_clause()
             .excluding_skills(["Kafka"]))
    m = mask_non_claim_spans(_norm("Kafka streams processing is what I know."), names)
    assert "kafka streams processing is what" in m


def test_every_producer_salutation_stops_the_clause():
    """One list, reconciled with its producer (adv #5)."""
    from applire.services.ats_audit import mask_non_claim_spans
    from applire.services.cover_letter import _SALUTATION_OPENERS

    names = non_claim_names(None, ["NovaPay GmbH"]).with_employer_clause()
    for opener in _SALUTATION_OPENERS:
        text = _norm(f"NovaPay GmbH 26 September 2026 {opener.strip()} Anna Kafka streams are what I run")
        m = mask_non_claim_spans(text, names)
        assert "kafka streams" in m, opener
