# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SF-WRITE.113 (Sorbet WP-T; ADR-078 amended 2026-11-13): a start the person
states beats the computed span.

Hazelnut build 8 delivery: the writer was offered "Hygiene- und
Dokumentationsdisziplin — since 04/2017 … über 9 Jahre" (the union of the dated
positions whose own text names the skill) while the person had said "…
(Sauberraumbereich bei <employer> seit 2021)". The own-figure channel read "N
Jahre", never a start. These tests pin the delivery point: what the letter's
TOOL DURATIONS block and the CV's EXPERIENCE DURATIONS block OFFER the writer.
Synthetic profiles only (names invented).
"""
from __future__ import annotations

import pytest

from applire.services.cover_letter import _letter_critic_fn
from applire.services.cv_v2 import cv_durations_block
from applire.services.letter_v2 import (
    render_tool_durations_block,
    tool_duration_facts,
)
from applire.services.skill_enrichment import extract_stated_starts, stated_skill_starts

EMPLOYER = "Nordal Kunststoffwerk GmbH"
HYGIENE = "Hygiene- und Dokumentationsdisziplin"
# The captured answer's shape, names swapped: the reconcile split the start off
# the skill's bullet; the person's verbatim answer survives as a denial statement.
DENIAL = (
    "Nein, mit IFS oder BRC habe ich keine Erfahrung — das ist eine ehrliche Lücke. "
    "Hygiene- und Dokumentationsdisziplin kenne ich aber aus der Fertigung für "
    "Kosmetik-Verpackungen (Sauberraumbereich bei Nordal seit 2021) und aus zehn Jahren "
    "ISO-9001-Audit-Praxis — der Standard wäre neu, die Audit-Disziplin nicht."
)


def _profile(resp, *, skills=(HYGIENE,), denial=None, start="2017-04", tech=()):
    p = {
        "personal_info": {"name": "Jonas Beispiel", "email": "jb@example.com"},
        "work_experience": [{
            "id": "w1", "company": EMPLOYER, "role": "Produktionsleiter",
            "start_date": start, "end_date": None, "is_current": True,
            "responsibilities": list(resp), "technologies": list(tech),
        }],
        "skills": [{"name": s, "category": "domain", "status": "confirmed"} for s in skills],
    }
    if denial:
        p["metadata"] = {"denied_concepts": [{
            "concept": "IFS", "statement": denial, "source": "interview",
            "date": "2026-11-10", "denial_level": "direct", "probe_asked": False,
        }]}
    return p


def _fact(facts, tool):
    got = [f for f in facts if f.tool == tool]
    assert len(got) == 1, facts
    return got[0]


def _letter_line(block, tool):
    return next(ln for ln in block.splitlines() if ln.startswith(f"  - {tool} —"))


def _cv_line(block, tool):
    return next(ln for ln in block.splitlines() if ln.startswith(f"  - {tool}:"))


# ── the reader ────────────────────────────────────────────────────────────

def test_the_start_pattern_reads_year_month_and_month_name():
    got = [(y, m) for y, m, _r, _o in extract_stated_starts(
        "seit 2021. Since 04/2019, seit 3.2018, seit April 2020, since Sept. 2016"
    )]
    assert got == [(2021, None), (2019, 4), (2018, 3), (2020, 4), (2016, 9)]


def test_a_figure_or_a_decimal_is_no_start():
    assert extract_stated_starts("seit 2021,5 Mio. EUR Umsatz; seit Jahren; seit 20 Jahren") == []


# ── the captured shape: a start in the person's sentence, outside the window ──

def test_captured_shape_withholds_the_computed_span_in_the_letter_and_the_cv():
    p = _profile(
        [f"{HYGIENE} aus der Fertigung für Kosmetik-Verpackungen im Sauberraumbereich."],
        denial=DENIAL,
    )
    f = _fact(tool_duration_facts(p, None), HYGIENE)
    assert f.years > 9 and f.start_conflict and f.stated_start is None
    assert f.allowed_wordings("de") == []
    line = _letter_line(render_tool_durations_block([f], "de"), HYGIENE)
    assert "state NO duration" in line and "über 9" not in line and "knapp 10" not in line
    cv = _cv_line(cv_durations_block(p, "de"), HYGIENE)
    assert cv == f"  - {HYGIENE}: no duration."


def test_the_start_on_the_skills_own_bullet_at_the_sentence_end():
    # What a reconcile that keeps the start with its skill writes (German puts it last).
    p = _profile([f"{HYGIENE} aus der Fertigung im Sauberraumbereich bei Nordal seit 2021."])
    f = _fact(tool_duration_facts(p, None), HYGIENE)
    assert f.allowed_wordings("de") == [] and f.start_conflict
    assert "no duration" in _cv_line(cv_durations_block(p, "de"), HYGIENE)


# ── a tight start: offered as the person said it ───────────────────────────

def test_a_tight_start_replaces_the_hedge_with_the_persons_own_start():
    p = _profile(["SAP PP seit 2019 täglich für die Feinplanung genutzt."], skills=("SAP PP",))
    f = _fact(tool_duration_facts(p, None), "SAP PP")
    assert f.years > 9 and f.stated_start == "2019"
    assert f.allowed_wordings("de") == ["seit 2019"]
    assert f.allowed_wordings("en") == ["since 2019"]
    line = _letter_line(render_tool_durations_block([f], "de"), "SAP PP")
    assert "the candidate's own start" in line and '"seit 2019"' in line
    assert "über" not in line.split("never")[0] and "May be stated" not in line
    assert _cv_line(cv_durations_block(p, "en"), "SAP PP") == (
        '  - SAP PP: the candidate\'s own start — "since 2019", or no duration.'
    )


def test_a_tight_start_with_a_month():
    p = _profile(["Since April 2020 I run SAP PP for the line."], skills=("SAP PP",))
    assert _fact(tool_duration_facts(p, None), "SAP PP").allowed_wordings("en") == ["since 04/2020"]


def test_a_start_stands_without_a_computed_span():
    # LucaNet is named nowhere in a dated position's text, so R-3 computes nothing.
    p = _profile(["Seit 2023 Erstellung der konsolidierten Quartalszahlen in LucaNet."],
                 skills=("LucaNet",), start=None)
    f = _fact(tool_duration_facts(p, None), "LucaNet")
    assert f.years == 0.0 and f.allowed_wordings("de") == ["seit 2023"]


def test_the_persons_own_number_of_years_still_wins_over_a_start():
    p = _profile(["SAP PP seit 2019 im Einsatz; insgesamt acht Jahre SAP PP."], skills=("SAP PP",))
    f = _fact(tool_duration_facts(p, None), "SAP PP")
    assert f.stated_years == 8.0 and f.stated_start is None
    assert f.allowed_wordings("de") == ["8 Jahre"]


# ── whose start is it: R4-1's barriers ─────────────────────────────────────

def test_a_start_behind_a_list_barrier_belongs_to_the_other_skill():
    starts = stated_skill_starts(
        _profile(["SAP PP seit 2011, Kanban seit 2021."], skills=("SAP PP", "Kanban")),
        ["SAP PP", "Kanban"],
    )
    assert starts["sap pp"].year == 2011 and starts["sap pp"].tight
    assert starts["kanban"].year == 2021 and starts["kanban"].tight


def test_a_start_about_something_else_never_becomes_the_tools_start():
    # "Seit 2021 führe ich das Team, davor nutzte ich SAP PP": offering "seit 2021"
    # for SAP PP would be false; withholding the span is safe.
    p = _profile(["Seit 2021 führe ich das Team, davor nutzte ich SAP PP bei Nordal."],
                 skills=("SAP PP",))
    f = _fact(tool_duration_facts(p, None), "SAP PP")
    assert f.stated_start is None and f.start_conflict
    assert f.allowed_wordings("de") == []


def test_no_start_anywhere_leaves_r3_unchanged():
    p = _profile([f"{HYGIENE} im Sauberraumbereich."])
    f = _fact(tool_duration_facts(p, None), HYGIENE)
    assert not f.start_conflict and f.stated_start is None
    assert f.allowed_wordings("de")[0].startswith("über ")


# ── the two readers of allowed_wordings stay consistent ────────────────────

def test_the_critic_names_the_persons_own_start():
    p = _profile(["SAP PP seit 2019 täglich für die Feinplanung genutzt."], skills=("SAP PP",))
    facts = tool_duration_facts(p, None)

    class _Adv:
        concept = "SAP PP"
        letter_state = "über neun Jahre SAP PP"

    fn = _letter_critic_fn([_Adv()], facts, "de")
    found = fn({"body": {"paragraphs": ["Ich arbeite über neun Jahre SAP PP."]}})
    assert found and "the candidate's own start" in found[0].text and '"seit 2019"' in found[0].text


# ── MD-8 (adversarial F4/F5): a start that is no start as written ─────────

@pytest.mark.parametrize("bullet", [
    "SAP PP nutze ich nicht seit 2021, sondern erst seit 2023.",   # negated, then a later one
    "Mit SAP PP habe ich seit 2020 nicht mehr gearbeitet.",        # ended
    "SAP PP seit 2021 nicht mehr aktiv im Einsatz.",
    "SAP PP seit 2019-2021 im Projekt.",                          # a range
    "SAP PP seit 2019 bis 2021 im Projekt.",
    "SAP PP seit 1949 im Einsatz.",                               # before 1950
    "SAP PP seit 2005 im Einsatz.",                               # > 10 y before the first position (2017)
    "SAP PP seit 2099 im Einsatz.",                               # the future
])
def test_a_negated_ranged_or_implausible_start_withholds_the_span(bullet):
    p = _profile([bullet], skills=("SAP PP",))
    f = _fact(tool_duration_facts(p, None), "SAP PP")
    assert f.stated_start is None and f.start_conflict
    assert f.allowed_wordings("de") == [] and f.allowed_wordings("en") == []
    assert _cv_line(cv_durations_block(p, "de"), "SAP PP") == "  - SAP PP: no duration."


def test_a_start_within_the_margin_before_the_first_position_is_offered():
    p = _profile(["SAP PP seit 2008 im Einsatz."], skills=("SAP PP",))
    assert _fact(tool_duration_facts(p, None), "SAP PP").allowed_wordings("de") == ["seit 2008"]


def test_earliest_wins_never_picks_a_negated_start():
    starts = stated_skill_starts(
        _profile(["SAP PP nutze ich nicht seit 2021, sondern erst seit 2023."], skills=("SAP PP",)),
        ["SAP PP"],
    )
    assert not starts["sap pp"].usable


def test_a_later_ended_start_outranks_an_earlier_usable_one():
    # "seit 2019" alone would read as ongoing; "seit 2021 nicht mehr" says it ended.
    p = _profile(["SAP PP seit 2019 im Einsatz. Mit SAP PP seit 2021 nicht mehr gearbeitet."],
                 skills=("SAP PP",))
    f = _fact(tool_duration_facts(p, None), "SAP PP")
    assert f.allowed_wordings("de") == [] and f.start_conflict


def test_the_1950_floor_holds_without_a_dated_position():
    from datetime import date

    from applire.services.skill_enrichment import _plausible

    today = date(2026, 11, 13)
    assert not _plausible(1949, None, None, today) and _plausible(1950, None, None, today)
    assert not _plausible(2026, 12, None, today) and _plausible(2026, 11, None, today)
