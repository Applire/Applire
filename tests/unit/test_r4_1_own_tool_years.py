# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""R4-1 (founder, 2026-11-07, amends R-3): "Eigene Zahl schlägt Rechnung".

ux-audit-4 Felix: his profile summary says "8 years of experience designing and
operating Python services", his dated positions referencing Python span ~10.6
years, and the TOOL DURATIONS line licensed "over 10 years". The letter said so,
the critic flagged it, and the reviser kept it "because the system line allows".

When the candidate's OWN record states a per-tool figure, the line states that
figure and gives no hedge licence for a larger one; with no stated figure R-3 is
unchanged. One producer: the Oracle's #747 reader (``VaultIndex.
stated_skill_tenures``), extended so a conjunction before the FIRST skill after
the duration ("designing and operating Python") no longer cuts the subject off.
Synthetic profiles only.
"""
from __future__ import annotations

from applire.services.cover_letter import _letter_critic_fn
from applire.services.letter_v2 import render_tool_durations_block, tool_duration_facts
from applire.services.oracle.matchers.vault import build_vault_index, skills_near_duration

SUMMARY = "Backend engineer with 8 years of experience designing and operating Python services for data-heavy products."


def _w(wid, company, start, end, *, tech=(), resp=()):
    return {"id": wid, "company": company, "role": "Engineer", "start_date": start,
            "end_date": end, "technologies": list(tech), "responsibilities": list(resp)}


WORK = [
    _w("w1", "Alpha GmbH", "2015-01", "2017-06", tech=["Python", "SQL"]),
    _w("w2", "Beta AG", "2017-07", "2021-02", tech=["Python", "Flask"]),
    _w("w3", "Gamma GmbH", "2021-03", "2025-12", tech=["Python", "Django"]),
]


def _profile(summary=None, skills=("Python", "Django", "Flask", "SQL"), extra_skills=(), work=WORK):
    p = {
        "personal_info": {"name": "Felix Beispiel", "email": "fb@example.com"},
        "work_experience": list(work),
        "skills": [{"name": s, "category": "technical"} for s in skills] + list(extra_skills),
    }
    if summary is not None:
        p["professional_summary"] = {"en": summary, "de": None}
    return p


def _fact(facts, tool):
    got = [f for f in facts if f.tool == tool]
    assert len(got) == 1, facts
    return got[0]


def _line(block, tool):
    return next(ln for ln in block.splitlines() if ln.startswith(f"  - {tool} —"))


# ── the person's figure wins ──────────────────────────────────────────────

def test_the_candidates_own_figure_replaces_the_computed_hedge():
    facts = tool_duration_facts(_profile(SUMMARY), None)
    py = _fact(facts, "Python")
    assert py.years > 10 and py.stated_years == 8.0
    assert py.allowed_wordings("en") == ["8 years"]
    line = _line(render_tool_durations_block(facts, "en"), "Python")
    assert '"8 years"' in line and "the candidate's own figure" in line
    assert "over 10" not in line and "almost 11" not in line and "May be stated" not in line
    assert "designing and operating Python services" in line  # the source travels with it


def test_german_shape_and_wording():
    de = "Backend-Entwickler mit acht Jahren Erfahrung in Entwurf und Betrieb von Python-Diensten."
    p = _profile()
    p["professional_summary"] = {"de": de, "en": None}
    py = _fact(tool_duration_facts(p, None), "Python")
    assert py.stated_years == 8.0 and py.allowed_wordings("de") == ["8 Jahre"]


def test_linkedin_shape_eight_years_of_tool():
    py = _fact(tool_duration_facts(_profile("Eight years of Python, a soft spot for data."), None), "Python")
    assert py.stated_years == 8.0


def test_a_transcribed_skill_duration_is_the_candidates_figure():
    p = _profile(skills=("Django", "Flask", "SQL"), extra_skills=[
        {"name": "Python", "category": "technical", "source": "transcribed", "years_experience": 7}])
    assert _fact(tool_duration_facts(p, None), "Python").allowed_wordings("en") == ["7 years"]


def test_a_larger_own_figure_is_still_the_figure():
    p = _profile("Backend engineer with 14 years of experience building Python services.")
    py = _fact(tool_duration_facts(p, None), "Python")
    assert py.years < 14 and py.allowed_wordings("en") == ["14 years"]


def test_an_own_figure_stands_without_a_dated_position():
    p = _profile("Data engineer with 6 years of experience running Airflow pipelines.",
                 skills=("Python", "Airflow"))
    af = _fact(tool_duration_facts(p, None), "Airflow")
    assert af.positions == () and af.allowed_wordings("en") == ["6 years"]
    assert '"6 years"' in _line(render_tool_durations_block([af], "en"), "Airflow")


def test_the_own_figure_wins_even_where_r3_would_say_no_duration():
    """R-3 option A (CV does not show the tool under every counted position) yields
    to the ruling: the candidate's own figure is theirs to state."""
    cv = {"work_history": [{"id": "w3", "role": "Engineer", "bullets": ["Python services"]}]}
    py = _fact(tool_duration_facts(_profile(SUMMARY), cv), "Python")
    assert not py.stateable and py.allowed_wordings("en") == ["8 years"]


