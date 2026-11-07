# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-090 amended 2026-09-26 (WP-R, founder ruling R-2, option A): the clause that
describes the employer's business is not a candidate claim.

The clause starts at a mention of the employer's name (as analysed, without its
legal form, or its first word when that word has at least four letters, as a
whole word) and ends at the first first-person word, the sentence end or a
salutation. It is anchored on the name ONLY: "Sie/Ihr/your" is no anchor, because
on the captured letters that anchor masked the candidate's own intent sentences.
Sentences below are the captured synthetic delivery-run letters (Rheinwerk,
NovaPay, Arnold), normalised the way the audit normalises the PDF text.
"""
from __future__ import annotations

import pytest

from applire.services.ats_audit import (
    _CORPUS_FRAGMENT_BOUNDARY,
    _keyword_coverage,
    _norm,
    mask_non_claim_spans,
    non_claim_names,
)

_B = _CORPUS_FRAGMENT_BOUNDARY
_RHEINWERK = non_claim_names("Leiter Operations (m/w/d)", ["Rheinwerk Verpackungen GmbH"]).with_employer_clause()
_NOVAPAY = non_claim_names("Senior Backend Engineer — Payments Platform (m/f/d)", ["NovaPay GmbH"]).with_employer_clause()
_ARNOLD = non_claim_names("Senior Controller (m/w/d)", ["Arnold Antriebstechnik GmbH"]).with_employer_clause()


def _gap(concept: str) -> dict:
    return {
        "concept": concept, "surface_forms": [concept], "claimable": False,
        "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": "",
    }


def _masked(text: str, names) -> str:
    return mask_non_claim_spans(_norm(text), names)


# ── what the clause covers ──────────────────────────────────────────────────


def test_clause_from_the_short_name_to_the_sentence_end():
    t = ("Die Position als Leiter Operations bei der Rheinwerk Verpackungen GmbH spricht mich an, "
         "weil Rheinwerk hochwertige Kunststoff- und Verbundverpackungen für Konsumgüter- und "
         "Lebensmittelkunden fertigt. Meine Erfahrung in diskreter Fertigung zählt.")
    m = _masked(t, _RHEINWERK)
    for word in ("verbundverpackungen", "konsumgüter", "lebensmittelkunden"):
        assert word not in m
    assert "meine erfahrung in diskreter fertigung" in m


def test_clause_stops_at_the_first_person_word():
    t = ("NovaPay’s platform for European e-commerce merchants across checkout and settlement "
         "rails interests me and builds on my B2B invoicing, subscription billing and "
         "Stripe-based checkout experience.")
    m = _masked(t, _NOVAPAY)
    assert "settlement" not in m and "european e commerce" not in m
    assert "my b2b invoicing, subscription billing" in m


def test_a_list_comma_inside_the_employer_clause_does_not_stop_it():
    """#803 (Bug, R2-7; ADR-090 amended 2026-10-24): adv #4's comma stop cut the
    employer's own enumeration, so "settlement" and "payout rails" stayed in group 1
    with an add-to-profile exit. A comma that continues an enumeration to its
    coordinator keeps the clause open (8 of 10 captured employer-sentence rows clear)."""
    t = ("NovaPay’s platform for European e-commerce merchants across checkout, settlement and "
         "payout rails interests me.")
    m = _masked(t, _NOVAPAY)
    assert "european e commerce" not in m
    assert "settlement" not in m and "payout rails" not in m
    assert m.endswith("me.")


def test_803_felix_round2_sentence_masks_the_whole_enumeration():
    """The round-2 Felix letter (felix-024): four employer terms, all in one list."""
    t = ("NovaPay’s work on checkout, settlement, and payout rails for European e-commerce "
         "merchants particularly interests me, and I would welcome the opportunity.")
    m = _masked(t, _NOVAPAY)
    for gone in ("settlement", "payout rails", "e commerce"):
        assert gone not in m, gone
    assert "i would welcome the opportunity" in m


def test_803_list_after_an_operates_verb_and_before_the_candidates_clause():
    t = ("NovaPay operates checkout, settlement, and payout rails for European e-commerce "
         "merchants, and I would welcome the opportunity to contribute to these workflows.")
    m = _masked(t, _NOVAPAY)
    assert "settlement" not in m and "payout rails" not in m
    assert "i would welcome the opportunity to contribute to these workflows" in m


def test_803_a_comma_straight_after_the_name_still_stops():
    """adv #4 stays: nothing of the employer's was said before the comma."""
    t = "For NovaPay, seven years of Kubernetes and Kafka operations are what I bring."
    m = _masked(t, _NOVAPAY)
    assert "seven years of kubernetes and kafka operations" in m


