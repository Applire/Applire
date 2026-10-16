# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E068 / ADR-094 (draft) — CV pipeline v2 (``CV_PIPELINE=v2``): the model writes,
code checks.

What these tests pin (each test is named for the property it holds):

* the switch: ``cv_pipeline()`` is v1 unless exactly ``v2`` (read at call time);
* ``compose_v2`` is the JOIN BY ID and nothing else — a recorded name is delivered as
  recorded, a non-recorded one falls back to the stored name and is NOTED (a block,
  never a rewrite); descriptions (industry line, field of study, language level) are
  the model's words, but an industry line is only taken where the vault states one;
  no bullet is cut, no skill is added, dropped, deduplicated or re-spelled;
* ``v2_checks`` reports what the v1 passes would have changed as loop findings, one
  implementation per detector (ADR-066); blocking vs advisory; bounded and ordered;
* ``carry_v2_fields`` — a later model pass must not silently drop a v2 field;
* the v2 prompts are DERIVED from the v1 strings (the v1 strings stay untouched, R-3);
* the writer's strict response schema;
* delivery through ``_render_cv_background``: v1 is the 0.43 wiring (no short ids, no
  checks), v2 wraps the provider in ``ShortIdProvider``, feeds the terminal loop its
  checks, and the delivered document keeps every bullet the writer wrote (no
  ``_cap_bullets``, condense measures only).

