# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet quality-1, ruling MD-Q3: the adversarial findings on the merged branch
(``adv/findings.md``) that belong to WP-Q1 — H1, M6, M7 + L1, L4. Every input is
the finding's own probe input (p1/p10, p2, p4/p9, p3), synthetic.

* H1 — the leadership kind counts only what the person says of THEIR OWN
  leadership; a third party, a topic, a reporting line, a question or a mixed
  statement gives the weaker kind or none (the neutral line and one ask).
* M6 — a stated start in an ordinary wording the reader did not parse stored the
  role span; it is read now, and a "seit … YYYY" it still cannot read withholds.
  The person's own N-years figure caps the stored value.
* M7 + L1 — position-vocabulary false holds (new since the base ``dddd7940``, and
  three old ones): a third party's title, a topic noun after a title, a title
  as an object.
* L4 — a duration stated for a coordinated list of skills must hold for each.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.oracle import verify_claim
from applire.services.profile.reconcile.position_terms import (
    position_claims,
    unheld_title_term,
)
from applire.services.profile.role_facts import leadership_kind
from applire.services.skill_enrichment import enrich_skills_deterministic, stored_span_years


def _kind(*bullets: str) -> str | None:
    return leadership_kind({"company": "X GmbH", "role": "Controller", "responsibilities": list(bullets)})


# ── H1 ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bullet", [
    "Mein Vorgesetzter hatte Personalverantwortung für das Team",
    "Personalverantwortung der Abteilung lag beim Werksleiter",
    "Disziplinarische Führung lag bei der HR",
    "Seminar zu Personalverantwortung besucht",
    "Team of 5, my manager handled line management",
    "Reported to the line manager",
    "Abstimmung mit dem Line Manager",
    "Berichtslinie zum CFO (direct report)",
    "Reports directly to the CFO; direct reports to the CFO",
    "Wer hat disziplinarische Verantwortung? Ich nicht.",
    "Disciplinary actions were handled by HR; I coached 3 juniors",
    "Kein Teamleiter, aber disziplinarische Führung durch den Abteilungsleiter",
    "Mentoring of 3 juniors, line manager was Anna",
    "Hatte ich Personalverantwortung für 5 Mitarbeitende? Nein.",
])
def test_H1_another_holder_a_topic_or_a_question_is_never_disciplinary(bullet):
    assert _kind(bullet) is None, bullet


@pytest.mark.parametrize("bullet", [
    "Ich führte das Team fachlich; disziplinarisch war ein anderer zuständig",
    "Team of 4, functional lead; disciplinary lead was the CTO",
])
def test_H1_the_other_holders_half_leaves_the_weaker_kind_or_none(bullet):
    assert _kind(bullet) in (None, "functional"), bullet


def test_H1_a_parenthesised_functional_marker_makes_a_mixed_statement_unknown():
    assert _kind("Projektteam geführt (fachlich) und Personalverantwortung für 5") is None


@pytest.mark.parametrize("bullet", [
    "Disziplinarische Führung von 38 Mitarbeitenden.",
    "Personalverantwortung für 12 Mitarbeitende.",
    "12 direct reports.",
    "Managed five direct reports.",
    "Line manager of six engineers.",
    "Als Teamleiter mit Personalverantwortung für 6 Personen.",
    "Leite das Team disziplinarisch.",
    # a cue in ANOTHER clause of the bullet does not void the person's own clause
    "Disziplinarische Führung von 12 Mitarbeitenden; Abstimmung mit der Personalabteilung.",
])
def test_H1_the_persons_own_disciplinary_leadership_still_counts(bullet):
    assert _kind(bullet) == "disciplinary", bullet


# ── M6 ───────────────────────────────────────────────────────────────────────


def _luca(*bullets: str) -> dict:
    return {
        "work_experience": [{
            "id": "a", "company": "Firma a", "role": "Controller", "start_date": "2015-01",
            "is_current": True, "responsibilities": list(bullets), "technologies": ["LucaNet"],
        }],
        "skills": [{"name": "LucaNet", "category": "technical"}],
    }


