# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 6, WP-L — the other computed facts and the prompt doors of G-1 / G-2 / MD6-2.

* ``employer_stations`` (G-2, ADR-057 amended 2026-11-04): the candidate's recorded
  position(s) at the target employer — a name match, legal form stripped, never a substring.
* ``build_gap_positioning_entry`` / ``build_motivation_entry``: the review loop's REQUIRED
  entries (three hooks: entry, reviewer check 4 by key, corrector bullet).
* ``limit_grounding`` grounds the NAMED gaps (ADR-076 amended 2026-11-04 clause 3).
* Every door that judges a letter sentence recognises the einarbeiten clause (MD6-2).
Synthetic data throughout.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest  # noqa: E402

from applire.services.cover_letter_positioning import (  # noqa: E402
    employer_stations,
    render_employer_record,
)
from applire.services.cross_document import KeyGap, gaps_to_name  # noqa: E402


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


# ── employer_stations (G-2) ──────────────────────────────────────────────────

PROFILE = {"work_experience": [
    {"company": "Nordlicht Therapeutics SE", "role": "Integration Architect",
     "start_date": "2014-03", "end_date": "2018-12"},
    {"company": "Nordlicht Therapeutics Logistik GmbH", "role": "Planner", "start_date": "2012-01"},
    {"company": "Kestrel Logistik GmbH", "role": "Head of Integration", "start_date": "2019-01",
     "is_current": True},
    "junk",
]}


@pytest.mark.parametrize("company", [
    "Nordlicht Therapeutics SE", "Nordlicht Therapeutics", "NORDLICHT THERAPEUTICS SE",
    "Nordlicht Therapeutics AG",
])
def test_a_station_matches_on_the_core_name_whatever_the_legal_form(company):
    out = employer_stations(PROFILE, company)
    assert [s["role"] for s in out] == ["Integration Architect"]
    assert out[0]["end_date"] == "2018-12"


def test_a_longer_or_shorter_name_never_matches():
    assert employer_stations(PROFILE, "Nordlicht") == []
    assert employer_stations(PROFILE, "Nordlicht Therapeutics Holding") == []


def test_a_current_station_reads_present():
    assert employer_stations(PROFILE, "Kestrel Logistik")[0]["end_date"] == "present"


@pytest.mark.parametrize("profile, company", [
    (PROFILE, None), (PROFILE, ""), (PROFILE, "GmbH"), (None, "Kestrel"), ({}, "Kestrel"),
])
def test_unknown_company_or_profile_yields_nothing(profile, company):
    assert employer_stations(profile, company) == []


def test_the_record_line_names_the_station_and_its_use():
    line = render_employer_record(employer_stations(PROFILE, "Nordlicht Therapeutics"))
    assert "Integration Architect at Nordlicht Therapeutics SE (2014-03–2018-12)" in line
    assert "may ground the engagement and the motivation" in line
    assert render_employer_record([]) == ""


# ── the REQUIRED entries ─────────────────────────────────────────────────────


def _gap(label, statement=None, concepts=None):
    return KeyGap(label=label, concepts=tuple(concepts or (label,)), statement=statement,
                  weight=1.0, mentions=1, first_position=0)


def test_gap_positioning_entry_carries_the_count_the_names_and_what_is_not_owed():
    from applire.services.cover_letter import build_gap_positioning_entry

    gaps = [_gap("Kafka"), _gap("Boomi", "Boomi nie.", ("Boomi", "Boomi Atmosphere")), _gap("Terraform")]
    entry = build_gap_positioning_entry(gaps, gaps_to_name(gaps), "en")
    assert entry["required"] is True and entry["gap_count"] == 3
    assert entry["named"] == [{"gap": "Kafka", "statement": None, "recorded": False}]
    flat = _flat(entry["instruction"])
    assert "from three on, only the most urgent one" in flat
    assert "never demanded, never named" in flat

    two = gaps[1:2]
    entry = build_gap_positioning_entry(two, gaps_to_name(two), "en")
    assert entry["named"][0]["statement"] == "Boomi nie." and entry["named"][0]["recorded"]
    assert entry["not_owed"] == ["Boomi Atmosphere"]
    assert "each of them" in _flat(entry["instruction"])


def test_no_gap_no_entry():
    from applire.services.cover_letter import build_gap_positioning_entry

    assert build_gap_positioning_entry([], [], "de") is None


def test_motivation_entry_typed_wins_else_derived():
    from applire.services.cover_letter import build_motivation_entry

    typed = build_motivation_entry("  Ich will in die Biotech.  ", [])
    assert typed["required"] and typed["typed"] == "Ich will in die Biotech."
    assert "used as given" in typed["instruction"]
    derived = build_motivation_entry(None, [{"role": "R"}])
    assert "typed" not in derived and "derived from the grounded fit" in derived["instruction"]
    assert "candidate_record" in derived["instruction"]
    assert "candidate_record" not in build_motivation_entry("", [])["instruction"]
    assert "never an invented feeling" in _flat(derived["instruction"])


# ── limit_grounding grounds the named gaps (ADR-076 am. clause 3) ────────────

LEDGER = [{"concept": "Kafka", "surface_forms": ["Kafka"], "status": "gap", "claimable": False,
           "fit_weight": 1.0}]
LETTER = {"body": {"paragraphs": [
    "Sehr geehrte Damen und Herren,",
    "Ich leite seit 2019 ein Integrationsteam. Mit Kafka habe ich noch nicht gearbeitet; "
    "in Kafka werde ich mich schnell einarbeiten.",
    "Mit freundlichen Grüßen",
]}}


