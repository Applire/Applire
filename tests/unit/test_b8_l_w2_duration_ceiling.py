# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RULING W-2 (founder 2026-11-10, Hazelnut build 8; amends R-3 for the letter):
the person's own career figure is the ceiling. A computed tool hedge whose floor
exceeds the largest duration the candidate states for their career gets NO duration
in the letter (ux-audit-4 Felix: "over 9 years PostgreSQL" beside his own "8 years
of experience"); a hedge below it stays; no own figure = no ceiling. The candidate's
own per-tool figure (R4-1) is never capped. Both readers of
``ToolDuration.allowed_wordings`` follow: the TOOL DURATIONS line and the critic's
numeric finding. Synthetic profiles only.
"""
from __future__ import annotations

from applire.services.cover_letter import _letter_critic_fn
from applire.services.letter_v2 import render_tool_durations_block, tool_duration_facts


def _w(wid, company, start, end, tech):
    return {"id": wid, "company": company, "role": "Engineer", "start_date": start,
            "end_date": end, "technologies": list(tech)}


WORK = [
    _w("w1", "Alpha GmbH", "2015-01", "2017-06", ["PostgreSQL", "Python"]),
    _w("w2", "Beta AG", "2017-07", "2021-02", ["PostgreSQL"]),
    _w("w3", "Gamma GmbH", "2021-03", "2025-12", ["PostgreSQL", "Django"]),
]


def _profile(summary="Engineer with 8 years of experience in logistics software."):
    return {
        "personal_info": {"name": "Felix Beispiel", "email": "fb@example.com"},
        "professional_summary": {"en": summary, "de": None},
        "work_experience": WORK,
        "skills": [{"name": s, "category": "technical"} for s in ("PostgreSQL", "Django", "Python")],
    }


def _fact(facts, tool):
    return next(f for f in facts if f.tool == tool)


def _line(block, tool):
    return next(ln for ln in block.splitlines() if ln.startswith(f"  - {tool} —"))


def test_a_hedge_over_the_ceiling_gets_no_duration():
    facts = tool_duration_facts(_profile(), None, ceiling_years=8)
    pg = _fact(facts, "PostgreSQL")
    assert pg.years >= 10 and pg.over_ceiling
    assert pg.allowed_wordings("en") == [] and pg.allowed_wordings("de") == []
    line = _line(render_tool_durations_block(facts, "en"), "PostgreSQL")
    assert "longer than the 8 years the candidate states" in line and "state NO duration" in line


def test_a_hedge_below_the_ceiling_stays():
    django = _fact(tool_duration_facts(_profile(), None, ceiling_years=8), "Django")
    assert not django.over_ceiling
    assert django.allowed_wordings("en") == ["over 4 years", "almost 5 years"]


def test_at_the_ceiling_only_the_over_wording_stays():
    """8.6 computed years beside the candidate's own 8: "over 8" stays, "almost 9"
    would still read as more than their own figure."""
    from applire.services.letter_v2 import ToolDuration

    t = ToolDuration(tool="X", years=8.6, since="01/2017", positions=(("a", "A"),),
                     cv_positions=("a",), has_cv=True, ceiling_years=8)
    assert t.allowed_wordings("de") == ["über 8 Jahre"]


def test_no_ceiling_leaves_r3_unchanged():
    pg = _fact(tool_duration_facts(_profile(), None), "PostgreSQL")
    assert pg.ceiling_years is None and pg.allowed_wordings("en")[0] == f"over {int(pg.years)} years"


def test_the_own_tool_figure_is_never_capped():
    facts = tool_duration_facts(_profile("Engineer with 11 years of PostgreSQL operations."), None,
                                ceiling_years=8)
    pg = _fact(facts, "PostgreSQL")
    assert pg.allowed_wordings("en") == ["11 years"] and not pg.over_ceiling


def test_every_fact_carries_the_ceiling_even_a_figure_without_a_span():
    """A tool the candidate states but no dated position counts still carries it."""
    p = _profile("Engineer with 8 years of experience; 3 years of Kafka on the side.")
    p["skills"].append({"name": "Kafka", "category": "technical"})
    facts = tool_duration_facts(p, None, ceiling_years=8)
    kafka = _fact(facts, "Kafka")
    assert kafka.years == 0.0 and kafka.stated_years == 3
    assert {f.ceiling_years for f in facts} == {8}


class _Adv:
    def __init__(self, concept, state):
        self.concept, self.letter_state = concept, state


def test_the_critic_finding_follows_the_ceiling():
    facts = tool_duration_facts(_profile(), None, ceiling_years=8)
    adv = _Adv("Years of PostgreSQL experience", "I have run PostgreSQL for over 10 years")
    draft = {"body": {"paragraphs": [f"Intro. {adv.letter_state}. Close."]}}
    (finding,) = _letter_critic_fn([adv], facts, "en")(draft)
    assert finding.text.endswith("For PostgreSQL state no duration.")
