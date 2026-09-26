# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-090 amended 2026-09-26 (WP-R, founder ruling R-1): the ATS unsupported-claim
decision reads only the candidate's text.

The #736 delivery run's letter flagged ``Payments platform`` in group 1 because the
letter repeats the target job title *"Senior Backend Engineer — Payments Platform
(m/f/d)"*; *take it out* on that row would have rewritten the title. The Rheinwerk
letters of four earlier runs flagged ``Operations`` the same way ("Leiter
Operations (m/w/d)"). Shapes below are taken from those captured letters
(synthetic personas).
"""
from __future__ import annotations

from applire.services.ats_audit import (
    _CORPUS_FRAGMENT_BOUNDARY,
    NonClaimNames,
    _keyword_coverage,
    _norm,
    mask_non_claim_spans,
    non_claim_names,
    non_claim_names_for_job,
)


def _gap(concept: str, *forms: str) -> dict:
    return {
        "concept": concept, "surface_forms": [concept, *forms], "claimable": False,
        "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": "",
    }


_RHEINWERK = non_claim_names("Leiter Operations (m/w/d)", ["Rheinwerk Verpackungen GmbH"])
_LETTER_DE = _norm(
    "STEFAN BRANDT Rheinwerk Verpackungen GmbH 19. September 2026 "
    "Bewerbung: Leiter Operations (m/w/d) bei Rheinwerk Verpackungen GmbH "
    "Sehr geehrte Damen und Herren, als Produktionsleiter mit 14 Jahren Erfahrung "
    "bewerbe ich mich als Leiter Operations bei der Rheinwerk Verpackungen GmbH."
)


# ── the names ───────────────────────────────────────────────────────────────


def test_names_carry_title_with_and_without_gender_marker_and_employer_with_and_without_legal_form():
    assert _RHEINWERK.titles == ("leiter operations (m/w/d)", "leiter operations")
    assert _RHEINWERK.employers == ("rheinwerk verpackungen gmbh", "rheinwerk verpackungen")


def test_names_for_job_reads_role_title_and_company_name_and_none_without_job():
    class _Job:
        role_title = "Senior Backend Engineer — Payments Platform (m/f/d)"
        company_name = "NovaPay GmbH"

    n = non_claim_names_for_job(_Job())
    assert "senior backend engineer payments platform" in n.titles
    assert "novapay" in n.employers
    assert non_claim_names_for_job(None) is None


def test_empty_names_are_falsy_and_mask_nothing():
    empty = non_claim_names(None, [None, ""])
    assert not empty
    assert mask_non_claim_spans("leiter operations", empty) == "leiter operations"
    assert mask_non_claim_spans("leiter operations", None) == "leiter operations"


def test_plus_employers_adds_the_recipient_company_normalised():
    n = NonClaimNames(titles=("x",)).plus_employers("Arnold Antriebstechnik GmbH", None)
    assert n.employers == ("arnold antriebstechnik gmbh", "arnold antriebstechnik")


# ── the mask ────────────────────────────────────────────────────────────────


def test_mask_removes_every_title_and_employer_occurrence_and_keeps_the_candidate_text():
    masked = mask_non_claim_spans(_LETTER_DE, _RHEINWERK)
    assert "operations" not in masked
    assert "verpackungen" not in masked
    assert "produktionsleiter mit 14 jahren erfahrung" in masked
    assert _CORPUS_FRAGMENT_BOUNDARY in masked


def test_mask_boundary_cannot_be_bridged_by_a_phrase_across_it():
    # "Operations Excellence" would read across a masked title followed by
    # "excellence" if the mask left a plain space behind.
    n = non_claim_names("Leiter Operations", [])
    masked = mask_non_claim_spans(_norm("als Leiter Operations Excellence"), n)
    assert "operations excellence" not in masked


# ── the decision ─────────────────────────────────────────────────────────────


def test_title_only_keyword_is_present_but_not_unsupported():
    cov = _keyword_coverage(_LETTER_DE, ["Operations"], [_gap("Operations")], non_claim=_RHEINWERK)
    assert cov.present == ["Operations"]
    assert cov.present_unsupported == []
    assert cov.present_unsupported_matches == {}


def test_without_names_the_same_text_still_flags_the_title_keyword():
    """Back-compat: a caller passing no names gets the full-text decision exactly —
    the baseline the guard is built on (fails if the fixture stopped carrying the
    title keyword)."""
    cov = _keyword_coverage(_LETTER_DE, ["Operations"], [_gap("Operations")])
    assert cov.present_unsupported == ["Operations"]


def test_a_candidate_occurrence_keeps_the_row_and_records_only_the_candidate_form():
    text = _norm(
        "Application: Senior Backend Engineer — Payments Platform (m/f/d) at NovaPay GmbH. "
        "I am applying for the Senior Backend Engineer — Payments Platform (m/f/d) role. "
        "I built B2B invoicing and subscription billing at Finleap."
    )
    names = non_claim_names("Senior Backend Engineer — Payments Platform (m/f/d)", ["NovaPay GmbH"])
    ledger = [_gap("Payments platform", "Payments", "Subscription billing")]
    cov = _keyword_coverage(text, ["Payments platform"], ledger, non_claim=names)
    assert cov.present_unsupported == ["Payments platform"]
    forms = [m.form for m in cov.present_unsupported_matches["Payments platform"]]
    assert forms == ["Subscription billing"]
    # without the names, the title forms are what a take-out would have removed
    cov0 = _keyword_coverage(text, ["Payments platform"], ledger)
    assert [m.form for m in cov0.present_unsupported_matches["Payments platform"]] == [
        "Payments platform", "Payments", "Subscription billing",
    ]


def test_candidate_claim_in_own_sentence_still_flags():
    text = _norm(
        "Bewerbung: Leiter Operations (m/w/d) bei Rheinwerk Verpackungen GmbH. "
        "Damit bringe ich Erfahrung mit der Digitalisierung der Fertigung mit."
    )
    cov = _keyword_coverage(
        text, ["Digitalisierung der Fertigung"], [_gap("Digitalisierung der Fertigung")],
        non_claim=_RHEINWERK,
    )
    assert cov.present_unsupported == ["Digitalisierung der Fertigung"]


def test_employer_name_substring_keyword_is_not_unsupported():
    """``Verpackungen`` flagged on the l/runs deliveries was a substring of the
    employer's name ``Rheinwerk Verpackungen GmbH``."""
    text = _norm("Bewerbung bei Rheinwerk Verpackungen GmbH. Ich leite ein Werk.")
    cov = _keyword_coverage(text, ["Verpackungen"], [_gap("Verpackungen")], non_claim=_RHEINWERK)
    assert cov.present == ["Verpackungen"]
    assert cov.present_unsupported == []


# ── R-4 + adversarial-pass instrument fixes (2026-09-26) ────────────────────


def test_a_one_word_title_is_not_masked_so_the_candidates_own_role_still_flags():
    """Adversarial finding 1: role_title "Engineer" masked the candidate's own
    "Engineer bei Foobar AG" and a real unsupported claim left the list."""
    n = non_claim_names("Engineer", [])
    assert n.titles == ()
    text = _norm("Berufserfahrung: Engineer bei Foobar AG 2018–2020.")
    cov = _keyword_coverage(text, ["Engineer"], [_gap("Engineer")], non_claim=n)
    assert cov.present_unsupported == ["Engineer"]


def test_a_one_word_title_with_its_gender_marker_is_still_masked_as_the_posting_quote():
    n = non_claim_names("Controller (m/w/d)", [])
    assert n.titles == ("controller (m/w/d)",)


def test_all_genders_markers_are_stripped_from_the_title_variant():
    for marker in ("(all genders)", "(alle Geschlechter)", "(gn*)", "(w/m/d)"):
        n = non_claim_names(f"Backend Engineer {marker}", [])
        assert "backend engineer" in n.titles, marker


def test_the_name_mask_tolerates_a_pypdf_kerning_space():
    """Adversarial finding 5 — the same tolerance as ``_find`` (#399)."""
    n = non_claim_names("Office Manager (m/w/d)", [])
    masked = mask_non_claim_spans(_norm("Bewerbung als Of fice Manager bei Contoso."), n)
    assert "manager" not in masked


def test_the_name_mask_is_whole_word():
    n = non_claim_names("Data Engineer (m/w/d)", [])
    masked = mask_non_claim_spans(_norm("Ich habe Big Data Engineering betrieben."), n)
    assert "data engineering" in masked
    masked = mask_non_claim_spans(_norm("Ich war Bigdata Engineer bei Acme."), n)
    assert "bigdata engineer" in masked