def test_an_unrecorded_lack_is_ungrounded_unless_the_ladder_names_it():
    from applire.services.limit_grounding import ungrounded_limits

    assert [f.concept for f in ungrounded_limits(LETTER, LEDGER, [])] == ["Kafka"]
    assert ungrounded_limits(LETTER, LEDGER, [], named_gaps=["Kafka"]) == []
    assert [f.concept for f in ungrounded_limits(LETTER, LEDGER, [], named_gaps=["Terraform"])] == ["Kafka"]


def test_the_cut_and_the_signal_honour_the_named_gaps():
    from applire.services.limit_grounding import (
        cut_ungrounded_limits,
        has_no_ungrounded_limit,
        limit_signal_issues_fn,
    )

    out, findings = cut_ungrounded_limits(LETTER, LEDGER, [], named_gaps=["Kafka"])
    assert out is LETTER and findings == []
    cut, findings = cut_ungrounded_limits(LETTER, LEDGER, [])
    assert findings and "noch nicht gearbeitet" not in " ".join(cut["body"]["paragraphs"])
    assert has_no_ungrounded_limit(LETTER, LEDGER, [], named_gaps=["Kafka"])
    assert not has_no_ungrounded_limit(LETTER, LEDGER, [])
    assert limit_signal_issues_fn(LEDGER, [], lambda d: d, named_gaps=["Kafka"])(LETTER) == []
    assert limit_signal_issues_fn(LEDGER, [], lambda d: d)(LETTER)


def test_the_v2_finding_honours_the_named_gaps():
    from applire.services.cover_letter import _letter_v2_findings

    def limits(**kw):
        return [f for f in _letter_v2_findings(LETTER, profile_json={}, keyword_ledger=LEDGER,
                                               denied_concepts=[], **kw) if f.family == "limit"]

    assert limits()
    assert limits(named_gaps=["Kafka"]) == []


# ── the doors recognise the clause (MD6-2) and the ladder (G-1, MD6-4) ───────


@pytest.mark.parametrize("v2", [False, True])
def test_reviewer_doors(v2):
    from applire.prompts.review_cover_letter import letter_loop_prompts

    review, terminal, corrector = letter_loop_prompts(v2)
    for text in (review, terminal):
        flat = _flat(text)
        assert "the einarbeiten clause (g-1, md6-2)" in flat
        assert "it claims nothing about the past: never flag, cut, soften or source it" in flat
        assert "``gap_positioning``, ``motivation``," in flat
        assert "never demand a concept ``named`` does not hold" in flat
        assert "one statement is one decision (md6-4)" in flat
        assert "or a ``gap_positioning`` gap" in flat           # check 1: grounds a lack
        assert "``candidate_record``" in flat                    # AUTHORITY / check 3 (G-2)
        assert "``stated_limits`` is a constraint only" in flat
    assert "never touch a ``gap_positioning`` gap's sentence" in _flat(terminal)
    flat = _flat(corrector)
    assert "never write a lack the gap ladder does not name" in flat
    assert "``gap_positioning`` and ``motivation`` paragraphs are required content" in flat
    assert "it needs no source — keep it" in flat
    assert "the einarbeiten clause of a named gap included" in flat


@pytest.mark.parametrize("pipeline", ["v1", "v2"])
def test_writer_rules(pipeline):
    from applire.prompts.cover_letter import build_system_prompt

    flat = _flat(build_system_prompt("de", pipeline=pipeline))
    assert "obligation — the gap ladder (g-1)" in flat
    assert "a named gap's einarbeiten clause included, md6-2" in flat
    assert "* motivation (g-2)" in flat
    assert "the candidate's own record of this employer (g-2)" in flat
    assert "do not mention any gap at all" not in flat          # the G-1 contradiction is gone
    assert "required: stated limits" not in flat
    assert "a bare admission" not in flat                        # L-1 rung 3, superseded (L-3 = A)


def test_oracle_triage_recognises_the_clause_and_keeps_its_first_line():
    from applire.prompts.oracle_triage import ORACLE_TRIAGE_SYSTEM_PROMPT as p

    assert p.startswith("You are the Truthfulness Oracle's sentence triage classifier.\n")
    flat = _flat(p)
    assert "a sentence that names a gap and closes with such a clause is candidate-limit" in flat
    assert "\"positive\" here means a claim about the candidate's past" in flat
    assert "(\"as a fast learner\") — then candidate-claim" in flat


def test_writer_user_prompt_threads_the_ladder_the_record_and_the_motivation_line():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    common = dict(cv_data={}, jd_text="Director Integration at Nordlicht.", detected_language="en",
                  company_name="Nordlicht Therapeutics SE")
    p = build_cover_letter_prompt(pre_gen_inputs={}, gap_ladder_block="=== KEY-REQUIREMENT GAPS X ===",
                                  employer_record="THE CANDIDATE'S OWN RECORD OF THIS EMPLOYER: y",
                                  **common)
    assert "=== KEY-REQUIREMENT GAPS X ===" in p
    assert "or the candidate's own record of this employer below" in p
    assert "THE CANDIDATE'S OWN RECORD OF THIS EMPLOYER: y" in p
    assert "Personal motivation: none typed — derive 1–2 sentences" in p
    typed = build_cover_letter_prompt(pre_gen_inputs={"motivation": "Biotech."}, **common)
    assert "Personal motivation (incorporate naturally): Biotech." in typed
    assert "none typed" not in typed
    assert "own record of this employer below" not in typed
