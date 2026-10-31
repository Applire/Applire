# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dateline (Hazelnut build 5, delivery run 5: 4/4 panelists noticed it).

The recorded location "Mainz, Rheinland-Pfalz, Deutschland" dated the letter
"Rheinland-Pfalz, 29 October 2026". ``date_line_place`` took the last segment that
was not a country, and that segment was the STATE. The rule is a FACT, a closed-list
cut of the recorded string: a country, a state or an area never fills the place
slot. When the recorded location names no city, the line is the date alone.

Seam tests for both doors that print the line (the letter's ``_inject_letter_date``
and the CV's ``format_place_date_for_cv``). Synthetic data only.
"""
from datetime import date

import pytest

from applire.services.letter_v2 import date_line_place, sender_location

TODAY = date(2026, 10, 29)


@pytest.mark.parametrize("recorded, place", [
    ("Mainz, Rheinland-Pfalz, Deutschland", "Mainz"),
    ("Musterstadt, Bavaria, Germany", "Musterstadt"),
    ("Koblenz, RLP", "Koblenz"),
    ("Graz, Steiermark, Österreich", "Graz"),
    ("Aarau, Aargau, Schweiz", "Aarau"),
    ("Rheinland-Pfalz, Deutschland", None),
    ("Rheinland-Pfalz", None),
    ("Bayern", None),
    ("Germany", None),
    ("Rhein-Main-Gebiet", None),
    ("Greater Munich Metropolitan Area", None),
    # negative controls: a city-state and a canton named like its capital ARE places
    ("Berlin, Deutschland", "Berlin"),
    ("Hamburg", "Hamburg"),
    ("Zürich, Schweiz", "Zürich"),
    ("Salzburg, Österreich", "Salzburg"),
    ("Wien", "Wien"),
    ("55116 Mainz", "Mainz"),
])
def test_the_place_is_a_city_never_a_state_or_region(recorded, place):
    assert date_line_place(recorded) == place


def test_the_letter_line_carries_the_city_or_only_the_date():
    from applire.services.cover_letter import _inject_letter_date

    def line(location):
        loc = sender_location({"personal_info": {"location": location}}, None) or ""
        return _inject_letter_date({}, "en", TODAY, location=loc)["recipient"]["date"]

    assert line("Mainz, Rheinland-Pfalz, Deutschland") == "Mainz, 29 October 2026"
    assert line("Rheinland-Pfalz, Deutschland") == "29 October 2026"


def test_the_cv_closing_line_carries_the_city_or_only_the_date(monkeypatch):
    import applire.utils.letter_date as letter_date
    from applire.services.cv import format_place_date_for_cv

    real = letter_date.format_letter_date
    monkeypatch.setattr(letter_date, "format_letter_date", lambda lang, today=None: real(lang, TODAY))
    assert format_place_date_for_cv("Mainz, Rheinland-Pfalz, Deutschland", "de") == "Mainz, 29. Oktober 2026"
    assert format_place_date_for_cv("Rheinland-Pfalz", "de") == "29. Oktober 2026"