def test_803_a_comma_after_the_possessive_alone_still_stops():
    t = "NovaPay’s, seven years of Kubernetes and Kafka operations are what I bring."
    m = _masked(t, _NOVAPAY)
    assert "seven years of kubernetes and kafka operations" in m


def test_803_a_comma_with_no_enumeration_after_it_stops():
    t = "NovaPay builds payment rails, seven years of Kafka taught me to value idempotency."
    m = _masked(t, _NOVAPAY)
    assert "seven years of kafka taught" in m


def test_803_comma_and_opens_a_new_clause_outside_an_enumeration():
    """", and …" continues only an enumeration the clause is already in."""
    t = "I admire NovaPay’s payments team, and have run Kafka consumers for seven years."
    m = _masked(t, _NOVAPAY)
    assert "have run kafka consumers for seven years" in m


def test_803_the_list_comma_still_stops_at_the_first_person_word():
    t = "NovaPay ships checkout, settlement and payouts, and my Kafka work fits that."
    m = _masked(t, _NOVAPAY)
    assert "settlement" not in m
    assert "my kafka work fits that" in m


def test_803_a_german_enumeration_runs_to_und():
    t = ("Die Rheinwerk Verpackungen GmbH fertigt Kunststoffverpackungen, Verbundverpackungen und "
         "Folien für Konsumgüter, und ich bringe Spritzguss-Erfahrung mit.")
    m = _masked(t, _RHEINWERK)
    assert "verbundverpackungen" not in m and "konsumgüter" not in m
    assert "ich bringe spritzguss erfahrung mit" in m


def test_803_flagged_term_in_the_employer_list_is_not_unsupported():
    letter = _norm("NovaPay’s work on checkout, settlement, and payout rails interests me.")
    cov = _keyword_coverage(letter, ["Settlement", "Payout rails"], [_gap("Settlement"), _gap("Payout rails")],
                            non_claim=_NOVAPAY)
    assert set(cov.present) == {"Settlement", "Payout rails"}
    assert cov.present_unsupported == []


def test_clause_stops_at_a_salutation_so_the_opening_claim_survives():
    """The PDF text has no sentence break between the recipient block and the
    salutation — without this stop the opening claim was masked on 4 of 16
    captured letters."""
    t = ("Rheinwerk Verpackungen GmbH Sehr geehrte Damen und Herren, mit 14 Jahren Erfahrung in "
         "der diskreten Fertigung und als Produktionsleiter bewerbe ich mich.")
    m = _masked(t, _RHEINWERK)
    assert "mit 14 jahren erfahrung in der diskreten fertigung und als produktionsleiter" in m


@pytest.mark.parametrize("pronoun", ["i", "me", "my", "ich", "mich", "mir", "meine", "meinem"])
def test_every_first_person_word_ends_the_clause(pronoun):
    t = f"Arnold fertigt Antriebe {pronoun} Lean Controlling."
    m = _masked(t, _ARNOLD)
    assert "antriebe" not in m
    assert f"{pronoun} lean controlling" in m


def test_the_short_name_anchors_only_as_a_whole_word():
    names = non_claim_names(None, ["Rhein Klinikum Bonn"]).with_employer_clause()
    m = _masked("Im Rheinland habe ich Intensivpflege geleitet.", names)
    assert "rheinland habe ich intensivpflege" in m
    assert _B not in m


def test_the_clause_never_crosses_a_sentence_end():
    names = non_claim_names(None, ["SAP SE"]).with_employer_clause()
    m = _masked("SAP hat Payroll. Danach kenne ich Payroll.", names)
    assert "hat payroll" not in m
    assert "danach kenne ich payroll" in m


def test_a_first_word_shorter_than_four_letters_is_no_anchor():
    names = non_claim_names(None, ["Ace Logistik GmbH"]).with_employer_clause()
    m = _masked("Ace ist gut. Logistik habe ich geleitet.", names)
    assert "ace ist gut" in m


def test_you_and_sie_are_no_anchor():
    t = ("Sie fertigen hochwertige Kunststoff- und Verbundverpackungen. Ihre Lean-Produktion "
         "konsequent weiterzuentwickeln reizt mich.")
    m = _masked(t, _RHEINWERK)
    assert "verbundverpackungen" in m and "lean produktion" in m


# ── the decision on the captured rows ───────────────────────────────────────


