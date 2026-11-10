# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""784:128 (Hazelnut build 8, WP-L) — a letter states a duration only as the candidate
states it (``prompt_view.own_duration_statements``, W's producer) or as a TOOL
DURATIONS line allows (the W-2 career ceiling applied). The fact is the NUMBER; the
subject stays the reviewer's (ADR-062 cl. 1). Synthetic profiles only.
"""
import pytest

from applire.services.cover_letter import _letter_v2_findings, _v2_checks_fn
from applire.services.letter_v2 import unbacked_letter_durations


def _w(wid, company, start, end, tech=()):
    return {"id": wid, "company": company, "role": "Analyst", "start_date": start,
            "end_date": end, "technologies": list(tech)}


PROFILE = {
    "personal_info": {"name": "Lena Beispiel", "email": "lb@example.com"},
    "professional_summary": {"de": "Laborleiterin mit 7 Jahren Erfahrung in der Labordiagnostik.", "en": None},
    "work_experience": [
        _w("w1", "Alpha Labor GmbH", "2019-01", "2025-12", ["LIMS"]),
        _w("w2", "Beta Diagnostik AG", "2016-03", "2018-12", ["LIMS"]),
    ],
    "skills": [{"name": "LIMS", "category": "technical"}],
}


def _l(*paragraphs):
    return {"body": {"paragraphs": list(paragraphs)}}


@pytest.mark.parametrize("sentence,raw", [
    ("Ich bringe zehn Jahre Erfahrung in der Labordiagnostik mit.", "zehn Jahre"),
    ("With ten years in laboratory diagnostics I lead teams.", "ten years"),
    ("Meine sechsjährige Spezialisierung auf Hämatologie passt.", "sechsjährige"),
])
def test_a_duration_no_channel_carries_is_found(sentence, raw):
    found = unbacked_letter_durations(_l(sentence), PROFILE)
    assert [f["raw"] for f in found] == [raw]


@pytest.mark.parametrize("sentence", [
    "Ich bringe 7 Jahre Erfahrung in der Labordiagnostik mit.",       # own figure
    "Mit sieben Jahren Labordiagnostik leite ich das Labor.",          # own figure, spelled
    "Mit LIMS arbeite ich seit über 7 Jahren.",                        # LIMS hedge "über 7"
    "Seit 2019 leite ich das Labor.",                                  # a year, not a duration
    "Ich leite ein Team von 6 Personen.",                              # not a duration
])
def test_a_duration_a_channel_carries_is_not(sentence):
    assert unbacked_letter_durations(_l(sentence), PROFILE) == []


def test_the_ceiling_closes_the_tool_channel_above_the_own_figure():
    """LIMS spans 2016-03..2025-12 (9.8 years). With an own career figure of 5, W-2
    gives it no duration, so "über 9 Jahren" has no channel; without an own figure
    the R-3 hedge carries it."""
    sentence = _l("Mit LIMS arbeite ich seit über 9 Jahren.")
    capped = dict(PROFILE, professional_summary={"de": "Laborleiterin mit 5 Jahren Führungserfahrung.", "en": None})
    assert [f["years"] for f in unbacked_letter_durations(sentence, capped)] == [9.0]
    uncapped = dict(PROFILE, professional_summary={"de": "Laborleiterin in der Labordiagnostik.", "en": None})
    assert unbacked_letter_durations(sentence, uncapped) == []


def test_the_finding_is_blocking_in_the_v2_checks_and_names_the_own_figure():
    letter = _l("Sehr geehrte Damen und Herren,", "Ich bringe zehn Jahre Labordiagnostik mit.",
                "Ich freue mich auf ein Gespräch und stehe gern zur Verfügung.")
    findings = [f for f in _letter_v2_findings(letter, profile_json=PROFILE, keyword_ledger=[],
                                               denied_concepts=[]) if f.family == "duration"]
    assert len(findings) == 1 and findings[0].subjects == ("zehn Jahre",)
    assert '"7 Jahren"' in findings[0].text
    checks = _v2_checks_fn(lambda d: d, profile_json=PROFILE, keyword_ledger=[], denied_concepts=[],
                           language="de")(letter)
    dur = [c for c in checks if c.check == "letter.duration"]
    assert len(dur) == 1 and dur[0].blocking
