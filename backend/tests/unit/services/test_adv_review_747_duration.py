# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strawberry build 2 adversarial review — #747 derived durations (Oracle + letter view).

Every test here is EXPECTED TO FAIL on the reviewed tree (`a96007bb`); each one
pins one finding of `Documents/Runs/Strawberry/build-2/adv-review/findings.md`.
Deterministic, no provider: `verify_claim` runs the Oracle's deterministic chain,
`prompt_profile_view(..., derived_spans="hedge")` is the letter's vault view.

A false accusation is a harm in itself (the candidate is told their own true
statement is unbacked); a false pass lets a flat derived count reach the letter.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from applire.services.oracle import verify_claim
from applire.services.prompt_view import prompt_profile_view


def _ym(months_ago: int) -> str:
    today = date.today()
    y, m = divmod(today.year * 12 + (today.month - 1) - months_ago, 12)
    return f"{y:04d}-{m + 1:02d}"


def _sap_profile(**extra) -> dict:
    """One current role naming SAP CO, started 7.6 years ago (the #747 probe shape),
    after an earlier role that does not name it."""
    p = {
        "professional_summary": {"de": "Controllerin im Mittelstand."},
        "work_experience": [
            {"id": "w-sp", "company": "Schwarzwald Präzision GmbH", "role": "Financial Controller",
             "start_date": _ym(91), "is_current": True,
             "responsibilities": ["Key-Userin SAP CO/FI"], "technologies": ["SAP CO", "SAP FI"]},
            # An earlier role that never names SAP: the career envelope (#469
            # ceiling) is 10 years, so only the per-subject check can decide.
            {"id": "w-br", "company": "Brauner & Söhne GmbH", "role": "Junior Controllerin",
             "start_date": _ym(121), "end_date": _ym(92),
             "responsibilities": ["Mitarbeit im Monatsabschluss"]},
        ],
        "skills": [
            {"name": "SAP CO", "category": "technical", "years_experience": 8, "source": "computed"},
            {"name": "SAP FI", "category": "technical", "years_experience": 8, "source": "computed"},
        ],
    }
    p.update(extra)
    return p


def _verdict(text: str, profile: dict):
    return asyncio.run(verify_claim(text, profile))


# ── 1. a parenthesised hedge is read as a flat count ──────────────────────────
def test_adv_review_1_parenthesised_knapp_hedge_is_not_accused():
    """`_required_span` only recognises a hedge preceded by a SPACE or at the very
    start of the claim. "(knapp acht Jahre)" — the CV skills-line shape — reads as a
    flat "acht Jahre" and the exact wording ruling T-1 prescribes for 7.6 years is
    graded `unbacked` (audit.py `_required_span`, `before.endswith(" " + h)`)."""
    v = _verdict("SAP CO (knapp acht Jahre) und SAP FI.", _sap_profile())
    assert v.verdict != "unbacked", v.detail


# ── 2. honest hedge variants missing from the closed table ────────────────────
@pytest.mark.parametrize("text", [
    "Seit nicht ganz acht Jahren arbeite ich mit SAP CO.",
    "Seit annähernd acht Jahren arbeite ich mit SAP CO.",
    "Seit zirka acht Jahren arbeite ich mit SAP CO.",
    "Just under eight years of SAP CO experience.",
    "Close to eight years of SAP CO experience.",
])
def test_adv_review_2_downward_hedge_variants_are_not_accused(text):
    """Each phrase says LESS than eight years about a 7.6-year span — the same
    statement as "knapp acht" — but is missing from `_HEDGE_ALMOST/_HEDGE_ABOUT`
    and is therefore read as a flat eight and accused."""
    v = _verdict(text, _sap_profile())
    assert v.verdict != "unbacked", (text, v.detail)


# ── 3. the end month / end year is read as its FIRST day ──────────────────────
def test_adv_review_3a_inclusive_end_month_true_count_is_not_accused():
    """Jan 2016 – Dec 2023 is eight years by the candidate's own dates. The span
    instrument stores "2023-12" as 2023-12-01 (7.92 y) and the 1/12 tolerance does
    not cover it: "Acht Jahre SAP CO" is accused although it is true."""
    profile = _sap_profile(work_experience=[
        {"id": "w-a", "company": "A GmbH", "role": "Controllerin",
         "start_date": "2016-01", "end_date": "2023-12", "technologies": ["SAP CO"]},
    ])
    v = _verdict("Acht Jahre SAP CO bei der A GmbH.", profile)
    assert v.verdict != "unbacked", v.detail


