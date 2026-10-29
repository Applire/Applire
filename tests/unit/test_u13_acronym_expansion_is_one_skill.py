# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""U-13 (Hazelnut build 5, delivery run 5): "CI/CD" and "continuous integration and
delivery" name ONE skill, in both directions, whatever sibling forms the keyword
ledger happened to list.

Run 5 rep a graded the chip "Continuous integration and delivery" unbacked. Rep b
graded the same chip grounded, from the same vault line, because rep b's ledger row
listed "CI/CD" as a sibling form and rep a's listed only "CI/CD pipelines". The
person then added "CI/CD (GitHub Actions)" in their own words, and "Jetzt prüfen"
still said unbacked. A closed acronym table (``oracle/matchers/acronyms.py``),
read symmetrically inside ``ground_skill_claim``, makes the verdict a fact.

One test proves that the Oracle audit and the review actions ("Ja" →
``support_in_profile``, the removal re-check ``skill_backed_now``) give the same
verdict for both ledger shapes. Synthetic data only.
"""
from __future__ import annotations

import asyncio
import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from applire.services.oracle.matchers import build_vault_index, ground_skill_claim
from applire.services.oracle.matchers.acronyms import EQUIVALENCE_CLASSES, acronym_equivalents
from applire.services.oracle.selfaudit import build_self_audit_report

CHIP = "Continuous integration and delivery"
BULLET = "CI/CD with GitHub Actions: test lanes, release builds, container images to a registry."

VAULT = {
    "personal_info": {"name": "Alex Muster"},
    "work_experience": [
        {"id": "w-own", "company": "Musterwerk", "role": "Founder & Lead Developer",
         "start_date": "2026-05", "is_current": True,
         "responsibilities": ["Own the product architecture.", BULLET], "achievements": []},
    ],
    "projects": [],
    "skills": [{"name": "Python"}],
}

# rep a's ledger row: no bare "CI/CD" sibling; rep b's: with it
LEDGER_A = [{"concept": CHIP, "sources": ["required"], "claimable": True, "status": "partial",
             "surface_forms": [CHIP, "CI/CD pipelines"], "evidence": BULLET}]
LEDGER_B = [{"concept": CHIP, "sources": ["required"], "claimable": True, "status": "partial",
             "surface_forms": ["CI/CD", "CI/CD pipelines", CHIP], "evidence": BULLET}]


def _no_ci_vault() -> dict:
    v = copy.deepcopy(VAULT)
    v["work_experience"][0]["responsibilities"] = ["Own the product architecture."]
    return v


def _with_own_words_skill() -> dict:
    """The person's own words, entered through "Im Profil eintragen"."""
    v = _no_ci_vault()
    v["skills"].append({"name": "CI/CD (GitHub Actions)"})
    return v


def _audit_verdict(vault: dict, chip: str, ledger) -> str:
    cv = {"summary": "", "skills": [chip], "work_history": []}
    report = asyncio.run(build_self_audit_report(
        vault, tailored_data=cv, document_language="en", keyword_ledger=ledger))
    (claim,) = [c for c in report["claims"] if c["claim"]["text"] == chip]
    return claim["verdict"]["verdict"]


def _recheck_backed(vault: dict, chip: str, ledger) -> bool:
    """``review_actions.skill_backed_now`` with the profile and ledger stubbed."""
    from applire.services import review_actions

    record = SimpleNamespace(job_analysis_id=uuid.uuid4())
    with (
        patch("applire.services.profile._get_latest",
              new=AsyncMock(return_value=SimpleNamespace(profile_json=vault))),
        patch("applire.services.gap._latest_gap_analysis",
              new=AsyncMock(return_value=SimpleNamespace(keyword_ledger=ledger))),
    ):
        return asyncio.run(review_actions.skill_backed_now(record, chip, None, user_id=uuid.uuid4()))


def _support_backed(vault: dict, chip: str) -> bool:
    from applire.services.review_actions import support_in_profile

    return support_in_profile([chip], vault) is not None


@pytest.mark.parametrize("ledger", [LEDGER_A, LEDGER_B], ids=["rep-a-ledger", "rep-b-ledger"])
@pytest.mark.parametrize("vault_fn", [lambda: copy.deepcopy(VAULT), _with_own_words_skill],
                         ids=["bullet", "own-words-skill"])
def test_audit_and_review_actions_agree_for_both_rep_shapes(ledger, vault_fn):
    vault = vault_fn()
    assert _audit_verdict(vault, CHIP, ledger) == "grounded"
    assert _recheck_backed(vault, CHIP, ledger) is True
    assert _support_backed(vault, CHIP) is True


@pytest.mark.parametrize("ledger", [LEDGER_A, LEDGER_B, None], ids=["rep-a", "rep-b", "no-ledger"])
def test_without_ci_evidence_every_door_says_unbacked(ledger):
    vault = _no_ci_vault()
    assert _audit_verdict(vault, CHIP, ledger) == "unbacked"
    assert _recheck_backed(vault, CHIP, ledger) is False
    assert _support_backed(vault, CHIP) is False


def test_both_directions_ground_on_the_vault_alone():
    idx = build_vault_index(VAULT)
    assert ground_skill_claim(CHIP, idx) is not None  # expansion → acronym in the vault
    v = _no_ci_vault()
    v["work_experience"][0]["responsibilities"].append(
        "Built continuous integration and delivery pipelines for the product.")
    assert ground_skill_claim("CI/CD", build_vault_index(v)) is not None  # acronym → expansion


def test_the_table_is_closed_symmetric_and_whole_phrase():
    # symmetric: every member maps to every other member of its class
    for cls in EQUIVALENCE_CLASSES:
        for form in cls:
            alts = {" ".join(a.lower().split()) for a in acronym_equivalents(form)}
            assert alts == set(cls) - {form}, form
    assert acronym_equivalents("CI/CD (GitHub Actions)")[0] == (
        "continuous integration and delivery (GitHub Actions)")
    # negative controls: no derived initialisms, no partial words
    assert acronym_equivalents("SAP") == []
    assert acronym_equivalents("CI") == []
    assert acronym_equivalents("Continuous improvement") == []
    assert acronym_equivalents("Storage") == [], "no 'rag' inside a word"
    assert acronym_equivalents("Python (Django)") == [], "a parenthetical is no equivalence"


def test_a_parenthetical_in_the_vault_makes_no_equivalence():
    v = _no_ci_vault()
    v["skills"] = [{"name": "Python (Django)"}]
    idx = build_vault_index(v)
    assert ground_skill_claim("Flask", idx) is None
    assert ground_skill_claim("Continuous improvement", build_vault_index(VAULT)) is None