def test_employer_business_keyword_is_present_but_not_unsupported():
    t = _norm("NovaPay’s platform for European e-commerce merchants across checkout and settlement "
              "rails interests me.")
    cov = _keyword_coverage(t, ["Settlement"], [_gap("Settlement")], non_claim=_NOVAPAY)
    assert cov.present == ["Settlement"] and cov.present_unsupported == []


def test_the_same_word_claimed_after_the_first_person_word_still_flags():
    t = _norm("NovaPay runs settlement rails, and I have run settlement systems for five years.")
    cov = _keyword_coverage(t, ["Settlement"], [_gap("Settlement")], non_claim=_NOVAPAY)
    assert cov.present_unsupported == ["Settlement"]


def test_a_candidate_claim_in_a_sentence_without_the_employer_still_flags():
    t = _norm("Damit bringe ich Erfahrung mit der Digitalisierung der Fertigung mit.")
    cov = _keyword_coverage(
        t, ["Digitalisierung der Fertigung"], [_gap("Digitalisierung der Fertigung")],
        non_claim=_RHEINWERK,
    )
    assert cov.present_unsupported == ["Digitalisierung der Fertigung"]


# ── R-3: letters only ───────────────────────────────────────────────────────


def test_without_the_letter_flag_only_the_names_are_masked():
    """A CV bullet has no first-person word; a clause opened by an employer name
    there would run through the candidate's own claim (ruling R-3)."""
    names = non_claim_names(None, ["Siemens Healthineers AG"])
    m = _masked("Siemens Energy, München. Führte die SAP-Einführung für 400 Nutzer.", names)
    assert "energy, münchen" in m


def test_the_letter_audit_turns_the_clause_on_and_the_cv_audit_does_not():
    from applire.schemas.cv import TailoredCVData
    from applire.services.ats_audit import _audit_cv_text, _audit_letter_text

    text = ("NovaPay’s platform for European e-commerce merchants across checkout and settlement "
            "rails interests me.")
    names = non_claim_names("Senior Backend Engineer", ["NovaPay GmbH"])
    letter = _audit_letter_text(text, {"recipient": {"company": "NovaPay GmbH"}}, ["Settlement"],
                                [_gap("Settlement")], non_claim=names)
    assert letter.keywords.present_unsupported == []
    cv = _audit_cv_text(text, TailoredCVData.model_validate({"contact": {"name": "X"}}), ["Settlement"],
                        [_gap("Settlement")], non_claim=names)
    assert cv.keywords.present_unsupported == ["Settlement"]


def test_a_generic_first_word_opens_no_clause():
    """Adversarial finding 2 / R-4: "Deutsche" is also the language."""
    names = non_claim_names(None, ["Deutsche Bahn AG"]).with_employer_clause()
    m = _masked("Ich spreche fließend Deutsche und Englisch und habe zwei Jahre in Frankreich gelebt.", names)
    assert "deutsche und englisch und habe zwei jahre" in m
    # the name without legal form still anchors
    m2 = _masked("Deutsche Bahn betreibt Fernverkehr in Europa, was mich reizt.", names)
    assert "fernverkehr" not in m2


# ── #803 belt (founder ruling T-2 = B): the employer-sentence population ─────


def test_803_belt_marcus_round2_term_in_a_sentence_naming_the_employer_is_neutral():
    """ux-audit-2 marcus-071: "Konsumgüter" sits after a "weil Sie …" clause the mask
    cannot reach. It stays listed (take-out + edit), in the neutral population."""
    t = _norm("Die Rheinwerk Verpackungen GmbH spricht mich an, weil Sie Verpackungen für "
              "Konsumgüter fertigen. Ich habe zwölf Jahre eine Fertigung geleitet.")
    cov = _keyword_coverage(t, ["Konsumgüter"], [_gap("Konsumgüter")], non_claim=_RHEINWERK)
    assert cov.present_unsupported == ["Konsumgüter"]
    assert cov.present_unsupported_employer_sentence == ["Konsumgüter"]


def test_803_belt_a_second_occurrence_outside_the_employer_sentence_keeps_the_red_row():
    t = _norm("Die Rheinwerk Verpackungen GmbH spricht mich an, weil Sie Konsumgüter fertigen. "
              "Ich habe zehn Jahre Konsumgüter produziert.")
    cov = _keyword_coverage(t, ["Konsumgüter"], [_gap("Konsumgüter")], non_claim=_RHEINWERK)
    assert cov.present_unsupported == ["Konsumgüter"]
    assert cov.present_unsupported_employer_sentence == []


