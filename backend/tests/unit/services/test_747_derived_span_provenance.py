# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#747 — a derived skill duration never reaches a prompt as a stated one.

Attributed from the captured 2026-09-19 log and a replay (WP-T report): skill
enrichment computed ``SAP CO: 8`` (02/2019 → 7.6 y, rounded) on the INCOMING
second import, ``prompt_incoming_view`` stripped its ``source: computed`` but
kept the number, the reconciler carried it as a stated span and the applier
stamped it ``transcribed``. Replay n=5 per rendering: 5/5 laundered as
rendered, 0/5 with the derived span stripped.

Pinned here: the incoming view (unit + the seam through
``build_reconcile_prompt``), the letter's hedge view (ruling T-1/T-1b), the
shared span instrument, and the enrichment precedence for an extractor-read
span.
"""
from __future__ import annotations

import json
from datetime import date

from applire.prompts.reconcile import build_reconcile_prompt
from applire.schemas.profile import MasterProfileData
from applire.services.prompt_view import prompt_incoming_view, prompt_profile_view
from applire.services.skill_enrichment import (
    enrich_skills_deterministic,
    evidenced_span_years,
)


def _start_years_ago(years: float) -> str:
    today = date.today()
    months = int(round(years * 12))
    y, m = divmod(today.year * 12 + (today.month - 1) - months, 12)
    return f"{y:04d}-{m + 1:02d}"


SAP_START = _start_years_ago(7.6)  # the probe's shape: 7.6 years, rounds to 8

INCOMING = {
    "work_experience": [
        {
            "id": "w-schwarzwald",
            "company": "Schwarzwald Präzision GmbH",
            "role": "Financial Controller",
            "start_date": SAP_START,
            "is_current": True,
            "responsibilities": ["Key-Userin SAP CO/FI"],
            "technologies": ["SAP CO", "SAP FI"],
        }
    ],
    "skills": [
        {"name": "SAP CO", "category": "technical", "years_experience": None},
        {"name": "HGB", "category": "domain", "years_experience": 15},  # extractor-read
    ],
}


def _enriched_incoming() -> MasterProfileData:
    return enrich_skills_deterministic(MasterProfileData.model_validate(INCOMING))


def test_enrichment_computes_the_probe_span_and_labels_it_computed():
    skills = {s.name: s for s in _enriched_incoming().skills}
    assert skills["SAP CO"].years_experience == 8
    assert skills["SAP CO"].source == "computed"


def test_extractor_read_span_is_transcribed_and_not_overwritten_by_dates():
    # HGB is named by no dated role here, so it stays as read; and a span the
    # extractor read keeps its number even when a role DOES name the skill.
    data = json.loads(json.dumps(INCOMING))
    data["skills"].append({"name": "SAP FI", "category": "technical", "years_experience": 3})
    skills = {s.name: s for s in enrich_skills_deterministic(
        MasterProfileData.model_validate(data)).skills}
    assert (skills["SAP FI"].years_experience, skills["SAP FI"].source) == (3, "transcribed")
    assert (skills["HGB"].years_experience, skills["HGB"].source) == (15, "transcribed")


def test_incoming_view_drops_a_derived_span_and_keeps_a_stated_one():
    view = prompt_incoming_view(_enriched_incoming().model_dump(mode="json"))
    skills = {s["name"]: s for s in view["skills"]}
    assert "years_experience" not in skills["SAP CO"]
    assert skills["HGB"]["years_experience"] == 15


def test_incoming_view_drops_an_estimated_span():
    view = prompt_incoming_view({"skills": [
        {"name": "Excel", "years_experience": 6, "source": "llm_estimated"}]})
    assert view["skills"] == [{"name": "Excel"}]


def test_seam_reconcile_prompt_never_shows_the_computed_span():
    existing = MasterProfileData.model_validate({"skills": []})
    prompt = build_reconcile_prompt(existing, _enriched_incoming(), "cv_upload")
    new_info = prompt[prompt.index("NEW INFORMATION"):]
    sap = new_info[new_info.index('"name": "SAP CO"'):]
    sap = sap[: sap.index("}")]
    assert "years_experience" not in sap, sap
    hgb = new_info[new_info.index('"name": "HGB"'):]
    assert '"years_experience": 15' in hgb[: hgb.index("}")]


def test_evidenced_span_is_fractional_and_names_its_roles():
    years, orgs = evidenced_span_years(_enriched_incoming(), "SAP CO", bound="floor")
    assert 7.4 < years < 7.8
    assert orgs == ["Schwarzwald Präzision GmbH"]
    assert evidenced_span_years(_enriched_incoming(), "Kubernetes", bound="upper") is None


def test_letter_view_hedges_a_computed_span_never_rounding_up():
    view = prompt_profile_view(_enriched_incoming().model_dump(mode="json"),
                               derived_spans="hedge")
    sap = {s["name"]: s for s in view["skills"]}["SAP CO"]
    assert "years_experience" not in sap
    derived = sap["years_experience_derived"]
    assert (derived["at_least"], derived["below"]) == (7, 8)
    assert "über 7 Jahre" in derived["say"] and "knapp 8 Jahre" in derived["say"]
    assert "8 Jahre\"" not in derived["say"].replace("knapp 8 Jahre\"", "")


def test_letter_view_drops_estimated_keeps_transcribed_and_default_is_unchanged():
    profile = {"skills": [
        {"name": "Excel", "years_experience": 6, "source": "llm_estimated"},
        {"name": "HGB", "years_experience": 9, "source": "transcribed"},
    ]}
    hedged = {s["name"]: s for s in prompt_profile_view(profile, derived_spans="hedge")["skills"]}
    assert "years_experience" not in hedged["Excel"]
    assert "years_experience_derived" not in hedged["Excel"]
    assert hedged["HGB"]["years_experience"] == 9
    plain = {s["name"]: s for s in prompt_profile_view(profile)["skills"]}
    assert plain["Excel"]["years_experience"] == 6


def test_letter_view_offers_no_almost_hedge_below_half_a_year():
    profile = {
        "work_experience": [{"id": "w", "company": "A", "role": "R",
                             "start_date": _start_years_ago(7.1), "is_current": True,
                             "technologies": ["SAP CO"]}],
        "skills": [{"name": "SAP CO", "years_experience": 7, "source": "computed"}],
    }
    derived = prompt_profile_view(profile, derived_spans="hedge")["skills"][0][
        "years_experience_derived"]
    assert derived["at_least"] == 7 and "below" not in derived
    assert "knapp" not in derived["say"]


# ── adversarial findings 3 + 5 (2026-10-07): one instrument, two bounds ──────
def _closed_role_profile(start: str, end: str | None, is_current: bool | None) -> MasterProfileData:
    return MasterProfileData.model_validate({
        "work_experience": [{"id": "w", "company": "A GmbH", "role": "R", "start_date": start,
                             "end_date": end, "is_current": is_current,
                             "technologies": ["SAP CO"]}],
        "skills": [{"name": "SAP CO", "category": "technical"}],
    })


def test_upper_bound_reads_an_end_month_and_an_end_year_inclusively():
    month = _closed_role_profile("2016-01", "2023-12", False)
    year = _closed_role_profile("2016", "2023", False)
    assert evidenced_span_years(month, "SAP CO", bound="upper")[0] >= 8.0
    assert evidenced_span_years(year, "SAP CO", bound="upper")[0] >= 8.0
    # The floor keeps the stored arithmetic: an end read as its first day.
    assert evidenced_span_years(month, "SAP CO", bound="floor")[0] < 8.0
    assert evidenced_span_years(year, "SAP CO", bound="floor")[0] < 7.1


def test_floor_drops_an_open_end_that_is_not_current_upper_keeps_it():
    p = _closed_role_profile("2010-01", None, False)
    assert evidenced_span_years(p, "SAP CO", bound="floor") is None
    assert evidenced_span_years(p, "SAP CO", bound="upper")[0] > 15
    assert evidenced_span_years(_closed_role_profile("2010-01", None, None), "SAP CO",
                                bound="floor") is None
    assert evidenced_span_years(_closed_role_profile("2019-01", None, True), "SAP CO",
                                bound="floor") is not None


def test_bound_is_required_and_closed():
    import pytest as _pt
    with _pt.raises(TypeError):
        evidenced_span_years(_enriched_incoming(), "SAP CO")  # type: ignore[call-arg]
    with _pt.raises(ValueError):
        evidenced_span_years(_enriched_incoming(), "SAP CO", bound="today")