def _stored(p: dict) -> tuple:
    out = enrich_skills_deterministic(MasterProfileData.model_validate(p))
    s = out.skills[0]
    return s.years_experience, s.source


_TODAY = date.today()
_Y = _TODAY.year - 3


def _since(month: int) -> int:
    from applire.services.skill_enrichment import _union_years

    return int(_union_years([(date(_Y, month, 1), _TODAY)]))


@pytest.mark.parametrize("bullet, month", [
    (f"LucaNet seit Q1 {_Y}.", 1),
    (f"LucaNet seit Anfang {_Y}.", 1),
    (f"LucaNet seit Mitte {_Y}.", 7),
    (f"LucaNet seit dem Jahr {_Y}.", 1),
    (f"LucaNet seit dem 1.4.{_Y}.", 4),
    (f"LucaNet ab {_Y} im Einsatz.", 1),
])
def test_M6_an_ordinary_start_wording_is_read_and_governs_the_stored_span(bullet, month):
    role_span = _stored(_luca("LucaNet für Konsolidierung."))[0]
    assert role_span is not None and role_span >= 11
    assert _stored(_luca(bullet)) == (_since(month), "computed"), bullet
    model = MasterProfileData.model_validate(_luca(bullet))
    assert stored_span_years(model, "LucaNet") == _since(month)  # ADR-066: one rule


@pytest.mark.parametrize("bullet", [
    "Seit drei Jahren LucaNet für Konsolidierung.",
    "Drei Jahre Erfahrung mit LucaNet in der Konsolidierung.",
    "Operated LucaNet in production for three years.",
    "Three years of LucaNet consolidation.",
    "LucaNet: 3 years.",
])
def test_M6_the_persons_own_figure_caps_the_stored_span_de_and_en(bullet):
    """Delivery run 731e6454 (Daniel, Kafka "three years", stored 6): the own N-years
    figure governs the stored value exactly as a stated start does (WP-T precedence:
    own figure, then stated start, then the computed span)."""
    assert _stored(_luca(bullet)) == (3, "computed"), bullet
    model = MasterProfileData.model_validate(_luca(bullet))
    assert stored_span_years(model, "LucaNet") == 3


def test_M6_a_start_the_reader_cannot_parse_withholds_the_span():
    assert _stored(_luca("LucaNet seit dem Geschäftsjahr 2023.")) == (None, "transcribed")


def test_M6_a_part_of_year_start_keeps_the_persons_wording_as_its_label():
    from applire.services.skill_enrichment import governing_starts

    model = MasterProfileData.model_validate(_luca("LucaNet seit Mitte 2023."))
    start = governing_starts(model, ["LucaNet"])["lucanet"]
    assert start.label == "Mitte 2023" and start.month == 7


def test_M6_an_own_figure_larger_than_the_role_span_leaves_the_role_span():
    p = _luca("Zwanzig Jahre Erfahrung mit LucaNet.")
    assert _stored(p) == _stored(_luca("LucaNet für Konsolidierung."))


# ── M7 + L1 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    # new false holds since dddd7940
    "Vorstellung beim Geschäftsführer",
    "Berichtslinie zum Geschäftsführer",
    "Eingearbeitet vom Schichtführer",
    "Betreut von Oberarzt Müller",
    "Schichtführer-Qualifikation erworben",
    "Gruppenführerin unterstützt",
    "Schichtführer eingearbeitet",
    "Team Leiter geschult",
    "Department head meetings attended",
    # old false holds (already claims at dddd7940)
    "Teamleiter-Schulung",
    "Teamleiter war Frau X",
    "Head of Department Meetings besucht",
    # delivery run 731e6454 (Stefan): his reports' title, after "über" + a count
    "Disziplinarische Führung von 38 Mitarbeitenden über drei Schichtleiter.",
    "Disziplinarisch, über drei Schichtleiter.",
    "Führung von 4 Teamleitern",
    "Disziplinarisch über Schichtleiter und Meister",  # "über" alone
    "Leitete drei Schichtleiter im Dreischichtbetrieb",  # a count alone
])
def test_M7_a_title_that_is_not_the_persons_position_is_no_claim(text):
    assert position_claims(text) == [], text