Every name, role and sentence here is synthetic. No Docker, no real LLM.
"""
from __future__ import annotations

import copy
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services import cv_v2  # noqa: E402
from applire.services.cv_budget import BudgetResult, RoleBudget  # noqa: E402
from applire.services.cv_v2 import (  # noqa: E402
    ComposeNotes,
    V2CheckContext,
    canonical,
    carry_v2_fields,
    compose_v2,
    cv_pipeline,
    norm_name,
    pick_recorded,
    v2_checks,
)

W1 = "11111111-1111-4111-8111-111111111111"  # recent role, aliases, industry line
W2 = "22222222-2222-4222-8222-222222222222"  # older role, no aliases, no industry line
E1 = "33333333-3333-4333-8333-333333333333"
L1 = "44444444-4444-4444-8444-444444444444"
L2 = "55555555-5555-4555-8555-555555555555"


# =============================================================================
# fixtures: one small synthetic vault and its v2 writer draft
# =============================================================================

def _vault() -> dict:
    return {
        "personal_info": {"name": "Milan Novak", "email": "milan@example.com"},
        "work_experience": [
            {
                "id": W1, "company": "Nivalo GmbH",
                "role": "Leiter Produktion",
                "role_aliases": ["Head of Production", "Production Lead"],
                "start_date": "2022-05", "end_date": None,
                "industry_context": "Maschinenbau",
                "responsibilities": ["Leitete die Fertigung mit zwölf Mitarbeitenden."],
            },
            {
                "id": W2, "company": "Brenner AG", "role": "Ingenieur",
                "start_date": "2018-01", "end_date": "2022-04",
                "responsibilities": ["Optimierte die Montagelinie."],
            },
        ],
        "education": [{
            "id": E1, "degree": "Diplom-Ingenieur",
            "degree_aliases": ["Master of Engineering"],
            "institution": "TU Wien", "field": "Maschinenbau",
            "start_date": "2010", "end_date": "2015",
        }],
        "languages": [
            {"id": L1, "language": "Deutsch", "level": "Muttersprache"},
            {"id": L2, "language": "Englisch", "level": "C1"},
        ],
        "skills": [
            {"name": "SPS", "status": "confirmed"},
            {"name": "MES", "status": "confirmed"},
            {"name": "SAP MM", "status": "confirmed"},
        ],
    }


def _prose() -> dict:
    """What a faithful v2 writer returns for ``_vault()``: every field by id."""
    return {
        "summary": "Leiter Produktion mit langjähriger Erfahrung im Maschinenbau.",
        "work": [
            {
                "id": W1, "role": "Head of Production", "company": "Nivalo GmbH",
                "industry_context": "Mechanical engineering",
                "bullets": ["Leitete die Fertigung mit zwölf Mitarbeitenden.",
                            "Senkte die Ausschussquote um 18 Prozent."],
                "projects": [],
            },
            {
                "id": W2, "role": "Ingenieur", "company": "Brenner AG",
                "industry_context": None,
                "bullets": ["Optimierte die Montagelinie."],
                "projects": [],
            },
        ],
        "projects": [],
        "skills": ["SPS", "MES"],
        "education": [{"id": E1, "degree": "Master of Engineering",
                       "institution": "TU Wien", "field": "Mechanical engineering"}],
        "languages": [{"id": L1, "language": "German", "level": "Native"},
                      {"id": L2, "language": "Englisch", "level": "C1"}],
    }


def _compose(prose=None, vault=None):
    notes = ComposeNotes()
    tailored = compose_v2(prose if prose is not None else _prose(),
                          vault if vault is not None else _vault(), notes=notes)
    return tailored, notes


def _budget(**max_bullets: int) -> BudgetResult:
    roles = {
        wid: RoleBudget(work_entry_id=wid, tier="top", max_bullets=n, company="x", role="y")
        for wid, n in max_bullets.items()
    }
    return BudgetResult(roles=roles, tiers={}, target_pages=2, region="DACH")


def _ctx(**kw) -> V2CheckContext:
    base = dict(profile_json=_vault(), keyword_ledger=[], job_dict={}, language="de")
    base.update(kw)
    return V2CheckContext(**base)


def _checks(prose=None, vault=None, **ctx_kw):
    prose = prose if prose is not None else _prose()
    vault = vault if vault is not None else _vault()
    tailored, notes = _compose(prose, vault)
    return v2_checks(prose, tailored, notes, _ctx(profile_json=vault, **ctx_kw))


def _by_check(findings) -> dict:
    return {f.check: f for f in findings}


# =============================================================================
# 1. cv_pipeline()
# =============================================================================

def test_cv_pipeline_defaults_to_v1(monkeypatch):
    monkeypatch.delenv("CV_PIPELINE", raising=False)
    assert cv_pipeline() == "v1"


@pytest.mark.parametrize("raw", ["v2", "V2", "  v2 "])
def test_cv_pipeline_v2_is_read_at_call_time(monkeypatch, raw):
    monkeypatch.setenv("CV_PIPELINE", raw)
    assert cv_pipeline() == "v2"


@pytest.mark.parametrize("raw", ["v3", "2", "banana", "", "true"])
def test_cv_pipeline_junk_falls_back_to_v1(monkeypatch, raw):
    monkeypatch.setenv("CV_PIPELINE", raw)
    assert cv_pipeline() == "v1"


# =============================================================================
# 2. pick_recorded — recorded names only, a mechanical normalisation
# =============================================================================

def test_pick_recorded_alias_returns_the_recorded_spelling_as_member():
    name, member = pick_recorded("head of production", "Leiter Produktion",
                                 ["Head of Production"])
    assert (name, member) == ("Head of Production", True)


def test_pick_recorded_free_translation_is_not_a_member_and_keeps_the_stored_name():
    name, member = pick_recorded("Production Manager", "Leiter Produktion",
                                 ["Head of Production"])
    assert (name, member) == ("Leiter Produktion", False)


@pytest.mark.parametrize("empty", [None, "", "   ", "..."])
def test_pick_recorded_empty_model_value_is_not_a_choice(empty):
    assert pick_recorded(empty, "Leiter Produktion", ["Head of Production"]) == (
        "Leiter Produktion", True)


def test_pick_recorded_normalises_case_whitespace_nfkc_and_surrounding_punctuation_only():
    stored, aliases = "Leiter Produktion", ["Senior Backend Engineer"]
    for spelled in ("senior backend engineer ", "SENIOR   BACKEND\tENGINEER",
                    "Senior Backend Engineer.", "Ｓenior Backend Engineer"):  # fullwidth S
        assert pick_recorded(spelled, stored, aliases) == ("Senior Backend Engineer", True), spelled
    # nothing semantic: word order, a dropped word, a hyphenated variant are not matches
    for other in ("Backend Engineer Senior", "Senior Backend", "Senior Back-end Engineer"):
        assert pick_recorded(other, stored, aliases) == (stored, False), other


def test_norm_name_is_empty_for_non_strings():
    assert norm_name(None) == "" and norm_name(42) == ""


# =============================================================================
# 3. compose_v2 — the join by id
# =============================================================================

def test_compose_delivers_the_alias_the_writer_picked():
    tailored, notes = _compose()
    assert tailored.work_history[0].role == "Head of Production"
    assert notes.names_not_recorded == []


def test_compose_non_recorded_role_delivers_the_stored_role_and_notes_it():
    prose = _prose()
    prose["work"][0]["role"] = "Production Manager"  # a free translation, not recorded

    tailored, notes = _compose(prose)

    assert tailored.work_history[0].role == "Leiter Produktion"
    assert [(loc, got, stored) for loc, got, stored, _ in notes.names_not_recorded] == [
        (f"work {W1} role", "Production Manager", "Leiter Produktion")]
    assert "Head of Production" in notes.names_not_recorded[0][3]


def test_compose_industry_line_is_the_models_where_the_vault_states_one():
    tailored, _ = _compose()
    assert tailored.work_history[0].industry_context == "Mechanical engineering"


def test_compose_industry_line_is_not_delivered_for_a_role_whose_vault_field_is_empty():
    prose = _prose()
    prose["work"][1]["industry_context"] = "Automotive supply"  # the vault states none

    tailored, notes = _compose(prose)

    assert tailored.work_history[1].industry_context is None
    assert not [r for r in notes.rows_not_returned if W2 in r[0]]


def test_compose_industry_line_the_writer_did_not_return_is_noted_not_invented():
    prose = _prose()
    prose["work"][0]["industry_context"] = None

    tailored, notes = _compose(prose)

    assert tailored.work_history[0].industry_context == "Maschinenbau", "the stored value stays"
    assert [loc for loc, _ in notes.rows_not_returned] == [f"work {W1} industry_context"]


def test_compose_education_field_and_language_level_are_delivered_as_the_model_wrote_them():
    tailored, notes = _compose()

    edu = tailored.education[0]
    assert (edu.degree, edu.institution, edu.field) == (
        "Master of Engineering", "TU Wien", "Mechanical engineering")
    assert [(l.language, l.level) for l in tailored.languages] == [
        ("German", "Native"), ("Englisch", "C1")]
    assert notes.names_not_recorded == [] and notes.rows_not_returned == []


def test_compose_language_name_in_the_closed_de_en_table_is_accepted():
    """'German' for the vault's 'Deutsch' is the same language by the closed table
    (ADR-062 cl. 1, a fact) — not a name finding."""
    _, notes = _compose()
    assert not [n for n in notes.names_not_recorded if "languages" in n[0]]


def test_compose_germanic_is_not_german_and_keeps_the_stored_language_name():
    prose = _prose()
    prose["languages"][0]["language"] = "Germanic"

    tailored, notes = _compose(prose)

    assert tailored.languages[0].language == "Deutsch"
    assert [(loc, got) for loc, got, _s, _n in notes.names_not_recorded
            if "languages" in loc] == [(f"languages {L1} language", "Germanic")]


def test_compose_missing_education_row_keeps_the_stored_values_and_is_noted():
    prose = _prose()
    prose["education"] = []

    tailored, notes = _compose(prose)

    edu = tailored.education[0]
    assert (edu.degree, edu.institution, edu.field) == (
        "Diplom-Ingenieur", "TU Wien", "Maschinenbau")
    assert [loc for loc, _ in notes.rows_not_returned] == [f"education {E1}"]


def test_compose_missing_language_row_keeps_the_stored_row_and_is_noted():
    prose = _prose()
    prose["languages"] = [prose["languages"][1]]

    tailored, notes = _compose(prose)

    assert (tailored.languages[0].language, tailored.languages[0].level) == (
        "Deutsch", "Muttersprache")
    assert [loc for loc, _ in notes.rows_not_returned] == [f"languages {L1}"]


def test_compose_removes_no_bullet_even_when_a_role_has_more_than_any_budget():
    prose = _prose()
    nine = [f"Konkrete Leistung Nummer {i}, belegt durch ein eigenes Ergebnis." for i in range(9)]
    prose["work"][0]["bullets"] = nine

    tailored, _ = _compose(prose)

    assert tailored.work_history[0].bullets == nine


def test_compose_adds_and_removes_no_skill():
    """The skills list is delivered exactly as the draft had it: a page duplicate, a
    JD-echo phrase, a vault skill the draft omitted (SAP MM) and a list past the cap."""
    prose = _prose()
    prose["skills"] = (["SPS", "MES", "MES (Maschinendatenerfassung)",
                        "Fast-Moving Product-Led Environment Experience"]
                       + [f"Zusatzkompetenz {i}" for i in range(30)])

    tailored, _ = _compose(prose)

    assert tailored.skills == prose["skills"]


def test_compose_joins_dates_and_employer_from_the_vault_by_id():
    prose = _prose()
    prose["work"][0].update({"start_date": "1999-01", "end_date": "2000-02"})
    prose["work"][0]["company"] = "Erfundene Firma AG"  # not a recorded company
    prose["work"].reverse()  # the draft's order is not the document's order

    tailored, notes = _compose(prose)

    assert [w.id for w in tailored.work_history] == [W1, W2], "vault order"
    first = tailored.work_history[0]
    assert (first.start_date, first.end_date, first.company) == ("2022-05", None, "Nivalo GmbH")
    assert tailored.work_history[1].start_date == "2018-01"
    assert tailored.work_history[1].end_date == "2022-04"
    assert [n[0] for n in notes.names_not_recorded] == [f"work {W1} company"]


# =============================================================================
# 4. v2_checks — the v1 passes as detectors on a copy
# =============================================================================

def _prose_with_projects() -> dict:
    prose = _prose()
    prose["work"][0]["bullets"] = [f"Leistung {i} mit eigenem Beleg." for i in range(4)]
    prose["work"][0]["projects"] = [
        {"name": "Relaunch", "bullets": ["Projektleistung A.", "Projektleistung B."]},
        {"name": "Leerlauf", "bullets": []},
    ]
    return prose


def test_role_budget_finding_names_the_id_and_counts_nested_project_bullets():
    # 4 role bullets alone fit a ceiling of 5; with 2 nested project bullets it is 6.
    findings = _by_check(_checks(_prose_with_projects(), budget=_budget(**{W1: 5, W2: 3})))

    f = findings["cv.role_budget"]
    assert f.blocking is True
    assert W1 in f.text and "6 bullets, budget 5" in f.text
    assert W2 not in f.text


def test_role_budget_finding_is_silent_at_the_ceiling_and_without_a_budget():
    prose = _prose_with_projects()
    assert "cv.role_budget" not in _by_check(_checks(prose, budget=_budget(**{W1: 6})))
    assert "cv.role_budget" not in _by_check(_checks(prose, budget=None))


def test_page_length_finding_fires_for_the_exact_draft_that_was_rendered():
    prose = _prose()
    findings = _by_check(_checks(prose, measured=(canonical(prose), 3, 2)))

    f = findings["cv.page_length"]
    assert f.blocking is True
    assert "3 pages" in f.text and "target is 2" in f.text


def test_page_length_finding_is_silent_for_a_measure_of_a_different_draft():
    prose = _prose()
    other = _prose()
    other["summary"] = "Ein anderer Entwurf."

    findings = _by_check(_checks(prose, measured=(canonical(other), 3, 2)))

    assert "cv.page_length" not in findings


def test_page_length_finding_is_silent_when_the_render_meets_the_target():
    prose = _prose()
    assert "cv.page_length" not in _by_check(_checks(prose, measured=(canonical(prose), 2, 2)))


def test_name_and_row_notes_become_recorded_name_and_vault_field_findings():
    prose = _prose()
    prose["work"][0]["role"] = "Production Manager"
    prose["education"] = []

    findings = _by_check(_checks(prose))

    name = findings["cv.recorded_name"]
    assert name.blocking and "Production Manager" in name.text and "Head of Production" in name.text
    rows = findings["cv.vault_field_missing"]
    assert rows.blocking and f"education {E1}" in rows.text


def test_a_clean_draft_raises_no_finding():
    assert _checks(_prose()) == []


def test_empty_project_finding_names_the_project():
    findings = _by_check(_checks(_prose_with_projects()))

    f = findings["cv.empty_project"]
    assert f.blocking is True and "Leerlauf" in f.text and "Relaunch" not in f.text


def test_skill_duplicate_finding_is_advisory():
    prose = _prose()
    prose["skills"] = ["SPS", "MES", "MES (Maschinendatenerfassung)"]

    f = _by_check(_checks(prose))["cv.skill_duplicate"]

    assert f.blocking is False
    assert "MES" in f.text


def test_skill_named_in_bullet_finding_is_advisory():
    prose = _prose()
    prose["work"][0]["bullets"] = ["Betreute SAP MM und die SPS-Programmierung der Linie."]
    prose["skills"] = ["SPS", "MES"]  # the vault's SAP MM is narrated but not listed

    f = _by_check(_checks(prose))["cv.skill_named_in_bullet"]

    assert f.blocking is False
    assert "SAP MM" in f.text


def test_skill_untied_finding_flags_a_jd_phrase_with_no_vault_tie():
    echo = "Fast-Moving Product-Led Environment Experience"
    prose = _prose()
    prose["skills"] = ["SPS", echo]

    findings = _by_check(_checks(prose, job_dict={"keywords": [echo]}))

    f = findings["cv.skill_untied"]
    assert f.blocking is True and echo in f.text
    assert "SPS" not in f.text


def test_skill_untied_finding_is_silent_without_the_jd_phrase():
    echo = "Fast-Moving Product-Led Environment Experience"
    prose = _prose()
    prose["skills"] = ["SPS", echo]
    # The phrase is not the posting's: not an echo, so not "untied".
    assert "cv.skill_untied" not in _by_check(_checks(prose, job_dict={"keywords": ["Other"]}))


@pytest.mark.xfail(
    strict=True,
    reason="PRODUCT DEFECT (W1): v2_checks 'cv.skills_cap' runs _tailor_skills_to_jd, which "
    "ALSO collapses page duplicates in its pool, so a 3-entry list with one duplicate pair "
    "is reported as 'over its cap of 24 entries' — a false, BLOCKING finding that turns "
    "the advisory cv.skill_duplicate into a mandatory corrector round.",
)
def test_skills_cap_finding_is_silent_for_a_list_under_the_cap():
    prose = _prose()
    prose["skills"] = ["SPS", "MES", "MES (Maschinendatenerfassung)"]

    findings = _by_check(_checks(prose))

    assert "cv.skills_cap" not in findings, findings["cv.skills_cap"].text


def test_skills_cap_finding_fires_for_a_list_over_the_cap():
    from applire.constants import CV_MAX_SKILLS

    prose = _prose()
    prose["skills"] = [f"Zusatzkompetenz {i}" for i in range(CV_MAX_SKILLS + 3)]

    f = _by_check(_checks(prose))["cv.skills_cap"]

    assert f.blocking is True and str(CV_MAX_SKILLS) in f.text


def _everything_wrong():
    prose = _prose_with_projects()
    prose["work"][0]["role"] = "Production Manager"
    prose["education"] = []
    echo = "Fast-Moving Product-Led Environment Experience"
    prose["work"][0]["bullets"][0] = "Betreute SAP MM und die SPS-Programmierung der Linie."
    prose["skills"] = ["SPS", "MES", "MES (Maschinendatenerfassung)", echo]
    return prose, dict(budget=_budget(**{W1: 5}), job_dict={"keywords": [echo]},
                       measured=(canonical(prose), 3, 2))


def test_findings_are_bounded_per_round():
    prose, kw = _everything_wrong()
    findings = _checks(prose, **kw)
    assert 0 < len(findings) <= cv_v2.MAX_FINDINGS_PER_ROUND


def test_findings_list_blocking_before_advisory():
    prose, kw = _everything_wrong()
    findings = _checks(prose, **kw)

    flags = [f.blocking for f in findings]
    assert flags == sorted(flags, reverse=True), "no advisory before a blocking finding"
    assert "cv.skill_duplicate" in {f.check for f in findings}, \
        "an advisory finding is listed (after the blocking ones), not swallowed"


def test_truncation_drops_advisory_findings_before_blocking_ones(monkeypatch):
    prose, kw = _everything_wrong()
    monkeypatch.setattr(cv_v2, "MAX_FINDINGS_PER_ROUND", 4)

    findings = _checks(prose, **kw)

    assert len(findings) == 4
    assert all(f.blocking for f in findings)


# =============================================================================
# 5. carry_v2_fields
# =============================================================================

def test_carry_restores_the_v2_fields_a_later_pass_dropped():
    previous = _prose()
    # a later pass (language pass, attribution round, corrector) returned v1's shape
    later = {
        "summary": "Neu formuliert.",
        "work": [{"id": W1, "bullets": ["B1"], "projects": []},
                 {"id": W2, "bullets": ["B2"], "projects": []}],
        "projects": [], "skills": ["SPS"],
    }

    out = carry_v2_fields(later, previous)

    assert out["education"] == previous["education"]
    assert out["languages"] == previous["languages"]
    w1, w2 = out["work"]
    assert (w1["role"], w1["company"], w1["industry_context"]) == (
        "Head of Production", "Nivalo GmbH", "Mechanical engineering")
    assert (w2["role"], w2["industry_context"]) == ("Ingenieur", None)
    # the later pass's own text is untouched
    assert out["summary"] == "Neu formuliert." and w1["bullets"] == ["B1"]
    assert out["education"] is not previous["education"], "a copy, not shared state"


def test_carry_never_overwrites_what_the_later_pass_returned():
    previous = _prose()
    later = copy.deepcopy(previous)
    later["work"][0]["role"] = "Production Lead"
    later["education"][0]["field"] = "Maschinenbau"

    out = carry_v2_fields(later, previous)

    assert out == later


def test_carry_is_a_noop_on_a_v1_draft():
    v1 = {"summary": "S", "work": [{"id": W1, "bullets": ["a"], "projects": []}],
          "projects": [], "skills": ["x"]}
    snapshot = copy.deepcopy(v1)

    out = carry_v2_fields(v1, copy.deepcopy(v1))

    assert out == snapshot and set(out) == set(snapshot)
    assert set(out["work"][0]) == {"id", "bullets", "projects"}
    assert v1 == snapshot, "input unmutated"


def test_carry_passes_non_dicts_through():
    assert carry_v2_fields("x", {"a": 1}) == "x"
    assert carry_v2_fields({"a": 1}, None) == {"a": 1}


# =============================================================================
# 6. build_v2_fields_block
# =============================================================================

def test_fields_block_lists_every_id_with_its_recorded_names():
    from applire.prompts.cv_tailoring import build_v2_fields_block

    block = build_v2_fields_block(_vault())

    assert block.startswith("VAULT FIELDS")
    for wid in (W1, W2):
        assert f"work [{wid}]" in block
    assert '"Leiter Produktion" | "Head of Production" | "Production Lead"' in block
    assert '"Nivalo GmbH"' in block and '"Ingenieur"' in block
    assert 'industry_context — "Maschinenbau" (translate)' in block
    assert "industry_context — null (the profile states none)" in block, \
        "an empty industry line is stated as null, so the writer does not invent one"
    assert f"education [{E1}]" in block
    assert '"Diplom-Ingenieur" | "Master of Engineering"' in block
    assert 'field — "Maschinenbau" (translate)' in block
    assert f"languages [{L1}]" in block and f"languages [{L2}]" in block
    assert '"Deutsch"' in block and '"Muttersprache"' in block and '"C1"' in block


def test_fields_block_carries_the_skills_cap_and_required_skills_only_when_given():
    from applire.prompts.cv_tailoring import build_v2_fields_block

    bare = build_v2_fields_block(_vault())
    full = build_v2_fields_block(_vault(), required_vault_skills=["SPS", "SAP MM"], skills_cap=24)

    assert "SKILLS LIST CAP" not in bare and "JD-REQUIRED SKILLS" not in bare
    assert "SKILLS LIST CAP: at most 24 entries" in full
    assert 'JD-REQUIRED SKILLS THE PROFILE HOLDS' in full and '"SPS", "SAP MM"' in full


def test_fields_block_for_the_job_names_the_required_vault_skills_and_the_cap():
    from applire.constants import CV_MAX_SKILLS

    block = cv_v2.build_v2_fields_block_for(
        _vault(), {"required_skills": ["SAP MM"]}, [])

    assert f"at most {CV_MAX_SKILLS} entries" in block
    assert '"SAP MM"' in block.split("JD-REQUIRED SKILLS THE PROFILE HOLDS", 1)[1]
    assert '"SPS"' not in block.split("JD-REQUIRED SKILLS THE PROFILE HOLDS", 1)[1]


# =============================================================================
# 7. prompt derivation — v2 derived from v1, v1 untouched
# =============================================================================

_V1_FACTS_SENTENCE = "are carried verbatim from the candidate's profile by the system"


def test_v2_writer_prompt_is_derived_with_the_recorded_names_rule_and_v2_schema():
    from applire.prompts.cv_tailoring import SYSTEM_PROMPT, V2_SYSTEM_PROMPT

    assert "RECORDED NAMES" in V2_SYSTEM_PROMPT
    for key in ('"education"', '"languages"', '"role"'):
        assert key in V2_SYSTEM_PROMPT, key
    assert _V1_FACTS_SENTENCE not in V2_SYSTEM_PROMPT
    assert _V1_FACTS_SENTENCE in SYSTEM_PROMPT, "v1 is untouched (R-3)"
    assert '"education"' not in SYSTEM_PROMPT


def test_v2_writer_prompt_states_that_the_budget_counts_nested_bullets_and_nothing_trims():
    from applire.prompts.cv_tailoring import SYSTEM_PROMPT, V2_SYSTEM_PROMPT

    assert "nothing trims the document after you" in V2_SYSTEM_PROMPT
    assert "nothing trims the document after you" not in SYSTEM_PROMPT


def test_v2_corrector_drops_the_two_sentences_v2_makes_false():
    from applire.prompts.cv_tailoring import (
        CV_TAILORING_REFINEMENT_PROMPT,
        V2_TAILORING_REFINEMENT_PROMPT,
    )

    for v1_only in ("re-joined from", "cut by the length budget"):
        assert v1_only not in V2_TAILORING_REFINEMENT_PROMPT, v1_only
        assert v1_only in CV_TAILORING_REFINEMENT_PROMPT, v1_only
    assert "CODE CHECKS" in V2_TAILORING_REFINEMENT_PROMPT
    assert "CODE CHECKS" not in CV_TAILORING_REFINEMENT_PROMPT


def test_v2_terminal_prompt_says_code_check_not_the_condense_mechanism():
    from applire.prompts.review_cv_tailoring import (
        build_terminal_review_prompt,
        build_v2_terminal_review_prompt,
    )

    kw = dict(page_count=2, target=2, condensation_exhausted=False, document_language="de")
    v2 = build_v2_terminal_review_prompt("SOURCE", {"summary": "x"}, **kw)
    v1 = build_terminal_review_prompt("SOURCE", {"summary": "x"}, **kw)

    assert "CODE CHECK" in v2 and "deterministic condense mechanism" not in v2
    assert "deterministic condense mechanism" in v1 and "CODE CHECK" not in v1


def test_v2_reviewer_systems_carry_the_swap_rule_and_v1_does_not():
    from applire.prompts.review_cv_tailoring import (
        REVIEW_SYSTEM_PROMPT,
        TERMINAL_REVIEW_SYSTEM_PROMPT,
        V2_REVIEW_SYSTEM_PROMPT,
        V2_TERMINAL_REVIEW_SYSTEM_PROMPT,
    )

    for v2 in (V2_REVIEW_SYSTEM_PROMPT, V2_TERMINAL_REVIEW_SYSTEM_PROMPT):
        assert "BUDGET RULE (v2)" in v2 and "SWAP" in v2
    for v1 in (REVIEW_SYSTEM_PROMPT, TERMINAL_REVIEW_SYSTEM_PROMPT):
        assert "BUDGET RULE (v2)" not in v1


def _v1_language_draft() -> dict:
    return {"summary": "Zusammenfassung.", "work": [
        {"id": W1, "bullets": ["Leitete die Fertigung."], "projects": []}],
        "projects": [], "skills": ["SPS"]}


def test_language_review_prompt_lists_education_and_language_rows_only_for_a_v2_draft():
    from applire.prompts.review_cv_language import build_cv_language_review_prompt

    v1 = build_cv_language_review_prompt("German", _v1_language_draft())
    v2_draft = {**_v1_language_draft(), **{k: _prose()[k] for k in ("education", "languages")}}
    v2 = build_cv_language_review_prompt("German", v2_draft)

    assert "education fields" not in v1 and "language rows" not in v1
    assert 'education fields of study: ["Mechanical engineering"]' in v2
    assert "language rows (name · level)" in v2 and "German · Native" in v2


def test_language_refinement_prompt_adds_the_v2_note_only_for_a_v2_draft():
    from applire.prompts.review_cv_language import build_cv_language_refinement_prompt

    v1 = build_cv_language_refinement_prompt(_v1_language_draft(), "fb")
    v2 = build_cv_language_refinement_prompt(
        {**_v1_language_draft(), "education": _prose()["education"]}, "fb")

    assert "education[].field" not in v1
    assert "education[].field" in v2 and "keep every `role`" in v2


# =============================================================================
# 8. the writer's strict response schema
# =============================================================================

def _walk_objects(node, path="$"):
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            yield path, node
        for key, child in node.items():
            yield from _walk_objects(child, f"{path}.{key}")
    elif isinstance(node, list):
        for i, child in enumerate(node):
            yield from _walk_objects(child, f"{path}[{i}]")


def test_writer_schema_is_strict_every_object_closed_and_fully_required():
    from applire.prompts.cv_tailoring import V2_WRITER_JSON_SCHEMA

    assert V2_WRITER_JSON_SCHEMA["strict"] is True
    objects = list(_walk_objects(V2_WRITER_JSON_SCHEMA["schema"]))
    assert len(objects) >= 6, "root, work, project(x2), education, languages"
    for path, obj in objects:
        assert obj["additionalProperties"] is False, path
        assert sorted(obj["required"]) == sorted(obj["properties"]), path


def test_writer_schema_asks_for_every_v2_field():
    from applire.prompts.cv_tailoring import V2_WRITER_JSON_SCHEMA

    root = V2_WRITER_JSON_SCHEMA["schema"]["properties"]
    assert set(root) == {"summary", "work", "projects", "skills", "education", "languages"}
    assert set(root["work"]["items"]["properties"]) == {
        "id", "role", "company", "industry_context", "bullets", "projects"}
    assert root["work"]["items"]["properties"]["industry_context"]["type"] == ["string", "null"]


def test_v2_writer_schema_follows_llm_structured_output(monkeypatch):
    from applire.config import settings
    from applire.prompts.cv_tailoring import V2_WRITER_JSON_SCHEMA

    monkeypatch.setattr(settings, "llm_structured_output", "auto")
    assert cv_v2.v2_writer_schema() is V2_WRITER_JSON_SCHEMA
    monkeypatch.setattr(settings, "llm_structured_output", "off")
    assert cv_v2.v2_writer_schema() is None


# =============================================================================
# 9. delivery through _render_cv_background (seam: services/cv.py)
# =============================================================================

TARGET_PAGES = 2


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, profile_json):
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis

    job_id, profile_id, cv_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=str(job_id), raw_text="Stelle", jd_language="de",
            role_title="Produktionsleiter", required_skills=[], nice_to_have_skills=[],
            keywords=[], seniority_level="senior", company_culture_signals=[],
            language_requirement="de",
        ),
        make_master_profile(
            id=profile_id, profile_json=profile_json,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        GeneratedCV(
            id=cv_id, job_analysis_id=job_id, profile_id=profile_id,
            tailored_data={}, template="creative_sidebar", status="pending",
            target_pages=TARGET_PAGES, document_language="de",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return job_id, profile_id, cv_id


NINE = [f"Konkrete Leistung Nummer {i} wurde durch ein eigenes Ergebnis belegt." for i in range(9)]


def _writer_payload(w1: str, w2: str, e1: str, l1: str, l2: str, *, v2: bool) -> dict:
    """The writer's response. v2: every vault field by id and nine bullets on the
    recent role (past any budget). The ids are the ones the model SEES: UUIDs under
    v1, short ids (w1…) under v2 — the wrapper restores them."""
    payload = {
        "summary": "Leiter Produktion mit langjähriger Erfahrung im Maschinenbau.",
        "work": [
            {"id": w1, "bullets": list(NINE), "projects": []},
            {"id": w2, "bullets": ["Optimierte die Montagelinie."], "projects": []},
        ],
        "projects": [],
        "skills": ["SPS", "MES"],
    }
    if v2:
        payload["work"][0].update(
            role="Head of Production", company="Nivalo GmbH",
            industry_context="Mechanical engineering")
        payload["work"][1].update(role="Ingenieur", company="Brenner AG", industry_context=None)
        payload["education"] = [{"id": e1, "degree": "Master of Engineering",
                                 "institution": "TU Wien", "field": "Mechanical engineering"}]
        payload["languages"] = [{"id": l1, "language": "German", "level": "Native"},
                                {"id": l2, "language": "Englisch", "level": "C1"}]
    return payload


def _fake_review(calls: list):
    """Chain-recording ``review_and_refine`` stub: every call's kwargs are kept, the
    terminal chain's closures are run (reviewer prompt, corrector prompt, and — under
    v2 — the checks the loop would be fed), and the draft settles untouched."""
    async def fake(**kwargs):
        chain = kwargs.get("chain_id")
        entry = {"chain": chain, "kwargs": kwargs}
        calls.append(entry)
        if chain == "cv_terminal_review":
            entry["reviewer_prompt"] = kwargs["reviewer_prompt_fn"](kwargs["source"], kwargs["draft"])
            if kwargs.get("checks_fn") is not None:
                entry["findings"] = kwargs["checks_fn"](kwargs["draft"])
        return kwargs["draft"]
    return fake


async def _run(db, monkeypatch, *, pipeline: str | None, pages: int = 3):
    from applire.services.cv import _render_cv_background

    if pipeline is None:
        monkeypatch.delenv("CV_PIPELINE", raising=False)
    else:
        monkeypatch.setenv("CV_PIPELINE", pipeline)
    v2 = pipeline == "v2"
    profile_json = _vault()
    ids = await _seed(db, profile_json)
    calls: list = []
    inner = AsyncMock()
    # v2 sees short ids (seeded in section order: w1, w2 / e1 / l1, l2); v1 sees UUIDs.
    inner.aparse_json.return_value = (
        _writer_payload("w1", "w2", "e1", "l1", "l2", v2=True) if v2
        else _writer_payload(W1, W2, E1, L1, L2, v2=False)
    )
    extract = MagicMock(side_effect=lambda pdf: ("text", pages))
    patches = [
        patch("applire.services.cv.get_provider", return_value=inner),
        patch("applire.services.cv.review_and_refine", side_effect=_fake_review(calls)),
        patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", 2),
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")),
        patch("applire.services.ats_audit.extract_text_and_pages", new=extract),
    ]
    from contextlib import ExitStack

    with patch("applire.services.cv.AsyncSessionLocal") as sl:
        sl.return_value.__aenter__.return_value = db
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await _render_cv_background(ids[2], ids[0], ids[1], "creative_sidebar")
    from applire.models.cv import GeneratedCV

    record = await db.get(GeneratedCV, ids[2])
    return record, calls, inner, extract


def _chain(calls, chain_id):
    return next(c for c in calls if c["chain"] == chain_id)


def _writer_calls(inner, system):
    return [c for c in inner.aparse_json.call_args_list if c.kwargs.get("system") == system]


@pytest.mark.asyncio
async def test_v1_wiring_is_the_043_pipeline_no_short_ids_no_checks(db, monkeypatch):
    from applire.prompts.cv_tailoring import CV_TAILORING_REFINEMENT_PROMPT
    from applire.services.model_ids import ShortIdProvider

    record, calls, inner, _ = await _run(db, monkeypatch, pipeline=None)

    assert record.status == "ready"
    for c in calls:
        assert not isinstance(c["kwargs"]["provider"], ShortIdProvider), c["chain"]
    terminal = _chain(calls, "cv_terminal_review")["kwargs"]
    for key in ("checks_fn", "on_checks_settle", "generator_json_schema"):
        assert key not in terminal, key
    assert terminal["max_retries"] == cv_terminal_default()
    assert _chain(calls, "cv_tailoring")["kwargs"]["generator_system"] is CV_TAILORING_REFINEMENT_PROMPT
    assert _chain(calls, "cv_tailoring")["kwargs"].get("generator_json_schema") is None


def cv_terminal_default() -> int:
    from applire.services.cv import CV_TERMINAL_REVIEW_MAX_RETRIES

    return CV_TERMINAL_REVIEW_MAX_RETRIES


@pytest.mark.asyncio
async def test_v1_baseline_condense_still_cuts_the_bullets_the_v2_arm_must_keep(db, monkeypatch):
    """The control for the v2 delivery test below: the SAME over-budget draft on a
    3-page render under v1 loses bullets (cap / condense). Without this the v2
    assertion would also pass on a pipeline that never had anything to cut."""
    record, _calls, _inner, _ = await _run(db, monkeypatch, pipeline=None)

    assert len(record.tailored_data["work_history"][0]["bullets"]) < len(NINE)


@pytest.mark.asyncio
async def test_v2_wiring_short_ids_v2_prompts_and_checks_reach_the_chains(db, monkeypatch):
    from applire.prompts.cv_tailoring import V2_SYSTEM_PROMPT, V2_TAILORING_REFINEMENT_PROMPT
    from applire.prompts.review_cv_tailoring import (
        V2_REVIEW_SYSTEM_PROMPT,
        V2_TERMINAL_REVIEW_SYSTEM_PROMPT,
    )
    from applire.services.model_ids import UUID_RE, ShortIdProvider

    record, calls, inner, _ = await _run(db, monkeypatch, pipeline="v2")

    assert record.status == "ready"
    for c in calls:
        assert isinstance(c["kwargs"]["provider"], ShortIdProvider), c["chain"]

    # the writer: v2 system prompt, VAULT FIELDS block, and no UUID anywhere
    writer = _writer_calls(inner, V2_SYSTEM_PROMPT)
    assert len(writer) == 1
    prompt = writer[0].args[0]
    assert "VAULT FIELDS" in prompt
    assert not UUID_RE.search(prompt) and not UUID_RE.search(writer[0].kwargs["system"])
    assert "work [w1]" in prompt and "education [e1]" in prompt and "languages [l1]" in prompt

    tailoring = _chain(calls, "cv_tailoring")["kwargs"]
    assert tailoring["generator_system"] is V2_TAILORING_REFINEMENT_PROMPT
    assert tailoring["reviewer_system"] is V2_REVIEW_SYSTEM_PROMPT

    terminal = _chain(calls, "cv_terminal_review")["kwargs"]
    assert callable(terminal["checks_fn"])
    assert callable(terminal["on_checks_settle"])
    assert terminal["max_retries"] >= 2
    assert terminal["reviewer_system"] is V2_TERMINAL_REVIEW_SYSTEM_PROMPT
    assert terminal["generator_system"] is V2_TAILORING_REFINEMENT_PROMPT


@pytest.mark.asyncio
async def test_v2_terminal_review_is_fed_the_budget_and_page_facts_of_the_drafted_document(db, monkeypatch):
    _record, calls, _inner, _ = await _run(db, monkeypatch, pipeline="v2")

    entry = _chain(calls, "cv_terminal_review")
    checks = {f.check for f in entry["findings"]}
    assert "cv.role_budget" in checks, "nine bullets on a role whose ceiling is lower"
    assert "cv.page_length" in checks, "3 rendered pages against a 2-page target"
    assert "CODE CHECK" in entry["reviewer_prompt"]
    assert "deterministic condense mechanism" not in entry["reviewer_prompt"]
    source = entry["kwargs"]["source"]
    assert "VAULT FIELDS" in source, "the reviewer reads the recorded names the writer was given"


@pytest.mark.asyncio
async def test_v2_delivery_keeps_every_bullet_and_does_not_condense_a_long_render(db, monkeypatch):
    record, _calls, _inner, extract = await _run(db, monkeypatch, pipeline="v2", pages=3)

    assert record.status == "ready"
    first = record.tailored_data["work_history"][0]
    assert first["bullets"] == NINE, "no _cap_bullets, no condense: 9 written, 9 delivered"
    assert extract.call_count >= 1, "the render WAS measured (3 pages vs target 2)"
    # the writer's vault-field choices are the delivered ones, joined by id
    assert first["role"] == "Head of Production"
    assert first["industry_context"] == "Mechanical engineering"
    assert first["start_date"] == "2022-05" and first["company"] == "Nivalo GmbH"
    edu = record.tailored_data["education"][0]
    assert (edu["degree"], edu["field"]) == ("Master of Engineering", "Mechanical engineering")
    assert [l["language"] for l in record.tailored_data["languages"]] == ["German", "Englisch"]
    assert record.tailored_data["skills"] == ["SPS", "MES"]
