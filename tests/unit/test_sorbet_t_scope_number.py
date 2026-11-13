# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""B8-12 / RULING T-2 (Sorbet WP-T, 2026-11-13): the ADR-069 floor reads a
PLAIN numeric string as its number.

Hazelnut build 8, case A: gpt-6-luna analysed "(ca. 120 Mitarbeitende,
Mehrschichtbetrieb)" correctly as a team-size bar, but wrote ``"value": "120"``.
``job._coerce_scope_requirements`` required a JSON number and dropped the entry,
so all 16 gap-analysis calls of that run carried ``scope_requirements: []`` —
the gap analysis, which flags a team-size bar it receives in 177 of 192 captured
calls, never saw it. Synthetic names; the captured entry's shape, quote
whitespace included.
"""
from __future__ import annotations

import pytest

from applire.prompts.gap_analysis import build_user_prompt
from applire.services.gap_inference import pre_classify
from applire.services.job import _coerce_scope_requirements
from applire.services.scope_requirements import build_scope_prompt_block

JD_TEXT = (
    "Ihre Aufgaben\n"
    "- Gesamtverantwortung für Fertigung, Instandhaltung und Arbeitsvorbereitung\n"
    "  (ca. 120 Mitarbeitende, Mehrschichtbetrieb) am Standort Lahnstein.\n"
    "- Budgetverantwortung von 6.000.000 EUR für den Fertigungsbereich.\n"
)
#: The captured build-8 entry, names swapped: the value is a STRING, and the
#: quote keeps the posting's line break and indent.
CAPTURED = {
    "kind": "team_size",
    "value": "120",
    "value_max": None,
    "comparator": "approx",
    "quote": (
        "Gesamtverantwortung für Fertigung, Instandhaltung und Arbeitsvorbereitung\n"
        "  (ca. 120 Mitarbeitende, Mehrschichtbetrieb) am Standort Lahnstein."
    ),
    "level": "required",
}
PROFILE = {
    "work_experience": [{
        "id": "w-1", "role": "Fertigungsleiter", "company": "Brandel Kunststoffwerk GmbH",
        "start_date": "2018-03", "is_current": True, "team_size": 38,
        "responsibilities": ["Führung von 38 Mitarbeitenden in zwei Fertigungsbereichen"],
    }],
    "skills": [],
}


def test_the_captured_string_value_is_kept_as_its_number():
    (kept,) = _coerce_scope_requirements([CAPTURED], JD_TEXT)
    assert kept["kind"] == "team_size" and kept["value"] == 120.0


@pytest.mark.parametrize("raw, number", [
    ("120", 120.0),
    (" 120 ", 120.0),
    ("6.000.000", 6_000_000.0),
    ("6,000,000", 6_000_000.0),
    ("1.500", 1_500.0),
    (120, 120.0),
    (6.5, 6.5),
])
def test_a_plain_number_is_read(raw, number):
    (kept,) = _coerce_scope_requirements([dict(CAPTURED, value=raw)], JD_TEXT)
    assert kept["value"] == number


@pytest.mark.parametrize("raw", [
    "~120", "ca. 120", "über 100", "120 MA", "6 Mio.", "1,5", "6.000,000",
    "12.34", "1.5000", "", "hundertzwanzig", True, None, [120],
])
def test_anything_but_a_plain_number_is_still_dropped(raw):
    assert _coerce_scope_requirements([dict(CAPTURED, value=raw)], JD_TEXT) == []


def test_value_max_follows_the_same_rule():
    (kept,) = _coerce_scope_requirements([dict(CAPTURED, value_max="150")], JD_TEXT)
    assert kept["value_max"] == 150.0
    assert _coerce_scope_requirements([dict(CAPTURED, value_max="bis 150")], JD_TEXT) == []


def test_the_seam_the_gap_analysis_input_now_carries_the_bar():
    """What the gap analysis receives (``gap.py``: ``build_scope_prompt_block``
    over ``job.scope_requirements``, then ``build_user_prompt``)."""
    kept = _coerce_scope_requirements([CAPTURED], JD_TEXT)
    block = build_scope_prompt_block(kept, PROFILE, "de")
    assert len(block) == 1 and block[0]["jd_value"] == 120.0
    assert [v["value"] for v in block[0]["candidate_values"]] == [38]
    job = {"required_skills": ["Fertigungsleitung"], "nice_to_have_skills": [], "keywords": []}
    prompt = build_user_prompt(job, PROFILE, pre_classify(job, PROFILE), block)
    assert "SCOPE REQUIREMENTS" in prompt and '"jd_value": 120.0' in prompt