@pytest.mark.parametrize("text", [
    "Ich war Oberarzt",
    "Als Geschäftsführer eingesetzt",
    "Prokuristin seit 2020",
    "Als Schichtführer eingearbeitet",
    "Beförderung zum Teamleiter",
    "Kommissarischer Leiter der Abteilung",
    "Seit 2011 Schichtleiter",
    "2 Jahre Teamleiter",
    "08/2011 Schichtleiter",
])
def test_M7_the_persons_own_title_is_still_a_claim(text):
    assert position_claims(text), text


def test_L1_a_third_partys_title_in_a_bullet_does_not_hold_it():
    p = {"work_experience": [{"company": "A", "role": "Sachbearbeiter",
                              "responsibilities": ["Teamleiter war Frau X."]}]}
    assert unheld_title_term("Teamleiter", p) == "teamleiter"


# ── L4 ───────────────────────────────────────────────────────────────────────


def test_L4_a_coordinated_list_states_the_duration_of_each_member():
    p = {
        "work_experience": [{
            "id": "w1", "company": "Firma A", "role": "Controller", "start_date": "2015-03",
            "is_current": True, "technologies": ["LucaNet", "Power BI"],
            "responsibilities": ["Konsolidierung in LucaNet seit 2021.", "Reporting mit Power BI."],
        }],
        "skills": [{"name": n, "category": "technical"} for n in ("LucaNet", "Power BI", "SAP")],
    }
    v = asyncio.run(verify_claim("LucaNet und Power BI: über sieben Jahre Erfahrung.", p))
    assert (v.verdict, v.detail_key) == ("unbacked", "subjectTenureExceedsStart"), v
    assert v.detail_params["skill"] == "LucaNet"
    # Power BI alone still carries seven years.
    ok = asyncio.run(verify_claim("Über sieben Jahre Erfahrung mit Power BI im Reporting.", p))
    assert ok.verdict != "unbacked"


# ── delivery run 731e6454: the redundant position ask (Stefan) ───────────────


class _Stub:
    def __init__(self, ops):
        self.payload = {"ops": ops, "ambiguities": [], "denials": []}

    async def aparse_json(self, prompt, **kwargs):
        return self.payload


@pytest.mark.asyncio
async def test_SEAM_the_leadership_kind_answer_naming_his_reports_is_not_asked_again():
    """Stefan answered the leadership-kind question "Disziplinarisch, über drei
    Schichtleiter."; the reconciler wrote "Disziplinarische Führung von 38
    Mitarbeitenden über drei Schichtleiter" and the position check held
    "Schichtleiter" — his reports' title — and asked whether it was his position."""
    from applire.services.profile.reconcile.engine import reconcile
    from applire.services.profile.reconcile.ops import AddBullets, RequestConfirmation

    profile = MasterProfileData.model_validate({"work_experience": [{
        "id": "w-1", "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
        "start_date": "2017-04", "is_current": True, "team_size": 38,
        "responsibilities": ["Verantwortung für zwei Fertigungsbereiche mit 38 Mitarbeitenden."],
    }]})
    bullet = "Disziplinarische Führung von 38 Mitarbeitenden über drei Schichtleiter"
    result = await reconcile(
        profile,
        {"answer": "Disziplinarisch, über drei Schichtleiter.", "gap": "Führungsart",
         "question": "War deine Führung bei Weberit disziplinarisch oder fachlich?"},
        "interview",
        _Stub([{"op": "add_bullets", "target": "w-1", "responsibilities": [bullet]}]),
    )
    assert [o for o in result.ops if isinstance(o, RequestConfirmation)] == []
    assert [o.responsibilities for o in result.ops if isinstance(o, AddBullets)] == [[bullet]]