def test_adv_review_3b_year_granular_dates_do_not_accuse_a_hedge():
    """"2016" – "2023" may be up to eight years; read as 2016-01-01 → 2023-01-01
    (7.0 y), so even the hedge "knapp acht" (needs 7.5) is accused."""
    profile = _sap_profile(work_experience=[
        {"id": "w-a", "company": "A GmbH", "role": "Controllerin",
         "start_date": "2016", "end_date": "2023", "technologies": ["SAP CO"]},
    ])
    v = _verdict("Knapp acht Jahre SAP CO bei der A GmbH.", profile)
    assert v.verdict != "unbacked", v.detail


def test_adv_review_3c_consecutive_roles_lose_a_month_per_boundary():
    """Four back-to-back roles naming SAP CO since 96 months ago (true span 8.0 y).
    Each boundary loses the end month (end read as day 1), the union is 7.77 y and
    a TRUE "seit acht Jahren" is accused — the tolerance is one month for the
    whole union, not one per role."""
    roles = [
        (96, 79), (78, 55), (54, 31),
    ]
    work = [
        {"id": f"w{i}", "company": f"Firma {i}", "role": "Controllerin",
         "start_date": _ym(s), "end_date": _ym(e), "technologies": ["SAP CO"]}
        for i, (s, e) in enumerate(roles)
    ]
    work.append({"id": "w9", "company": "Firma 9", "role": "Controllerin",
                 "start_date": _ym(30), "is_current": True, "technologies": ["SAP CO"]})
    v = _verdict("Seit acht Jahren arbeite ich mit SAP CO.", _sap_profile(work_experience=work))
    assert v.verdict != "unbacked", v.detail


# ── 4. the "candidate's own words" escapes are subject-blind ──────────────────
def test_adv_review_4a_a_career_total_does_not_launder_a_skill_count():
    """The summary states the CAREER total ("9 Jahren Erfahrung"). `stated_tenure_years`
    is one vault-wide set of numbers, so "Seit neun Jahren … SAP CO" (span 7.6 y)
    escapes because the number 9 appears anywhere in the vault. With a summary of
    12 years the same sentence IS flagged — the pass depends on a coincidence of
    digits, the #214 category error in the escape direction."""
    profile = _sap_profile(professional_summary={"de": "Controllerin mit 9 Jahren Erfahrung im Mittelstand."})
    v = _verdict("Seit neun Jahren arbeite ich mit SAP CO.", profile)
    assert v.verdict == "unbacked", v


def test_adv_review_4b_another_skills_transcribed_span_does_not_launder():
    """Excel carries a transcribed 10 years; naming Excel in the same window lets a
    flat nine years of SAP CO (span 7.6 y) through — `any(... for name in subjects)`."""
    profile = _sap_profile()
    profile["work_experience"][1]["technologies"] = ["Excel"]  # a dated role names Excel
    profile["skills"].append(
        {"name": "Excel", "category": "technical", "years_experience": 10, "source": "transcribed"}
    )
    v = _verdict("Seit neun Jahren arbeite ich mit SAP CO und Excel.", profile)
    assert v.verdict == "unbacked", v


# ── 5. the letter view hands a ceiling as a floor for an open, non-current role ─
def test_adv_review_5_hedge_view_never_offers_a_floor_from_an_unknown_end():
    """A role with no end date that is explicitly NOT current: the span instrument
    reads the missing end as "today" (the Oracle's deliberately PERMISSIVE upper
    bound). The letter view then hands the writer that ceiling as the floor —
    "über 16 Jahre / knapp 17 Jahre" — although the candidate's next role started
    in 2014. Ruling T-1: the view hands the FLOOR; T-1b: a span with no checkable
    floor is dropped, not hedged."""
    profile = {
        "work_experience": [
            {"id": "w-a", "company": "A GmbH", "role": "Controllerin",
             "start_date": "2010-01", "end_date": None, "is_current": False,
             "technologies": ["SAP CO"]},
            {"id": "w-b", "company": "B GmbH", "role": "Leiterin Controlling",
             "start_date": "2014-01", "is_current": True, "responsibilities": ["Konzernreporting"]},
        ],
        "skills": [
            {"name": "SAP CO", "category": "technical", "years_experience": 17, "source": "computed"},
        ],
    }
    (skill,) = prompt_profile_view(profile, derived_spans="hedge")["skills"]
    derived = skill.get("years_experience_derived") or {}
    assert derived.get("at_least", 0) <= 4, derived
