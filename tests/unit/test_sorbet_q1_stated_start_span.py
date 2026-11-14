# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q1 clusters (a) and (e): a start the person states governs every
reader of a skill's span, not only the writer's offer (ADR-061 cl. 7 / ADR-052
amended 2026-11-14).

Platform-1 delivery run (Luna, both runs): the person said "… erstelle seit 2023
die konsolidierten Quartalszahlen in LucaNet …" in a role dated 02/2019. PR #841
(SF-WRITE.113) made the writer's TOOL DURATIONS offer "seit 2023", but the vault
STORED ``LucaNet years_experience 8, computed`` (#832:221), and the Oracle's
subject-span check accepted any duration up to the role-dated union, so an
invented "über 9 Jahre" against "seit 2021" was not flagged (#839:85).

One rule, three readers (ADR-066): the person's own N-years figure wins; else a
TIGHT usable start clips the counted ranges; a start that only shares a sentence
with the skill withholds the span. Readers covered here, one seam test each:
enrichment (stored value), ``stored_span_years`` (Health hub), the letter's
hedge view, the Oracle's subject-span check.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.oracle import verify_claim
from applire.services.skill_enrichment import (
    _union_years,
    enrich_skills_deterministic,
    stored_span_years,
)

TODAY = date.today()
START = TODAY.year - 3  # "seit <START>": three-and-a-bit years ago


def _ago(years: float) -> str:
    months = int(round(years * 12))
    y, m = divmod(TODAY.year * 12 + (TODAY.month - 1) - months, 12)
    return f"{y:04d}-{m + 1:02d}"


def _katrin(**extra) -> dict:
    """Synthetic: a controller since ~7.8 years who says LucaNet only "seit <START>"."""
    p = {
        "professional_summary": {"de": "Controllerin mit Erfahrung im Mittelstand."},
        "work_experience": [
            {"id": "w-sp", "company": "Schwarzwald Präzision GmbH", "role": "Financial Controller",
             "start_date": _ago(7.8), "is_current": True,
             "responsibilities": [
                 f"Erstellung der konsolidierten Quartalszahlen in LucaNet seit {START}.",
                 "Werkscontrolling für zwei Produktionsstandorte.",
                 "Management-Reporting in Power BI umgesetzt.",
             ],
             "technologies": ["LucaNet", "Power BI"]},
        ],
        "skills": [
            {"name": "LucaNet", "category": "technical"},
            {"name": "Power BI", "category": "technical"},
            {"name": "Werkscontrolling", "category": "domain"},
        ],
    }
    p.update(extra)
    return p


def _stefan() -> dict:
    """Synthetic: a production lead since ~9.6 years; the hygiene sentence names a
    start ten words away from the skill (not tight), the ISO sentence an own figure."""
    return {
        "work_experience": [
            {"id": "w-we", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
             "start_date": _ago(9.6), "is_current": True,
             "responsibilities": [
                 "Hygiene- und Dokumentationsdisziplin in der Fertigung für Kosmetik-Verpackungen "
                 f"im Sauberraumbereich bei Weberit seit {TODAY.year - 5}.",
                 f"Seit {TODAY.year - 5} zehn Jahre ISO 9001 Audit-Praxis gesammelt.",
                 "Begleitung der jährlichen ISO-9001-Audits als Bereichsverantwortlicher.",
                 "Shopfloor-Management eingeführt.",
             ]},
        ],
        "skills": [
            {"name": "Hygiene- und Dokumentationsdisziplin", "category": "domain"},
            {"name": "ISO 9001", "category": "domain"},
            {"name": "Shopfloor-Management", "category": "domain"},
        ],
    }


def _enriched(p: dict) -> dict[str, tuple]:
    out = enrich_skills_deterministic(MasterProfileData.model_validate(p))
    return {s.name: (s.years_experience, s.source) for s in out.skills}


def _years_since(year: int) -> int:
    return int(_union_years([(date(year, 1, 1), TODAY)]))


# ── (a) the stored value ─────────────────────────────────────────────────────


def test_a_tight_stated_start_governs_the_stored_span():
    got = _enriched(_katrin())
    # Before: the role span, rounded — 8 for a role ~7.8 years old.
    assert got["LucaNet"] == (_years_since(START), "computed")
    assert got["LucaNet"][0] < 7


def test_a_a_skill_without_a_start_keeps_the_role_span():
    got = _enriched(_katrin())
    assert got["Power BI"] == (8, "computed")
    assert got["Werkscontrolling"] == (8, "computed")


def test_a_a_start_that_only_shares_the_sentence_withholds_the_stored_span():
    got = _enriched(_stefan())
    # Before: (10, "computed") — the role tenure for a skill the person dates 2021.
    assert got["Hygiene- und Dokumentationsdisziplin"] == (None, "transcribed")


def test_a_the_persons_own_figure_outranks_a_start_in_the_same_sentence():
    got = _enriched(_stefan())
    # "zehn Jahre ISO 9001" is the person's figure (R4-1): no start governs,
    # so the stored value is the unchanged role span.
    assert got["ISO 9001"] == (10, "computed")
    assert got["Shopfloor-Management"] == (10, "computed")


def test_a_enrichment_is_idempotent_over_its_own_output():
    once = enrich_skills_deterministic(MasterProfileData.model_validate(_stefan()))
    twice = enrich_skills_deterministic(once)
    assert [(s.name, s.years_experience, s.source) for s in once.skills] == [
        (s.name, s.years_experience, s.source) for s in twice.skills
    ]


def test_a_a_transcribed_span_still_outranks_everything():
    p = _katrin()
    p["skills"][0] = {"name": "LucaNet", "category": "technical", "years_experience": 2,
                      "source": "transcribed"}
    assert _enriched(p)["LucaNet"] == (2, "transcribed")


def test_a_stored_span_years_agrees_with_what_enrichment_stores():
    """The Health hub's laundered-span check compares against this (ADR-066)."""
    for p, name in ((_katrin(), "LucaNet"), (_katrin(), "Power BI"),
                    (_stefan(), "Hygiene- und Dokumentationsdisziplin")):
        model = MasterProfileData.model_validate(p)
        assert stored_span_years(model, name) == _enriched(p)[name][0], name


def test_a_the_letter_hedge_view_counts_from_the_start_or_withholds():
    from applire.services.prompt_view import prompt_profile_view

    k = enrich_skills_deterministic(MasterProfileData.model_validate(_katrin())).model_dump(mode="json")
    view = prompt_profile_view(k, derived_spans="hedge")
    luca = next(s for s in view["skills"] if s["name"] == "LucaNet")
    assert luca["years_experience_derived"]["at_least"] == _years_since(START)
    s = _stefan()
    s["skills"][0].update({"years_experience": 10, "source": "computed"})  # a stale stored value
    view = prompt_profile_view(s, derived_spans="hedge")
    hyg = next(x for x in view["skills"] if x["name"].startswith("Hygiene"))
    assert "years_experience_derived" not in hyg and "years_experience" not in hyg


# ── (e) the Oracle's subject-span check ──────────────────────────────────────


def _v(text: str, profile: dict):
    return asyncio.run(verify_claim(text, profile))


_SPAN_KEYS = {"subjectTenureExceeds", "subjectTenureExceedsStart", "subjectTenureStartUnclear"}


def _not_span_flagged(v) -> bool:
    """Neither accused nor left open BY THE SUBJECT-SPAN CHECK (a generic
    ``noCloseEvidence`` on a short probe sentence is another checker's verdict)."""
    return v.verdict != "unbacked" and v.detail_key not in _SPAN_KEYS


def test_e_a_duration_longer_than_the_persons_own_start_is_unbacked():
    v = _v("Über sieben Jahre Erfahrung mit LucaNet in der Konsolidierung.", _katrin())
    # Before: grounded — the role named LucaNet for ~7.8 years.
    assert (v.verdict, v.detail_key) == ("unbacked", "subjectTenureExceedsStart"), v
    assert v.detail_params["start"] == str(START)
    assert v.detail_params["skill"] == "LucaNet"


@pytest.mark.parametrize("text", [
    f"Seit {START} erstelle ich die konsolidierten Quartalszahlen in LucaNet.",
    "Über drei Jahre Erfahrung mit LucaNet.",
    "Über sieben Jahre Erfahrung mit Power BI im Reporting.",
])
def test_e_a_duration_within_the_start_or_without_one_is_not_flagged(text):
    assert _not_span_flagged(_v(text, _katrin())), text


def test_e_a_duration_beyond_a_sentence_only_start_is_left_open():
    v = _v("Über neun Jahre Hygiene- und Dokumentationsdisziplin im Sauberraum.", _stefan())
    # Before: grounded by the ~9.6-year role union.
    assert (v.verdict, v.detail_key) == ("unverifiable", "subjectTenureStartUnclear"), v
    assert v.detail_params["start"] == str(TODAY.year - 5)


def test_e_a_duration_within_a_sentence_only_start_stays_unflagged():
    v = _v("Über fünf Jahre Hygiene- und Dokumentationsdisziplin im Sauberraum.", _stefan())
    assert _not_span_flagged(v), v


def test_e_the_own_figure_escape_still_wins():
    v = _v("Zehn Jahre ISO 9001 Audit-Praxis in der Serienfertigung.", _stefan())
    assert _not_span_flagged(v), v


def test_e_with_an_own_figure_no_start_governs_the_oracles_span():
    """R4-1 precedence in the index: a skill the person gives an own N-years figure
    for is checked against the role span, never against a start in that sentence."""
    from applire.services.oracle.matchers.vault import build_vault_index

    idx = build_vault_index(_stefan())
    assert "ISO 9001" not in idx.skill_starts
    assert "Hygiene- und Dokumentationsdisziplin" in idx.skill_starts
    assert idx.skill_spans["ISO 9001"][0] > 9
