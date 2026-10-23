# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""E-2 (founder ruling, Hazelnut build 2; ADR-052 amended 2026-10-22, WP-T): a stated
degree, regulated title or named certification the vault never backs is ``unbacked``.

1. Detection — a positive and a negative sentence set (frames, hedges, register).
2. Backing — lenient family forms.
3. ``verify_claim`` — the one entry point every door uses (self-audit, agent
   ``audit_document``, ``cv_assist``): which verdicts the rule overrides, the
   other-language guard, the restatement seam, the localisable detail key.
"""
import copy

import pytest

from applire.schemas.oracle import Claim, ClaimVerdict
from applire.services.oracle import audit as audit_mod
from applire.services.oracle.audit import _claimable_index, verify_claim
from applire.services.oracle.credentials import (
    detect_credentials,
    fold,
    kind_for_location,
    unbacked_credentials,
)

_VAULT = {
    "professional_summary": {"de": "Erfahrene Finanzbuchhalterin mit Schwerpunkt Monatsabschluss.", "en": ""},
    "work_experience": [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "company": "Muster Logistik GmbH",
            "title": "Finanzbuchhalterin",
            "start_date": "2016-01",
            "end_date": None,
            "responsibilities": [
                "Monatsabschlüsse nach HGB vorbereitet",
                "Kreditorenbuchhaltung für 3 Gesellschaften geführt",
            ],
            "location": "Bremen",
        }
    ],
    "education": [
        {
            "institution": "Hochschule Bremen",
            "degree": "Dipl.-Kfm. (FH)",
            "field_of_study": "Betriebswirtschaft",
            "start_date": "2008",
            "end_date": "2012",
            "grade": None,
        }
    ],
    "skills": [{"name": "DATEV", "level": None, "category": "technical"}],
    "languages": [{"language": "Deutsch", "level": "Muttersprache", "is_native": True}],
    "certifications": [{"name": "Scrum Master (PSM I)", "issuer": "Scrum.org", "date": "2021"}],
    "contact": {"first_name": "Erika", "last_name": "Muster", "email": "erika@example.com", "location": "Bremen"},
}


def _vf(vault=_VAULT) -> str:
    return fold(" \n ".join(u.text for u in _claimable_index(vault).units))


# ------------------------------------------------------------- 1. detection

_POSITIVE = [
    # (text, kind, family)
    ("Als ausgebildete Bilanzbuchhalterin bringe ich Erfahrung im Abschluss mit.", "letter", "bilanzbuchhalter"),
    ("Ich bin Steuerberaterin und kenne die Anforderungen.", "letter", "steuerberater"),
    ("Mit meinem MBA verbinde ich Strategie und Zahlen.", "letter", "mba"),
    ("I am a Chartered Accountant with eight years in audit.", "letter", "chartered_acc"),
    ("My PMP certification helps me run large programmes.", "letter", "pmp"),
    ("Registered Nurse mit 8 Jahren Erfahrung auf der Intensivstation.", "cv", "registered_nurse"),
    ("Promovierte Chemikerin mit Fokus auf Analytik.", "cv", "doctorate"),
    ("Master of Science in Informatik, Schwerpunkt verteilte Systeme.", "cv", "master"),
    ("Zertifizierter Datenschutzbeauftragter für mittelständische Unternehmen.", "cv", "certified_x"),
]

_NEGATIVE = [
    # hedged / aspired / negated
    ("Derzeit bereite ich mich auf die Prüfung zur Bilanzbuchhalterin vor.", "letter"),
    ("Ich strebe den Abschluss als Steuerberaterin an.", "letter"),
    ("I am working towards my PMP certification.", "letter"),
    ("Ich habe keinen MBA, aber zehn Jahre Praxis.", "letter"),
    # a title the sentence does not ascribe to the candidate
    ("Ich betreue Wirtschaftsprüfer bei der Jahresabschlussprüfung.", "letter"),
    ("Monatsabschlüsse in Abstimmung mit dem Steuerberater erstellt.", "cv"),
    # letter register: no first person → a sentence about the employer or the role
    ("Ihr Team sucht eine erfahrene Bilanzbuchhalterin.", "letter"),
    ("Ihr Haus steuert seine Projekte nach PRINCE2 und ITIL.", "letter"),
    # "als" is a preposition, ALS a life-support course
    ("Ich arbeite als Teamleitung im Rechnungswesen.", "letter"),
]


@pytest.mark.parametrize("text,kind,family", _POSITIVE)
def test_detects_an_ascribed_credential(text, kind, family):
    assert family in [m.family for m in detect_credentials(text, kind=kind)]


@pytest.mark.parametrize("text,kind", _NEGATIVE)
def test_does_not_detect_a_hedged_or_unascribed_mention(text, kind):
    assert detect_credentials(text, kind=kind) == []


def test_acronym_is_case_sensitive():
    assert [m.family for m in detect_credentials("I hold ALS and BLS certificates.", kind="letter")] == ["life_support"]
    assert detect_credentials("ich arbeite als fachkraft", kind="letter") == []


def test_kind_for_location():
    assert kind_for_location("body.paragraphs[1][0]") == "letter"
    assert kind_for_location("text[3]") == "letter"
    assert kind_for_location("summary[0]") == "cv"
    assert kind_for_location("work_history[1].bullets[2]") == "cv"


# --------------------------------------------------------------- 2. backing


def test_family_forms_back_a_paraphrase():
    # vault "Dipl.-Kfm." backs a CV line "Diplom-Kaufmann"; vault "PSM I" backs "Scrum Master"
    assert unbacked_credentials("Diplom-Kauffrau mit 10 Jahren Erfahrung.", kind="cv", vault_folded=_vf()) == []
    assert unbacked_credentials("Mein PSM I hilft im Sprint.", kind="letter", vault_folded=_vf()) == []


def test_the_f_e_sentence_is_unbacked():
    missing = unbacked_credentials(
        "Als ausgebildete Bilanzbuchhalterin bringe ich Erfahrung im Abschluss mit.", kind="letter", vault_folded=_vf()
    )
    assert [m.matched for m in missing] == ["Bilanzbuchhalterin"]


# ---------------------------------------------------------- 3. verify_claim

_FE = "Als ausgebildete Bilanzbuchhalterin bringe ich Erfahrung im Monatsabschluss nach HGB mit."


@pytest.mark.asyncio
async def test_verify_claim_grades_the_f_e_sentence_unbacked_with_a_key():
    v = await verify_claim(Claim(text=_FE, location="body.paragraphs[1][0]"), _VAULT, None, document_language="de")
    assert v.verdict == "unbacked"
    assert v.checker == "grounding"
    assert "Bilanzbuchhalterin" in v.detail
    assert v.detail_key == "credentialUnbacked"
    assert v.detail_params == {"credential": "Bilanzbuchhalterin"}


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", ["grounded", "unverifiable"])
async def test_rule_overrides_only_grounded_and_unverifiable(monkeypatch, prior):
    async def fake(*a, **k):
        return ClaimVerdict(verdict=prior, checker="grounding", detail="x")

    monkeypatch.setattr(audit_mod, "_verify_claim_unchecked", fake)
    v = await verify_claim(Claim(text=_FE, location="body.paragraphs[0][0]"), _VAULT, None, document_language="de")
    assert v.verdict == "unbacked" and v.detail_key == "credentialUnbacked"


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", ["inflated", "misattributed", "not_applicable", "unbacked"])
async def test_rule_leaves_every_other_verdict_standing(monkeypatch, prior):
    stand = ClaimVerdict(verdict=prior, checker="numbers", detail="kept")

    async def fake(*a, **k):
        return stand

    monkeypatch.setattr(audit_mod, "_verify_claim_unchecked", fake)
    v = await verify_claim(Claim(text=_FE, location="body.paragraphs[0][0]"), _VAULT, None, document_language="de")
    assert v is stand


@pytest.mark.asyncio
async def test_rule_decides_a_restatement_seam_candidate(monkeypatch):
    cand = audit_mod._SeamCandidate(
        seam="restatement", claim_text=_FE, units=[], deny_verdict=None,
        unavailable_verdict=ClaimVerdict(verdict="unverifiable", checker="grounding"),
    )

    async def fake(*a, **k):
        return cand

    monkeypatch.setattr(audit_mod, "_verify_claim_unchecked", fake)
    v = await verify_claim(Claim(text=_FE, location="body.paragraphs[0][0]"), _VAULT, None, document_language="de")
    assert isinstance(v, ClaimVerdict) and v.verdict == "unbacked"


@pytest.mark.asyncio
async def test_other_language_pair_is_never_accused():
    en_vault = copy.deepcopy(_VAULT)
    en_vault["professional_summary"] = {"de": "", "en": "Experienced financial accountant focused on month-end close."}
    en_vault["work_experience"][0]["title"] = "Financial Accountant"
    en_vault["work_experience"][0]["responsibilities"] = [
        "Prepared the monthly closing under German GAAP for three entities",
        "Ran accounts payable for three group companies and their auditors",
    ]
    idx = _claimable_index(en_vault)
    assert audit_mod.holds_other_language(idx, "de")  # fixture claim, proven
    v = await verify_claim(Claim(text=_FE, location="body.paragraphs[0][0]"), en_vault, None, document_language="de")
    assert v.detail_key != "credentialUnbacked"


@pytest.mark.asyncio
async def test_backed_credential_is_not_accused():
    v = await verify_claim(
        Claim(text="Als Diplom-Kauffrau verantworte ich seit 2016 die Monatsabschlüsse nach HGB.", location="body.paragraphs[0][0]"),
        _VAULT, None, document_language="de",
    )
    assert v.detail_key != "credentialUnbacked"
