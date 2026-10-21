# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Build-2 adversarial F-D (founder ruling R3-a) — the D2-1 "other language" row.

D2-1 (ADR-068 amended 2026-10-20) moves a ``present_unsupported`` keyword into a
neutral "not checked across languages" row when the vault may state evidence in
another language. As built, EVERY remaining unsupported term moved once the pair
fact held, including a term one of the candidate's own recorded denials covers
— so a CV that names a tool the candidate said they never used showed 0 red
(adversarial CV 5087a827: "Power BI", denial "Ich habe keine Erfahrung mit
Power BI und habe es nie eingesetzt."). And the row said "your profile states it
in another language", which the instrument never checked.

Ruling R3-a: (a) a term a recorded denial covers never leaves the red
population (the same cover the review card reads, ``denials_covering``);
(b) the row claims only that the term was not checked across languages.

Fixture rows are copied from the persisted adversarial rows (synthetic user):
the job's ledger entries for the two terms, the recorded denial verbatim, and
a German vault shaped like the adversary's.
"""
import pytest

from applire.services.ats_audit import _keyword_coverage, _norm, _other_language_check
from applire.services.oracle.matchers import build_vault_index

_DENIAL = {
    "date": "2026-10-21", "source": "testimony", "concept": "Power BI",
    "statement": "Ich habe keine Erfahrung mit Power BI und habe es nie eingesetzt.",
    "probe_asked": False, "denial_level": "direct",
}
_DE_VAULT = {
    "personal_info": {"name": "Ada Probe"},
    "professional_summary": {"de": (
        "Controllerin mit 9 Jahren Erfahrung im industriellen Mittelstand. Zuhause in "
        "Monats- und Jahresabschluss, Planung und Werkscontrolling."), "en": None},
    "work_experience": [{
        "id": "11111111-1111-1111-1111-111111111111",
        "company": "Weber Maschinenbau GmbH", "title": "Controllerin",
        "start_date": "2017-01", "end_date": "2026-06",
        "responsibilities": [
            "Ich habe den Monatsabschluss nach HGB für drei Werke verantwortet und erstellt.",
            "Ich habe die Herstellkostenrechnung für die Fertigung neu aufgebaut und betreut.",
            "Ich habe die Investitionsrechnung für neue Anlagen vorbereitet und präsentiert.",
        ],
    }],
    "skills": [{"name": "SAP CO", "status": "confirmed"}],
    "metadata": {"denied_concepts": [_DENIAL]},
}
_LEDGER = [
    {"concept": "Power BI", "status": "gap", "surface_forms": ["Power BI", "Microsoft Power BI"],
     "claimable": False, "sources": ["keyword"], "fit_weight": 1.0, "evidence": ""},
    {"concept": "IFRS", "status": "gap", "claimable": False, "sources": ["keyword"],
     "fit_weight": 1.0, "evidence": "",
     "surface_forms": ["IFRS-Umstellung", "Umstellung auf IFRS", "IFRS",
                       "International Financial Reporting Standards"]},
]
_EN_CV_TEXT = _norm(
    "Built Power BI dashboards for the plant management and led the IFRS group "
    "consolidation for three entities."
)


def _coverage(vault=_DE_VAULT, language="en"):
    return _keyword_coverage(
        _EN_CV_TEXT, ["Power BI", "IFRS"], _LEDGER,
        vault_index=build_vault_index(vault), document_language=language,
    )


def test_a_term_a_recorded_denial_covers_stays_red_on_a_cross_language_pair():
    cov = _coverage()
    assert cov.present_unsupported == ["Power BI"]
    assert cov.present_unsupported_denials == {"Power BI": ["Power BI"]}
    # the uncovered term still takes the neutral row (D2-1 unchanged for it)
    assert cov.present_other_language == ["IFRS"]
    assert set(cov.present_unsupported_matches) == {"Power BI"}


def test_without_the_denial_both_terms_take_the_neutral_row():
    vault = {**_DE_VAULT, "metadata": {}}
    cov = _coverage(vault)
    assert cov.present_unsupported == []
    assert cov.present_other_language == ["Power BI", "IFRS"]


def test_same_language_pair_keeps_both_red_and_the_denial_named():
    cov = _keyword_coverage(
        _norm("Power BI-Dashboards gebaut und die IFRS-Konsolidierung geleitet."),
        ["Power BI", "IFRS"], _LEDGER, vault_index=build_vault_index(_DE_VAULT),
        document_language="de",
    )
    assert cov.present_unsupported == ["Power BI", "IFRS"]
    assert cov.present_unsupported_denials == {"Power BI": ["Power BI"]}
    assert cov.present_other_language == []


def test_the_row_claims_only_that_the_term_was_not_checked_across_languages():
    cov = _coverage({**_DE_VAULT, "metadata": {}})
    checks = []
    _other_language_check(checks, cov)
    [row] = checks
    assert (row.id, row.status, row.details_key) == (
        "keyword-other-language", "not_applicable", "keyword-other-language")
    assert row.details_params == {"count": 2, "terms": "Power BI, IFRS"}
    text = row.details.lower()
    assert "not checked across languages" in text
    # the instrument never looked for the term in the other language: no claim
    # that the profile states, names or contains it
    for overclaim in ("states it", "profile states", "names it", "your profile has"):
        assert overclaim not in text, row.details


# ── skill-chip half (D2-1 L12, ADR-066: one rule for both halves) ───────────

_EN_UNITS = [
    "I wrote the brand guidelines and trained the regional marketing teams on them.",
    "I developed and owned the brand identity for the new product line of the company.",
]
_MIXED_VAULT = {
    **_DE_VAULT,
    "work_experience": [{
        **_DE_VAULT["work_experience"][0],
        "responsibilities": _DE_VAULT["work_experience"][0]["responsibilities"] + _EN_UNITS,
    }],
}


def _chip(report, text):
    [c] = [c for c in report.claims if c.claim.text == text]
    return c.verdict


@pytest.mark.asyncio
async def test_a_denied_skill_chip_over_a_mixed_vault_stays_unbacked():
    from applire.services.oracle import audit_document
    from applire.services.oracle.matchers import holds_other_language

    assert holds_other_language(build_vault_index(_MIXED_VAULT), "de") is True
    cv = {"summary": "", "work_experience": [], "skills": ["Power BI", "Konzernkonsolidierung"]}
    report = await audit_document("cv", _MIXED_VAULT, tailored_data=cv, document_language="de")
    assert _chip(report, "Power BI").verdict == "unbacked"
    # an uncovered literal miss keeps D2-1's neutral verdict
    assert _chip(report, "Konzernkonsolidierung").verdict == "unverifiable"
    no_denial = {**_MIXED_VAULT, "metadata": {}}
    report = await audit_document("cv", no_denial, tailored_data=cv, document_language="de")
    assert _chip(report, "Power BI").verdict == "unverifiable"
