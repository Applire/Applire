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


# ── ADV-1 (build 8 adversarial pass): a duration a SOURCE carries is not unbacked ──
# The check fired on durations that are not the candidate's claim (the employer's
# age, the posting's bar) and on durations the profile's own dates carry (one role,
# one training). The fact is still the NUMBER: a duration no source the loop reads
# states and no single dated entry spans (ADR-062 cl. 1).

POSTING = ("Die Nordwerk GmbH ist seit über 30 Jahren ein verlässlicher Partner der Industrie. "
           "Sie bringen mindestens 2 Jahre Berufserfahrung im Einkauf mit.")

DATED = {
    "personal_info": {"name": "Lena Beispiel", "email": "lb@example.com"},
    "professional_summary": {"de": "Industriekauffrau mit Schwerpunkt Einkauf.", "en": None},
    "work_experience": [
        _w("w1", "Nordwerk GmbH", "2019-01", "2022-08"),          # 3.67 years: 3 / "knapp 4"
        _w("w2", "Südhaus AG", "2012-08", "2018-12"),             # 6.42 years: 6
    ],
    "education": [{"institution": "IHK München", "degree": "Industriekauffrau",
                   "start_date": "2009-08", "end_date": "2012-07"}],  # 3.0 years
    "skills": [],
}


@pytest.mark.parametrize("sentence", [
    "Ihr Unternehmen ist seit über 30 Jahren ein verlässlicher Partner der Industrie.",
    "Die geforderten mindestens 2 Jahre Berufserfahrung im Einkauf bringe ich mit.",
])
def test_a_duration_the_posting_states_is_not_unbacked(sentence):
    assert unbacked_letter_durations(_l(sentence), DATED, sources_text=POSTING) == []
    # the control: without the source the same sentence is found
    assert len(unbacked_letter_durations(_l(sentence), DATED)) == 1


@pytest.mark.parametrize("sentence", [
    "Nach meiner dreijährigen Ausbildung bei der IHK München wechselte ich in den Einkauf.",
    "In drei Jahren bei der Nordwerk GmbH verantwortete ich den strategischen Einkauf.",
    "In knapp vier Jahren bei der Nordwerk GmbH baute ich den Lieferantenstamm auf.",
    "Sechs Jahre lang arbeitete ich bei der Südhaus AG im operativen Einkauf.",
])
def test_a_duration_one_dated_entry_spans_is_not_unbacked(sentence):
    assert unbacked_letter_durations(_l(sentence), DATED) == []


@pytest.mark.parametrize("sentence,years", [
    ("Ich bringe zehn Jahre Erfahrung im Einkauf mit.", 10.0),     # w1 + w2 added up: never
    ("Sieben Jahre lang arbeitete ich bei der Südhaus AG.", 7.0),  # 6.42 is not "sieben"
    ("Ich bringe zwölf Jahre Erfahrung im Einkauf mit.", 12.0),
])
def test_a_sum_across_entries_or_a_rounded_up_span_is_still_found(sentence, years):
    found = unbacked_letter_durations(_l(sentence), DATED, sources_text=POSTING)
    assert [f["years"] for f in found] == [years]


def test_an_entry_without_a_readable_end_carries_no_duration():
    """A start with neither an end date nor ``is_current`` is no span; a current
    entry runs to today (date arithmetic, never a guess)."""
    open_end = dict(DATED, work_experience=[], education=[
        {"institution": "IHK München", "degree": "Industriekauffrau", "start_date": "2009-08"}])
    s = _l("Nach meiner dreijährigen Ausbildung wechselte ich in den Einkauf.")
    assert [f["years"] for f in unbacked_letter_durations(s, open_end)] == [3.0]
    from datetime import date
    start = f"{date.today().year - 2}-01"
    current = dict(DATED, education=[], work_experience=[
        dict(_w("w9", "Nordwerk GmbH", start, None), is_current=True)])
    s2 = _l("Seit zwei Jahren verantworte ich bei der Nordwerk GmbH den Einkauf.")
    assert unbacked_letter_durations(s2, current) == []


def test_the_checks_fn_passes_the_sources_through():
    letter = _l("Sehr geehrte Damen und Herren,",
                "Ihr Unternehmen ist seit über 30 Jahren ein verlässlicher Partner der Industrie.",
                "Ich freue mich auf ein Gespräch und stehe gern zur Verfügung.")
    kw = dict(profile_json=DATED, keyword_ledger=[], denied_concepts=[], language="de")
    with_src = _v2_checks_fn(lambda d: d, sources_text=POSTING, **kw)(letter)
    without = _v2_checks_fn(lambda d: d, **kw)(letter)
    assert [c for c in with_src if c.check == "letter.duration"] == []
    assert len([c for c in without if c.check == "letter.duration"]) == 1
