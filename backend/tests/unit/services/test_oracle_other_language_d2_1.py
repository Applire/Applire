# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#790 lines L10 + L12 — founder ruling D2-1 = A (ADR-068 amended 2026-10-20).

A literal miss on a pair whose vault may state the evidence in ANOTHER language
is "not checked across languages", never an absence-based accusation:

* the keyword check moves such a ``present_unsupported`` term into the neutral
  ``keyword-other-language`` not_applicable row (outside group 1);
* a skill chip with no literal match over a MIXED-language vault (the case
  ADR-068 seam A's dominant-language trigger is blind to) reads
  ``unverifiable``, not ``unbacked``. No model call on either half.
"""
import pytest

from applire.services.ats_audit import _keyword_coverage, _norm, _other_language_check
from applire.services.oracle import audit_document
from applire.services.oracle.matchers import build_vault_index, holds_other_language
from applire.services.oracle.matchers.vault import OTHER_LANGUAGE_MIN_SHARE

_DE_BULLETS = [
    "Ich habe die Stripe-Auszahlungen gegen die offenen Rechnungen im System abgeglichen.",
    "Ich habe die Abrechnung für die Händler auf eine neue Plattform umgestellt und betreut.",
    "Ich habe die monatlichen Berichte für die Geschäftsführung erstellt und präsentiert.",
]
_EN_BULLETS = [
    "I developed and owned the brand identity for the new product line of the company.",
    "I led the creative team through the rebrand and the launch of the new website.",
]


def _profile(bullets, skills=()):
    return {
        "personal_info": {"name": "Ada Probe"},
        "work_experience": [{
            "id": "11111111-1111-1111-1111-111111111111",
            "company": "NovaPay GmbH", "title": "Finance Ops",
            "start_date": "2019-01", "end_date": "2024-06",
            "responsibilities": list(bullets),
        }],
        "skills": [{"name": s} for s in skills],
    }


DE_VAULT = _profile(_DE_BULLETS)
MIXED_VAULT = _profile(_DE_BULLETS + _EN_BULLETS[:1] + [
    "I wrote the brand guidelines and trained the regional marketing teams on them."
], skills=["Adobe InDesign"])
PURE_DE_VAULT = _profile(_DE_BULLETS, skills=["Adobe InDesign"])


# ── the pair fact ────────────────────────────────────────────────────────────

def test_holds_other_language_reads_dominant_and_mixed_vaults():
    de = build_vault_index(DE_VAULT)
    assert de.dominant_language == "de"
    assert holds_other_language(de, "en") is True          # seam A's own trigger
    assert holds_other_language(de, "de") is False         # one language, same pair
    mixed = build_vault_index(MIXED_VAULT)
    assert mixed.dominant_language == "de"
    assert mixed.unit_language_shares.get("en", 0) >= OTHER_LANGUAGE_MIN_SHARE
    assert holds_other_language(mixed, "de") is True        # the blind case (L12)
    assert holds_other_language(mixed, None) is False       # fail open
    assert holds_other_language(mixed, "fr") is False
    # seam A's trigger alone: only short units (no long-unit vote), English blob
    short_en = build_vault_index({"personal_info": {"name": "A"}, "work_experience": [], "skills": [
        {"name": "Design of the brand"}, {"name": "Work with the team"}, {"name": "Care for the users"}]})
    assert short_en.dominant_language == "en" and short_en.unit_language_shares == {}
    assert holds_other_language(short_en, "de") is True


# ── keyword half (L10) ───────────────────────────────────────────────────────

_LEDGER = [{"concept": "Stripe payouts", "surface_forms": ["Stripe payouts"], "claimable": False,
            "status": "gap", "sources": ["keyword"], "fit_weight": 1.0, "evidence": ""}]
_EN_CV_TEXT = _norm("Reconciled Stripe payouts against invoices for 400 merchants.")


def test_keyword_on_a_cross_language_pair_leaves_group_one_for_the_neutral_row():
    cov = _keyword_coverage(
        _EN_CV_TEXT, ["Stripe payouts"], _LEDGER, vault_index=build_vault_index(DE_VAULT),
        document_language="en",
    )
    assert cov.present_unsupported == []
    assert cov.present_other_language == ["Stripe payouts"]
    checks = []
    _other_language_check(checks, cov)
    assert [(c.id, c.status, c.details_key) for c in checks] == [
        ("keyword-other-language", "not_applicable", "keyword-other-language")
    ]
    assert checks[0].details_params == {"count": 1, "terms": "Stripe payouts"}


def test_keyword_on_a_same_language_pair_still_flags_the_term():
    en_vault = _profile(["I built the merchant onboarding flow and owned its monitoring end to end."])
    cov = _keyword_coverage(
        _EN_CV_TEXT, ["Stripe payouts"], _LEDGER, vault_index=build_vault_index(en_vault),
        document_language="en",
    )
    assert cov.present_unsupported == ["Stripe payouts"]
    assert cov.present_other_language == []
    checks = []
    _other_language_check(checks, cov)
    assert checks == []


def test_keyword_without_document_language_is_unchanged():
    cov = _keyword_coverage(
        _EN_CV_TEXT, ["Stripe payouts"], _LEDGER, vault_index=build_vault_index(DE_VAULT),
    )
    assert cov.present_unsupported == ["Stripe payouts"]


# ── skill-chip half (L12) ────────────────────────────────────────────────────

_DE_CV = {"summary": "", "work_experience": [], "skills": ["Markenidentität"]}


def _skill_verdict(report):
    [c] = [c for c in report.claims if c.claim.text == "Markenidentität"]
    return c.verdict


@pytest.mark.asyncio
async def test_skill_chip_over_a_mixed_vault_is_unverifiable_not_unbacked():
    report = await audit_document("cv", MIXED_VAULT, tailored_data=_DE_CV, document_language="de")
    v = _skill_verdict(report)
    assert v.verdict == "unverifiable", v
    assert "another language" in v.detail


@pytest.mark.asyncio
async def test_skill_chip_over_a_one_language_vault_stays_unbacked():
    report = await audit_document("cv", PURE_DE_VAULT, tailored_data=_DE_CV, document_language="de")
    assert _skill_verdict(report).verdict == "unbacked"
    # and without a document language the mixed vault keeps today's verdict
    report = await audit_document("cv", MIXED_VAULT, tailored_data=_DE_CV)
    assert _skill_verdict(report).verdict == "unbacked"