def test_803_belt_a_claim_in_a_sentence_without_the_employer_is_never_neutral():
    t = _norm("Bei der Rheinwerk Verpackungen GmbH reizt mich die Aufgabe. Damit bringe ich "
              "Erfahrung mit der Digitalisierung der Fertigung mit.")
    cov = _keyword_coverage(
        t, ["Digitalisierung der Fertigung"], [_gap("Digitalisierung der Fertigung")],
        non_claim=_RHEINWERK,
    )
    assert cov.present_unsupported_employer_sentence == []


def test_803_belt_the_known_cost_a_claim_inside_the_employer_sentence_goes_neutral():
    """The ruled trade (T-2 = B): a genuine claim written into the employer's own
    sentence loses its one-click add but stays listed with take-out + edit."""
    t = _norm("NovaPay runs settlement rails, and I have run settlement systems for five years.")
    cov = _keyword_coverage(t, ["Settlement"], [_gap("Settlement")], non_claim=_NOVAPAY)
    assert cov.present_unsupported == ["Settlement"]
    assert cov.present_unsupported_employer_sentence == ["Settlement"]


def test_803_belt_is_letters_only():
    cv_names = non_claim_names(None, ["Rheinwerk Verpackungen GmbH"])  # no employer_clause
    t = _norm("Rheinwerk Verpackungen GmbH: Konsumgüter-Linie aufgebaut.")
    cov = _keyword_coverage(t, ["Konsumgüter"], [_gap("Konsumgüter")], non_claim=cv_names)
    assert cov.present_unsupported_employer_sentence == []


# ── #803 reopened (build 7, R2-7): the letter addresses the employer ──────────
# Round 3 (ux-audit-3, luna): the delivered letters named no employer in the
# sentence that restated its business — Marcus "Ihre Fertigung … Verbundverpackungen",
# Felix "Your checkout, settlement and payout platform aligns with my …".


def _cov_letter(text: str, term: str, names):
    from applire.services.ats_audit import _norm_cased

    return _keyword_coverage(_norm(text), [term], [_gap(term)], non_claim=names, text_cased=_norm_cased(text))


def test_803_reopened_your_clause_is_neutral_felix_round3():
    t = ("I am applying for the Senior Backend Engineer role at NovaPay GmbH. Your checkout, settlement "
         "and payout platform aligns with my backend work on invoice-dunning workflows.")
    cov = _cov_letter(t, "Settlement", _NOVAPAY)
    assert cov.present_unsupported == ["Settlement"]
    assert cov.present_unsupported_employer_sentence == ["Settlement"]


def test_803_reopened_ihre_clause_is_neutral_marcus_round3():
    t = ("hiermit bewerbe ich mich als Leiter Operations (m/w/d) bei der Rheinwerk Verpackungen GmbH. "
         "Ihre Fertigung hochwertiger Kunststoff- und Verbundverpackungen für Konsumgüter- und "
         "Lebensmittelkunden verbindet sich mit Produktionsführung – Bereiche, in denen ich "
         "einschlägige Erfahrung mitbringe.")
    cov = _cov_letter(t, "Verbundverpackungen", _RHEINWERK)
    assert cov.present_unsupported_employer_sentence == ["Verbundverpackungen"]


def test_803_reopened_the_claim_after_the_first_person_word_stays_checked():
    """Main's acceptance (MD7-2): "Your team's … matches my five years of …" — the
    clause ends at the first first-person word; the claim half stays red."""
    t = "Your team's settlement work matches my five years of settlement engineering."
    cov = _cov_letter(t, "Settlement", _NOVAPAY)
    assert cov.present_unsupported == ["Settlement"]
    assert cov.present_unsupported_employer_sentence == []


def test_803_reopened_a_term_only_in_the_claim_half_stays_checked():
    t = "Your platform matches my five years of settlement engineering."
    cov = _cov_letter(t, "Settlement", _NOVAPAY)
    assert cov.present_unsupported_employer_sentence == []


def test_803_reopened_lowercase_ihre_is_their_not_your():
    """"ihre" (their/her) is no address — only the capitalised possessive is."""
    t = ("Ich habe drei Schichtleiter geführt und ihre Teams in der Verbundverpackungen-Fertigung "
         "aufgebaut.")
    cov = _cov_letter(t, "Verbundverpackungen", _RHEINWERK)
    assert cov.present_unsupported_employer_sentence == []


