# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sorbet quality-1 delivery-2a rec 215: a short employer name anchors the guard.

Stefan answered "… habe ich bei Weberit die Vorbereitung der ISO-45001-Zertifizierung
für die Produktion begleitet …". Luna targeted the bullets at his Rasselstein role
(as captured; 2/5 on replay). The #243 guard matched only the core name
"Weberit Kunststofftechnik", so the answer named no employer, the op went through,
and his CV printed the facts under Rasselstein, grounded, with no flag.

The rule: a core's distinctive leading token (at least four characters, not a
generic company word or a legal form, in no other employer's name) names that
employer too. Mirrors the Oracle's #372 fallback. The over-fire this rule would have
added on channel 3 (q2 rec 464, found over 159 captured reconcile calls) is closed
by requiring the batch to have drawn on the anchor's own sentence.

**Every fixture here is SYNTHETIC**: the delivery runs' invented personas.
"""
from __future__ import annotations

from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile import attribution as AT
from applire.services.profile.reconcile.attribution import enforce_attribution
from applire.services.profile.reconcile.ops import AddBullets, RequestConfirmation

_WEB, _RAS = "w-weberit", "w-rasselstein"

# rec 215, verbatim
_GAP = "Arbeitssicherheit und Normen"
_QUESTION = (
    "Welche konkreten Erfahrungen hast du im Arbeitssicherheitsmanagement, mit ISO 45001 "
    "oder allgemein in der Arbeitssicherheit gesammelt? Nenne bitte – falls zutreffend – die "
    "Rolle und den Zeitraum, konkrete Maßnahmen oder Audits sowie messbare Ergebnisse oder "
    "Verbesserungen."
)
_ANSWER = (
    "Ich bin ausgebildeter Sicherheitsbeauftragter und habe bei Weberit die Vorbereitung der "
    "ISO-45001-Zertifizierung für die Produktion begleitet: Gefährdungsbeurteilungen "
    "überarbeitet, Unterweisungssystem neu aufgesetzt. Die Unfallquote (LTIF) in meinem "
    "Bereich habe ich in fünf Jahren von 8,2 auf 3,1 gesenkt. Die Zertifizierung selbst hat "
    "der QHSE-Leiter verantwortet, ich war der Produktionspart."
)
_RESP = (
    "Vorbereitung der ISO-45001-Zertifizierung für die Produktion begleitet; "
    "Gefährdungsbeurteilungen überarbeitet und Unterweisungssystem neu aufgesetzt."
)
_ACH = "Unfallquote (LTIF) im eigenen Bereich in fünf Jahren von 8,2 auf 3,1 gesenkt."


def _profile(*extra: WorkEntry) -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(id=_WEB, company="Weberit Kunststofftechnik GmbH",
                      role="Produktionsleiter", start_date="2017-04", is_current=True),
            WorkEntry(id=_RAS, company="Rasselstein Umformtechnik GmbH",
                      role="Fertigungsmeister / Schichtleiter", start_date="2011-08",
                      end_date="2017-03"),
            *extra,
        ]
    )


def _ni(answer: str = _ANSWER) -> dict:
    return {"gap": _GAP, "question": _QUESTION, "answer": answer}


def _ops(target: str = _RAS) -> list:
    return [AddBullets(target=target, responsibilities=[_RESP], achievements=[_ACH])]


def _held(out: list) -> list[str]:
    return [
        f["text"]
        for o in out
        if isinstance(o, RequestConfirmation)
        for f in (o.context or {}).get("flagged", [])
    ]


def _applied(out: list, target: str) -> list[str]:
    return [
        b
        for o in out
        if isinstance(o, AddBullets) and o.target == target
        for b in (*o.responsibilities, *o.achievements)
    ]


def test_rec_215_both_facts_are_held_behind_weberit_not_written_to_rasselstein():
    out = enforce_attribution(_ops(), profile=_profile(), new_info=_ni(), source="interview")
    assert _applied(out, _RAS) == []
    assert sorted(_held(out)) == sorted([_RESP, _ACH])
    card = next(o for o in out if isinstance(o, RequestConfirmation))
    assert card.context["anchor_employer"] == "Weberit Kunststofftechnik GmbH"
    assert card.context["target_employer"] == "Rasselstein Umformtechnik GmbH"


def test_rec_215_on_the_right_employer_passes_untouched():
    # the 3/5 replays that targeted Weberit
    out = enforce_attribution(_ops(_WEB), profile=_profile(), new_info=_ni(), source="interview")
    assert _held(out) == [] and sorted(_applied(out, _WEB)) == sorted([_RESP, _ACH])


def test_the_short_form_is_each_employers_own_distinctive_leading_token():
    cands = AT._company_candidates(_profile())
    assert AT._distinctive_short_forms(cands) == {
        "Weberit Kunststofftechnik": "Weberit",
        "Rasselstein Umformtechnik": "Rasselstein",
    }


def test_two_employers_sharing_the_leading_token_anchor_nothing():
    prof = _profile(WorkEntry(id="w-wlog", company="Weberit Logistik GmbH", role="Disponent",
                              start_date="2009-01", end_date="2011-07"))
    out = enforce_attribution(_ops(), profile=prof, new_info=_ni(), source="interview")
    assert _held(out) == [], "an ambiguous short form names nobody (fail open)"
    assert sorted(_applied(out, _RAS)) == sorted([_RESP, _ACH])


def test_an_answer_naming_both_employers_keeps_the_existing_fail_open():
    answer = _ANSWER.replace("bei Weberit", "bei Weberit und davor bei Rasselstein")
    out = enforce_attribution(_ops(), profile=_profile(), new_info=_ni(answer), source="interview")
    assert _held(out) == []


def test_a_customer_or_supplier_that_is_not_an_employer_anchors_nothing():
    answer = _ANSWER.replace("bei Weberit", "für unseren Kunden Bosch und den Lieferanten Arburg")
    out = enforce_attribution(_ops(), profile=_profile(), new_info=_ni(answer), source="interview")
    assert _held(out) == []


def test_a_generic_or_short_leading_token_is_never_a_short_form():
    prof = MasterProfileData(work_experience=[
        WorkEntry(id="g", company="Global Kunststoff GmbH", role="Leiter", start_date="2020-01"),
        WorkEntry(id="t", company="Technik Zentrum Süd GmbH", role="Leiter", start_date="2015-01"),
        WorkEntry(id="k", company="KMW Werkzeugbau GmbH", role="Leiter", start_date="2010-01"),
    ])
    assert AT._distinctive_short_forms(AT._company_candidates(prof)) == {}


# q2 rec 464 (2026-11-14), verbatim answer: the short form "Brauner" anchors the
# answer, but the batch wrote only the first sentence's facts, rightly, to the
# current employer.
_Q2_ANSWER = (
    "Für unseren Sondermaschinenbereich habe ich 2022 die Bewertung langfristiger Aufträge "
    "nach Fertigstellungsgrad eingeführt, in der HGB-konformen Variante: Meilensteinlogik mit "
    "der Projektleitung definiert, monatliche POC-Bewertung für sechs Großprojekte, "
    "Abweichungen ans Management berichtet. Investitionsrechnungen für Maschinenbeschaffungen "
    "habe ich bei Brauner & Söhne gemacht. Make-or-buy-Entscheidungen habe ich keine eigene "
    "Erfahrung."
)
_Q2_BULLET = (
    "2022 die HGB-konforme Bewertung langfristiger Aufträge nach Fertigstellungsgrad für sechs "
    "Großprojekte eingeführt; Meilensteinlogik mit der Projektleitung definiert, monatliche "
    "POC-Bewertung durchgeführt und Abweichungen ans Management berichtet."
)


def test_q2_rec_464_a_batch_that_never_used_the_anchors_sentence_is_not_held():
    prof = MasterProfileData(work_experience=[
        WorkEntry(id="s", company="Schwarzwald Präzision GmbH", role="Financial Controller",
                  start_date="2019-02", is_current=True),
        WorkEntry(id="b", company="Brauner & Söhne Maschinenbau GmbH",
                  role="Junior Controllerin", start_date="2016-09", end_date="2019-01"),
    ])
    ni = {"gap": "Investitions- und Make-or-buy-Entscheidungen",
          "question": "Hast du POC-Bewertungen oder Make-or-buy-Entscheidungen begleitet?",
          "answer": _Q2_ANSWER}
    out = enforce_attribution([AddBullets(target="s", responsibilities=[_Q2_BULLET])],
                              profile=prof, new_info=ni, source="interview")
    assert _held(out) == [] and _applied(out, "s") == [_Q2_BULLET]


def test_the_vocabulary_mirrors_the_oracles_372_fallback():
    from applire.services.oracle import extract as oracle_extract

    assert oracle_extract._GENERIC_COMPANY_TOKENS <= AT._GENERIC_COMPANY_TOKENS
    assert oracle_extract._MIN_DISTINCTIVE_TOKEN_CHARS == AT._MIN_DISTINCTIVE_TOKEN_CHARS


def test_a_shared_leading_token_in_the_bullets_own_words_names_no_foreign_employer():
    # channel 2 reads the bullet's own words; "bei Weberit" on the Weberit Logistik
    # role must not read as naming the OTHER Weberit
    prof = _profile(WorkEntry(id="w-wlog", company="Weberit Logistik GmbH", role="Disponent",
                              start_date="2009-01", end_date="2011-07"))
    bullet = "Bei Weberit die Tourenplanung für zwölf Fahrzeuge eingeführt."
    out = enforce_attribution([AddBullets(target="w-wlog", responsibilities=[bullet])],
                              profile=prof, new_info=_ni("Ja, " + bullet), source="interview")
    assert _held(out) == [] and _applied(out, "w-wlog") == [bullet]


# MD-Q13 N3 (re-check at c2d4427f) — the reviewer's probe_k inputs. The short form
# is matched as written, on the turn read with its case kept, and not when a
# capitalised word outside the employer's name follows it.
_PROBE_BULLET = "Gefährdungsbeurteilungen überarbeitet."


def _probe(companies: list[tuple[str, str]], answer: str, target: str, bullet: str = _PROBE_BULLET):
    prof = MasterProfileData(work_experience=[
        WorkEntry(id=i, company=c, role="R", start_date="2010-01") for i, c in companies
    ])
    return enforce_attribution([AddBullets(target=target, responsibilities=[bullet])],
                               profile=prof, new_info={"gap": "g", "question": "Was?", "answer": answer},
                               source="interview")


def test_md_q13_n3_a_common_word_that_starts_an_employers_name_names_nobody():
    db = [("a", "Deutsche Bahn AG"), ("b", "Rasselstein Umformtechnik GmbH")]
    out = _probe(db, "Ich habe deutsche Kunden und neue Mitarbeiter bei der Schichtplanung betreut.", "b",
                 "Deutsche Kunden und neue Mitarbeiter bei der Schichtplanung betreut.")
    assert _held(out) == []
    neue = [("a", "Neue Energie GmbH"), ("b", "Rasselstein Umformtechnik GmbH")]
    assert _held(_probe(neue, "Ich habe neue Mitarbeiter eingearbeitet.", "b",
                        "Neue Mitarbeiter eingearbeitet.")) == []
    # sentence-initial, followed by a capitalised word that is not the employer's
    assert _held(_probe(neue, "Neue Mitarbeiter habe ich eingearbeitet.", "b",
                        "Neue Mitarbeiter eingearbeitet.")) == []


def test_md_q13_n3_a_customer_sharing_the_first_word_is_not_the_employer():
    pair = [("a", "Weberit Kunststofftechnik GmbH"), ("b", "Rasselstein Umformtechnik GmbH")]
    out = _probe(pair, "Für Kunden wie Weberit Automotive habe ich die Gefährdungsbeurteilungen "
                       "überarbeitet.", "b")
    assert _held(out) == []


def test_md_q13_n3_the_employers_own_next_word_or_legal_form_still_names_it():
    pair = [("a", "Weberit Kunststofftechnik GmbH"), ("b", "Rasselstein Umformtechnik GmbH")]
    for answer in ("Bei Weberit GmbH habe ich die Gefährdungsbeurteilungen überarbeitet.",
                   "Bei Weberit habe ich die Gefährdungsbeurteilungen überarbeitet.",
                   "Die Gefährdungsbeurteilungen habe ich überarbeitet, bei Weberit."):
        assert _held(_probe(pair, answer, "b")) == [_PROBE_BULLET], answer


def test_md_q13_n3_a_shared_first_word_still_names_only_the_full_name():
    pair = [("a", "Siemens Energy AG"), ("b", "Siemens Healthineers AG")]
    assert _held(_probe(pair, "Bei Siemens habe ich die Linie optimiert.", "b", "Linie optimiert.")) == []
    assert _held(_probe(pair, "Bei Siemens Energy habe ich die Linie optimiert.", "b",
                        "Linie optimiert.")) == ["Linie optimiert."]
    assert _held(_probe(pair, "Bei Siemens Energy habe ich die Linie optimiert.", "a",
                        "Linie optimiert.")) == []


def test_md_q13_n3_a_lower_case_common_word_is_not_the_name_even_before_a_lower_case_word():
    neue = [("a", "Neue Energie GmbH"), ("b", "Rasselstein Umformtechnik GmbH")]
    assert _held(_probe(neue, "Ich habe neue und erfahrene Mitarbeiter eingearbeitet.", "b",
                        "Erfahrene und neue Mitarbeiter eingearbeitet.")) == []
    # Known residual (MD-Q13, collector): a text that STARTS with the word, followed
    # by a lower-case word ("Neue und erfahrene …"), still reads as the name.


def test_md_q13_n3_the_owning_sentence_channel_reads_the_short_form_as_written():
    # the batch writes to BOTH employers, so the whole-answer channel is silent;
    # only the owning sentence ("Bei Weberit …") can hold the first bullet
    answer = ("Bei Weberit habe ich die Gefährdungsbeurteilungen überarbeitet. "
              "Außerdem habe ich Schichtpläne für 38 Mitarbeitende erstellt.")
    ops = [AddBullets(target=_RAS, responsibilities=["Gefährdungsbeurteilungen überarbeitet."]),
           AddBullets(target=_WEB, responsibilities=["Schichtpläne für 38 Mitarbeitende erstellt."])]
    out = enforce_attribution(ops, profile=_profile(), new_info=_ni(answer), source="interview")
    assert _held(out) == ["Gefährdungsbeurteilungen überarbeitet."]
    assert _applied(out, _WEB) == ["Schichtpläne für 38 Mitarbeitende erstellt."]