# ── nothing else moves ────────────────────────────────────────────────────

def test_no_stated_figure_keeps_r3_unchanged():
    facts = tool_duration_facts(_profile("Backend engineer who enjoys mentoring."), None)
    py = _fact(facts, "Python")
    assert py.stated_years is None
    assert py.allowed_wordings("en") == ["over 10 years", "almost 11 years"]
    assert 'May be stated as "over 10 years" or "almost 11 years".' in _line(
        render_tool_durations_block(facts, "en"), "Python")


def test_another_tools_figure_does_not_leak():
    s = "Backend engineer with 8 years of experience designing and operating Python services (Django and Flask)."
    facts = tool_duration_facts(_profile(s), None)
    assert _fact(facts, "Python").stated_years == 8.0
    assert _fact(facts, "Django").stated_years is None
    assert _fact(facts, "Flask").stated_years is None


def test_a_skill_before_the_duration_keeps_it_and_the_next_list_item_gets_none():
    text = "SAP CO (knapp acht Jahre) und SAP FI im Monatsabschluss."
    assert [n for n, _ in skills_near_duration(text, "acht Jahre", ["SAP CO", "SAP FI"])] == ["SAP CO"]
    # without punctuation: the skill before the duration still keeps the conjunction barrier
    text = "Python seit acht Jahren und Django im Team"
    assert [n for n, _ in skills_near_duration(text, "acht Jahren", ["Python", "Django"])] == ["Python"]


def test_a_comma_or_a_second_duration_cuts_the_subject():
    assert skills_near_duration(
        "ten years of experience in system integration, including LIMS", "ten years", ["LIMS"]) == []
    assert [n for n, _ in skills_near_duration(
        "five years of operations and nine years with PostgreSQL", "five years", ["PostgreSQL"])] == []


def test_an_overall_career_figure_without_the_tool_is_no_tool_figure():
    facts = tool_duration_facts(_profile("Engineer with 8 years of experience in logistics software."), None)
    assert _fact(facts, "Python").stated_years is None


def test_a_system_figure_is_no_person_figure():
    """A computed / llm_estimated skill duration is the system's arithmetic, not the
    candidate's word; a ledger evidence text never reaches this reader at all."""
    for source in ("computed", "llm_estimated"):
        p = _profile(skills=("Django", "Flask", "SQL"), extra_skills=[
            {"name": "Python", "category": "technical", "source": source, "years_experience": 11}])
        assert _fact(tool_duration_facts(p, None), "Python").stated_years is None
    idx = build_vault_index(_profile(SUMMARY))
    assert idx.stated_skill_tenure_sources["Python"][0] == "professional_summary.en"


# ── the critic's numeric finding reads the same figure ────────────────────

class _Adv:
    def __init__(self, concept, state):
        self.concept, self.letter_state = concept, state


def _critic_text(adv, facts):
    draft = {"body": {"paragraphs": [f"Intro. {adv.letter_state}. Close."]}}
    out = _letter_critic_fn([adv], facts, "en")(draft)
    assert len(out) == 1
    return out[0].text


def test_the_critic_finding_names_the_own_figure():
    facts = tool_duration_facts(_profile(SUMMARY), None)
    text = _critic_text(_Adv("Years of Python experience", "I have worked with Python for over 10 years"), facts)
    assert "the candidate's own figure" in text and '"8 years"' in text


def test_the_critic_finding_names_the_r3_hedge_without_an_own_figure():
    facts = tool_duration_facts(_profile(), None)
    text = _critic_text(_Adv("Years of Python experience", "I have worked with Python for 12 years"), facts)
    assert '"over 10 years"' in text


def test_the_critic_finding_is_unchanged_for_another_concept_or_without_facts():
    facts = tool_duration_facts(_profile(SUMMARY), None)
    adv = _Adv("Team size", "I led a team of 12 engineers")
    assert _critic_text(adv, facts).endswith("never add a fact.")
    adv = _Adv("Years of Python experience", "I have worked with Python for over 10 years")
    assert _critic_text(adv, None).endswith("never add a fact.")