def test_803_reopened_a_pronoun_is_no_anchor():
    """"I can help you scale Kafka" carries the candidate's capability: "you" / "Sie"
    never open the clause, only the possessive does."""
    t = "I can help you scale settlement systems quickly."
    cov = _cov_letter(t, "Settlement", _NOVAPAY)
    assert cov.present_unsupported_employer_sentence == []
    t2 = "Gern unterstütze ich Sie beim Aufbau Ihrer Plattform mit Verbundverpackungen-Erfahrung aus zehn Jahren."
    cov2 = _cov_letter(t2, "Verbundverpackungen", _RHEINWERK)
    assert cov2.present_unsupported_employer_sentence == ["Verbundverpackungen"]  # inside "Ihrer Plattform …"


def test_803_reopened_without_case_data_german_is_not_read():
    """A caller passing no case-kept text keeps the English possessive only."""
    t = "Ihre Fertigung von Verbundverpackungen beeindruckt."
    cov = _keyword_coverage(_norm(t), ["Verbundverpackungen"], [_gap("Verbundverpackungen")], non_claim=_RHEINWERK)
    assert cov.present_unsupported_employer_sentence == []


def test_803_reopened_the_mask_never_reads_the_second_person_clause():
    """R-2 stands: "your" is no anchor of the MASK (the term stays flagged, listed)."""
    m = _masked("Your checkout and settlement platform interests me.", _NOVAPAY)
    assert "settlement" in m


# ── #803 reopened: the Oracle's employer-fact verdict joins the belt ────────


def _letter_report(terms, sentences, es=()):
    return {
        "document": "cover_letter",
        "keywords": {
            "present_unsupported": list(terms),
            "present_unsupported_employer_sentence": list(es),
            "present_unsupported_sentences": sentences,
            "present_unsupported_matches": {t: [{"form": t, "stem": False}] for t in terms},
        },
    }


def _truth(*employer_fact_texts, claim_texts=()):
    claims = [
        {"claim": {"text": x}, "verdict": {"verdict": "not_applicable", "detail_key": "triageEmployerFact"}}
        for x in employer_fact_texts
    ] + [
        {"claim": {"text": x}, "verdict": {"verdict": "grounded", "detail_key": None}} for x in claim_texts
    ]
    return {"claims": claims}


def test_803_reopened_triage_employer_fact_moves_the_term():
    from applire.services.ats_audit import apply_letter_employer_facts

    s = _norm("Die Fertigung hochwertiger Kunststoff- und Verbundverpackungen verbindet sich mit "
              "Produktionsführung – Bereiche, in denen ich Erfahrung mitbringe.")
    rep = _letter_report(["Verbundverpackungen"], {"Verbundverpackungen": [s]})
    out = apply_letter_employer_facts(rep, _truth(
        "Die Fertigung hochwertiger Kunststoff- und Verbundverpackungen verbindet sich mit Produktionsführung"
    ))
    assert out["keywords"]["present_unsupported_employer_sentence"] == ["Verbundverpackungen"]
    assert rep["keywords"]["present_unsupported_employer_sentence"] == []  # input untouched


def test_803_reopened_triage_a_second_sentence_keeps_the_red_row():
    from applire.services.ats_audit import apply_letter_employer_facts

    s1 = _norm("Die Fertigung von Verbundverpackungen beeindruckt mich.")
    s2 = _norm("Ich habe zehn Jahre Verbundverpackungen produziert.")
    rep = _letter_report(["Verbundverpackungen"], {"Verbundverpackungen": [s1, s2]})
    out = apply_letter_employer_facts(rep, _truth("Die Fertigung von Verbundverpackungen beeindruckt mich."))
    assert out["keywords"]["present_unsupported_employer_sentence"] == []


def test_803_reopened_triage_candidate_claims_never_move():
    from applire.services.ats_audit import apply_letter_employer_facts

    s = _norm("Ich habe zehn Jahre Verbundverpackungen produziert.")
    rep = _letter_report(["Verbundverpackungen"], {"Verbundverpackungen": [s]})
    out = apply_letter_employer_facts(rep, _truth(claim_texts=["Ich habe zehn Jahre Verbundverpackungen produziert."]))
    assert out["keywords"]["present_unsupported_employer_sentence"] == []


def test_803_reopened_triage_refinement_is_letters_only_and_never_raises():
    from applire.services.ats_audit import apply_letter_employer_facts

    cv = dict(_letter_report(["X"], {"X": ["x"]}), document="cv")
    assert apply_letter_employer_facts(cv, _truth("x")) is cv
    assert apply_letter_employer_facts(None, None) is None
    garbage = {"document": "cover_letter", "keywords": {"present_unsupported": ["X"], "present_unsupported_sentences": "nope"}}
    assert apply_letter_employer_facts(garbage, {"claims": "bad"}) is garbage
    assert apply_letter_employer_facts(garbage, _truth("x")) is garbage
