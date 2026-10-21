# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build-2 adversarial F-A (founder ruling R3-a) — an unclaimable vault skill
grounds nothing, in the Oracle and in the ATS keyword check alike.

ADR-061 cl. 3 (+ amendment 2026-08-08): an ``unconfirmed`` entry backs nothing
and a ``denied`` one was retracted; every claim surface reads the vault through
``exclude_unconfirmed``. Two did not: the Oracle's ``audit_document`` built its
index from the FULL profile, so a CV skill chip "Unternehmensgruppe" (profile
row ``denied``) and "Anlagenbau-Controlling" (``unconfirmed``) read
``grounded`` (adversarial CV 7333ecbc, evidence ``skills[13]``/``skills[12]``);
and the ATS keyword check's literal guard (``profile_literal_corpus``) read the
whole vault — the unclaimable rows and the receipts that added them — so a gap
keyword such a row names could never be flagged. ADR-066: one predicate
(``entry_is_claimable``) for both halves.

Rows below are shaped like the persisted adversarial rows (synthetic user).
"""
import pytest

from applire.services.ats_audit import _keyword_coverage, _norm, grounding_vault_index
from applire.services.keyword_ledger import profile_literal_corpus
from applire.services.oracle import audit_document


def _skill(name, status, sid):
    return {"id": sid, "name": name, "source": "transcribed", "status": status,
            "category": "domain", "proficiency": "intermediate", "experience_refs": [],
            "years_experience": None}


def _receipt(skill):
    return {"id": "r-" + skill["id"], "source": "manual_edit", "timestamp": "2026-10-21T16:16:19Z",
            "changes": [{"field": skill["name"], "action": "added", "section": "skills",
                         "new_value": skill, "old_value": None,
                         "rationale": f"Added {skill['name']} in skills (manual section edit)."}]}


_CONFIRMED = _skill("SAP CO", "confirmed", "00000000-0000-0000-0000-000000000001")
_UNCONFIRMED = _skill("Anlagenbau-Controlling", "unconfirmed", "00000000-0000-0000-0000-000000000002")
_DENIED = _skill("Unternehmensgruppe", "denied", "00000000-0000-0000-0000-000000000003")

VAULT = {
    "personal_info": {"name": "Ada Probe"},
    "work_experience": [{
        "id": "11111111-1111-1111-1111-111111111111",
        "company": "Weber Maschinenbau GmbH", "title": "Controllerin",
        "start_date": "2017-01", "end_date": "2026-06",
        "responsibilities": ["Ich habe den Monatsabschluss nach HGB für drei Werke verantwortet."],
    }],
    "skills": [_CONFIRMED, _UNCONFIRMED, _DENIED],
    "metadata": {"enrichment_history": [_receipt(s) for s in (_CONFIRMED, _UNCONFIRMED, _DENIED)]},
}


def _chips(report):
    return {c.claim.text: c.verdict.verdict for c in report.claims if c.claim.kind == "skill"}


@pytest.mark.asyncio
async def test_oracle_skill_chip_backed_only_by_an_unclaimable_row_is_unbacked():
    cv = {"summary": "", "work_experience": [],
          "skills": ["SAP CO", "Anlagenbau-Controlling", "Unternehmensgruppe"]}
    report = await audit_document("cv", VAULT, tailored_data=cv, document_language="de")
    assert _chips(report) == {
        "SAP CO": "grounded",
        "Anlagenbau-Controlling": "unbacked",
        "Unternehmensgruppe": "unbacked",
    }


@pytest.mark.asyncio
async def test_oracle_sentence_backed_only_by_an_unclaimable_row_is_not_grounded():
    letter = {"body": {"paragraphs": [
        "Ich bringe Erfahrung im Anlagenbau-Controlling mit.",
    ]}}
    report = await audit_document("cover_letter", VAULT, letter_data=letter, document_language="de")
    verdicts = [c.verdict for c in report.claims if "Anlagenbau-Controlling" in c.claim.text]
    assert verdicts and all(v.verdict != "grounded" for v in verdicts), verdicts


def test_literal_corpus_carries_no_unclaimable_entry_nor_its_receipt():
    corpus = profile_literal_corpus(VAULT)
    assert "sap co" in corpus
    assert "anlagenbau-controlling" not in corpus
    assert "unternehmensgruppe" not in corpus
    # a confirmed entry's own receipt stays (unchanged behaviour)
    assert "added sap co in skills" in corpus


def test_ats_flags_a_gap_keyword_only_an_unclaimable_row_names():
    ledger = [{"concept": "Unternehmensgruppe", "status": "gap", "claimable": False,
               "surface_forms": ["Unternehmensgruppe", "Konzernunternehmen"], "sources": ["keyword"],
               "fit_weight": 0.0, "evidence": ""}]
    text = _norm("Controlling für die gesamte Unternehmensgruppe verantwortet.")
    cov = _keyword_coverage(
        text, ["Unternehmensgruppe"], ledger, profile_literal_corpus(VAULT) or None,
        grounding_vault_index(VAULT), document_language="de",
    )
    assert cov.present_unsupported == ["Unternehmensgruppe"]


@pytest.mark.asyncio
async def test_a_typed_profile_is_filtered_the_same_way():
    from applire.schemas.profile import MasterProfileData
    from applire.services.oracle import verify_claim

    typed = MasterProfileData.model_validate(VAULT)
    from applire.services.oracle.extract import Claim

    for name, want in (("SAP CO", "grounded"), ("Unternehmensgruppe", "unbacked")):
        v = await verify_claim(Claim(text=name, location="skills[0]", kind="skill"), typed)
        assert v.verdict == want, (name, v)
