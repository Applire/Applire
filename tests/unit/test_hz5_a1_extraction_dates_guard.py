# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 D-24: the extraction reviewer called a true role date
"fabricated, the source gives no dates for this role" and the corrector nulled
it (delivery run 5; run 3's D-14 was the same harm on an end date). The
corrector's remedy is now bounded by a FACT: a date the first extraction gave
a work entry is restored when its year stands in the source near that entry.
A fabricated year's removal stands.

Offline replay of the logged chains: run 5 (2008-08 / 2011-05 nulled) restored
2/2; run 3 (2005-09 nulled) restored 1/1; every other entry untouched; a
control with the draft's start year changed to one the source never states
near the role stays removed.
"""
import copy

from applire.services.profile.extraction_dates import keep_source_dates

SOURCE = """Nordwerk AG
Systems Engineer
Oktober 2018 - Mai 2021 (2 Jahre 8 Monate)
Hamburg
Ran the lab systems.
Hochschule Talstadt
Wissenschaftliche Hilfskraft
August 2008 - Mai 2011 (2 Jahre 10 Monate)
Talstadt
Diploma thesis on sensor data.
"""


def _draft():
    return {"work_experience": [
        {"company": "Nordwerk AG", "role": "Systems Engineer", "start_date": "2018-10", "end_date": "2021-05"},
        {"company": "Hochschule Talstadt", "role": "Wissenschaftliche Hilfskraft",
         "start_date": "2008-08", "end_date": "2011-05"},
    ]}


def _nulled(draft, i=1):
    out = copy.deepcopy(draft)
    out["work_experience"][i]["start_date"] = None
    out["work_experience"][i]["end_date"] = None
    return out


def test_a_true_date_the_corrector_nulled_is_restored():
    draft = _draft()
    refined = _nulled(draft)
    notes = keep_source_dates(draft, refined, SOURCE)
    assert len(notes) == 2
    assert refined["work_experience"][1]["start_date"] == "2008-08"
    assert refined["work_experience"][1]["end_date"] == "2011-05"
    assert refined["work_experience"][0] == draft["work_experience"][0]


def test_a_true_date_the_corrector_changed_is_restored():
    draft = _draft()
    refined = copy.deepcopy(draft)
    refined["work_experience"][1]["end_date"] = "2010"
    keep_source_dates(draft, refined, SOURCE)
    assert refined["work_experience"][1]["end_date"] == "2011-05"


def test_a_fabricated_year_stays_removed():
    draft = _draft()
    draft["work_experience"][1]["start_date"] = "2006-08"  # the source never says 2006
    refined = _nulled(draft)
    keep_source_dates(draft, refined, SOURCE)
    assert refined["work_experience"][1]["start_date"] is None
    assert refined["work_experience"][1]["end_date"] == "2011-05"


def test_a_year_from_another_entrys_span_does_not_count():
    draft = _draft()
    draft["work_experience"][0]["start_date"] = "2008-08"  # 2008 is the OTHER entry's year
    refined = copy.deepcopy(draft)
    refined["work_experience"][0]["start_date"] = None
    keep_source_dates(draft, refined, SOURCE)
    assert refined["work_experience"][0]["start_date"] is None


def test_an_entry_the_corrector_dropped_or_renamed_is_left_alone():
    draft = _draft()
    refined = {"work_experience": [copy.deepcopy(draft["work_experience"][0])]}
    assert keep_source_dates(draft, refined, SOURCE) == []
    assert keep_source_dates(draft, draft, SOURCE) == []
