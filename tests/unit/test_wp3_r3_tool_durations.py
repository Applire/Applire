# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""R-3 (founder, 2026-11-04) — the TOOL DURATIONS fact and the derived-span drop.

Synthetic profiles only. Closed end dates keep the arithmetic independent of
today's date, except where a test says it is about "present".
"""
from __future__ import annotations

from datetime import date

import pytest

from applire.services.letter_v2 import (
    TOOL_DURATIONS_MAX,
    ToolDuration,
    _cv_entry_texts,
    render_tool_durations_block,
    tool_duration_facts,
)
from applire.services.prompt_view import (
    _drop_derived_spans,
    prompt_profile_view,
    span_hedges,
)
from applire.services.skill_enrichment import evidenced_positions


def _w(wid, company, start, end, *, tech=(), resp=()):
    d = {
        "id": wid, "company": company, "role": "Engineer",
        "start_date": start, "technologies": list(tech),
        "responsibilities": list(resp),
    }
    if end is None:
        d["is_current"] = True
    else:
        d["end_date"] = end
    return d


def _profile(work, skills=()):
    return {
        "personal_info": {"name": "Katrin Beispiel", "email": "kb@example.com"},
        "work_experience": list(work),
        "skills": [{"name": s, "category": "technical"} for s in skills],
    }


def _cv(**entries):
    return {"work_history": [
        {"id": wid, "role": "Engineer", "bullets": list(b)} for wid, b in entries.items()
    ]}


TWO = [
    _w("w1", "Alpha GmbH", "2014-09", "2019-03", tech=["Mulesoft"], resp=["Built things"]),
    _w("w2", "Beta AG", "2019-04", "2021-04", tech=["Mulesoft"], resp=["Built more"]),
]


def _one(facts, tool):
    got = [f for f in facts if f.tool == tool]
    assert len(got) == 1, facts
    return got[0]


# ── 1. union of ranges ────────────────────────────────────────────────────

def test_union_over_two_positions_is_the_summed_contiguous_span():
    f = _one(tool_duration_facts(_profile(TWO), None), "Mulesoft")
    # 2014-09 .. 2021-04 is ~6.6 years, contiguous
    assert 6.4 < f.years < 6.9
    assert [pid for pid, _ in f.positions] == ["w1", "w2"]


def test_tool_named_only_in_a_bullet_counts_that_position():
    work = [
        _w("w1", "Alpha GmbH", "2015-01", "2018-01", tech=["Java"], resp=["Used Airflow daily"]),
    ]
    f = _one(tool_duration_facts(_profile(work, skills=["Airflow"]), None), "Airflow")
    assert 2.9 < f.years < 3.1
    assert f.positions == (("w1", "Alpha GmbH"),)


def test_overlapping_ranges_are_not_double_counted():
    work = [
        _w("w1", "Alpha GmbH", "2015-01", "2019-01", tech=["Kafka"]),
        _w("w2", "Beta AG", "2017-01", "2019-01", tech=["Kafka"]),
    ]
    f = _one(tool_duration_facts(_profile(work), None), "Kafka")
    assert 3.9 < f.years < 4.1  # not 6


def test_tool_under_one_year_gets_no_fact():
    work = [_w("w1", "Alpha GmbH", "2020-01", "2020-07", tech=["Terraform"])]
    assert tool_duration_facts(_profile(work, skills=["Terraform"]), None) == []


def test_present_end_runs_to_today():
    start_year = date.today().year - 3
    work = [_w("w1", "Alpha GmbH", f"{start_year}-01", None, tech=["Rust"])]
    f = _one(tool_duration_facts(_profile(work), None), "Rust")
    assert f.years >= 2.9


# ── 2. since / positions ──────────────────────────────────────────────────

def test_since_is_the_earliest_start_month_year():
    f = _one(tool_duration_facts(_profile(list(reversed(TWO))), None), "Mulesoft")
    assert f.since == "09/2014"


def test_positions_label_the_counted_positions_only():
    work = TWO + [_w("w3", "Gamma KG", "2021-05", "2023-05", tech=["Cobol"])]
    f = _one(tool_duration_facts(_profile(work), None), "Mulesoft")
    assert [lbl for _, lbl in f.positions] == ["Alpha GmbH", "Beta AG"]


# ── 3. CV consistency ─────────────────────────────────────────────────────

def test_stateable_when_cv_shows_tool_under_every_counted_position():
    cv = _cv(w1=["Delivered Mulesoft APIs"], w2=["Operated Mulesoft"])
    f = _one(tool_duration_facts(_profile(TWO), cv), "Mulesoft")
    assert f.has_cv and f.cv_positions == ("w1", "w2")
    assert f.stateable is True
    block = render_tool_durations_block([f], "en")
    assert "May be stated as" in block
    assert "state NO duration" not in block


def test_not_stateable_when_cv_shows_tool_under_only_some_positions():
    cv = _cv(w1=["Delivered Mulesoft APIs"], w2=["Operated gateways"])
    f = _one(tool_duration_facts(_profile(TWO), cv), "Mulesoft")
    assert f.cv_positions == ("w1",)
    assert f.stateable is False
    block = render_tool_durations_block([f], "en")
    assert "state NO duration for Mulesoft" in block
    assert "only under Alpha GmbH" in block
    assert "May be stated as" not in block


def test_not_stateable_when_cv_shows_tool_under_none():
    cv = _cv(w1=["Other"], w2=["Other"])
    f = _one(tool_duration_facts(_profile(TWO), cv), "Mulesoft")
    assert f.cv_positions == () and f.stateable is False
    block = render_tool_durations_block([f], "en")
    assert "under none of them" in block
    assert "May be stated as" not in block


def test_no_cv_means_every_fact_is_stateable():
    facts = tool_duration_facts(_profile(TWO), None)
    assert facts and all(f.has_cv is False and f.stateable for f in facts)


def test_stateable_requires_positions():
    f = ToolDuration("X", 3.0, "01/2020", (), (), True)
    assert f.stateable is False


def test_cv_with_unknown_ids_shows_nothing():
    cv = _cv(zz=["Mulesoft"])
    f = _one(tool_duration_facts(_profile(TWO), cv), "Mulesoft")
    assert f.cv_positions == () and not f.stateable


def test_cv_entry_texts_cover_role_bullets_and_projects():
    cv = {"work_history": [
        {"id": "w1", "role": "Integration Architect", "bullets": ["alpha"],
         "projects": [{"name": "Projekt Fuchs", "bullets": ["beta"]}]},
        {"role": "no id"}, "junk",
    ]}
    out = _cv_entry_texts(cv)
    assert set(out) == {"w1"}
    for token in ("integration architect", "alpha", "fuchs", "beta"):
        assert token in out["w1"]
    assert _cv_entry_texts(None) == {}


# ── 4. rendering ──────────────────────────────────────────────────────────

def _fact(years, stateable=True):
    return ToolDuration(
        "Mulesoft", years, "01/2015", (("w1", "Alpha GmbH"),),
        ("w1",) if stateable else (), True,
    )


def test_block_carries_the_obligation_sentence():
    block = render_tool_durations_block([_fact(7.6)], "en")
    assert block.startswith("TOOL DURATIONS")
    assert "never another figure" in block
    assert "never rounded up" in block
    assert "A tool with no line here gets no duration." in block


def test_wordings_en_and_de_come_from_span_hedges():
    en = render_tool_durations_block([_fact(7.6)], "en")
    de = render_tool_durations_block([_fact(7.6)], "de")
    assert '"over 7 years" or "almost 8 years"' in en
    assert '"über 7 Jahre" or "knapp 8 Jahre"' in de
    assert "over 7 years" not in de


def test_unknown_or_missing_language_falls_back_to_english():
    assert '"over 7 years"' in render_tool_durations_block([_fact(7.6)], None)
    assert '"over 7 years"' in render_tool_durations_block([_fact(7.6)], "fr")
    assert '"über 7 Jahre"' in render_tool_durations_block([_fact(7.6)], " DE ")


def test_close_to_n_gets_only_the_over_wording():
    block = render_tool_durations_block([_fact(7.1)], "en")
    assert '"over 7 years"' in block and "almost" not in block


def test_empty_facts_render_empty_string():
    assert render_tool_durations_block([], "en") == ""


def test_facts_without_a_hedge_render_empty_string():
    assert render_tool_durations_block([_fact(0.5)], "en") == ""


def test_not_stateable_line_is_not_hedged_even_if_years_large():
    block = render_tool_durations_block([_fact(12.0, stateable=False)], "en")
    assert "over 12 years" not in block
    assert "state NO duration" in block


# ── 5. priority and cap ───────────────────────────────────────────────────

def test_priority_orders_matching_tools_first():
    work = [
        _w("w1", "Alpha GmbH", "2010-01", "2020-01", tech=["Oracle"]),
        _w("w2", "Beta AG", "2016-01", "2019-01", tech=["Docker"]),
    ]
    prof = _profile(work)
    assert [f.tool for f in tool_duration_facts(prof, None)] == ["Oracle", "Docker"]
    got = tool_duration_facts(prof, None, priority=["docker"])
    assert [f.tool for f in got] == ["Docker", "Oracle"]


def test_cap_at_tool_durations_max_keeps_priority_tool():
    tech = [f"Tool{i:02d}" for i in range(TOOL_DURATIONS_MAX + 5)]
    work = [_w("w1", "Alpha GmbH", "2010-01", "2020-01", tech=tech)]
    prof = _profile(work)
    assert len(tool_duration_facts(prof, None)) == TOOL_DURATIONS_MAX
    last = tech[-1]
    got = tool_duration_facts(prof, None, priority=[last])
    assert len(got) == TOOL_DURATIONS_MAX and got[0].tool == last


def test_longer_span_first_without_priority():
    work = [
        _w("w1", "Alpha GmbH", "2018-01", "2020-01", tech=["Short"]),
        _w("w2", "Beta AG", "2008-01", "2020-01", tech=["Long"]),
    ]
    assert [f.tool for f in tool_duration_facts(_profile(work), None)] == ["Long", "Short"]


# ── 6. fail-open ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    None, "x", [], {"work_experience": "nope"},
    {"work_experience": [{"start_date": 3}], "personal_info": 5},
])
def test_malformed_profile_returns_empty_without_raising(bad):
    assert tool_duration_facts(bad, None) == []


def test_language_skill_names_are_not_duration_tools():
    prof = _profile(TWO)
    prof["skills"].append({"name": "Deutsch", "category": "language"})
    assert all(f.tool != "Deutsch" for f in tool_duration_facts(prof, None))


# ── evidenced_positions ──────────────────────────────────────────────────

def _model(prof):
    from applire.schemas.profile import MasterProfileData
    return MasterProfileData.model_validate(prof)


def test_evidenced_positions_returns_years_and_rows():
    got = evidenced_positions(_model(_profile(TWO)), "Mulesoft")
    assert got is not None
    years, rows = got
    assert 6.4 < years < 6.9
    assert [e.id for e, _s, _e in rows] == ["w1", "w2"]
    assert all(s < e for _, s, e in rows)


def test_evidenced_positions_none_when_no_position_names_it():
    assert evidenced_positions(_model(_profile(TWO)), "Fortran") is None


def test_evidenced_positions_rejects_unknown_bound():
    with pytest.raises(ValueError):
        evidenced_positions(_model(_profile(TWO)), "Mulesoft", bound="ceiling")


def test_evidenced_positions_floor_skips_undated_end_current_runs_to_today():
    work = [_w("w1", "Alpha GmbH", f"{date.today().year - 2}-01", None, tech=["Go"])]
    got = evidenced_positions(_model(_profile(work)), "Go", bound="floor")
    assert got is not None and got[0] > 1.9


# ── 7. span_hedges / derived-span drop ────────────────────────────────────

def test_span_hedges_under_one_year_is_none():
    assert span_hedges(0.5) is None
    assert span_hedges(0.99) is None


def test_span_hedges_wordings():
    h = span_hedges(7.6)
    assert h == {
        "at_least": 7, "below": 8,
        "de": ["über 7 Jahre", "knapp 8 Jahre"],
        "en": ["over 7 years", "almost 8 years"],
    }
    assert span_hedges(1.2)["en"] == ["over one year"]
    assert span_hedges(7.1)["below"] is None


def _view():
    return {"skills": [
        {"name": "SAP CO", "years_experience": 8, "source": "computed"},
        {"name": "Excel", "years_experience": 6, "source": "llm_estimated"},
        {"name": "HGB", "years_experience": 9, "source": "transcribed"},
        {"name": "Plain"},
    ]}


def test_drop_derived_spans_removes_only_derived_years():
    out = _drop_derived_spans(_view())["skills"]
    by = {s["name"]: s for s in out}
    assert "years_experience" not in by["SAP CO"]
    assert "years_experience" not in by["Excel"]
    assert by["SAP CO"]["source"] == "computed"
    assert by["HGB"]["years_experience"] == 9
    assert by["Plain"] == {"name": "Plain"}


def test_drop_derived_spans_tolerates_a_view_without_skills():
    assert _drop_derived_spans({"x": 1}) == {"x": 1}


def test_prompt_profile_view_drop_end_to_end():
    prof = {
        "personal_info": {"name": "Katrin Beispiel", "email": "kb@example.com"},
        "work_experience": [],
        "skills": [
            {"name": "SAP CO", "category": "technical", "years_experience": 8,
             "source": "computed", "status": "confirmed"},
            {"name": "HGB", "category": "domain", "years_experience": 9,
             "source": "transcribed", "status": "confirmed"},
        ],
    }
    view = prompt_profile_view(prof, derived_spans="drop")
    by = {s["name"]: s for s in view["skills"]}
    assert "years_experience" not in by["SAP CO"]
    assert by["HGB"]["years_experience"] == 9
    assert "years_experience_derived" not in by["SAP CO"]
