# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""UX audit round 4 (Marcus, panel-seen): an education entry with a lone year —
"Ausbildung Industriemechaniker, 2004" stored as ``start_date`` with no end —
rendered "2004 – heute". Education has no ``is_current``; a lone date there is
a completion year and renders alone. Work keeps D-14's reading."""
from __future__ import annotations

from applire.templates.filters import period


def test_an_education_lone_year_renders_alone():
    assert period({"start_date": "2004", "end_date": None}, "heute", "education") == {
        "start": "2004", "end": "", "dash": False,
    }


def test_education_with_both_dates_and_work_with_unstated_current_are_unchanged():
    assert period({"start_date": "2001", "end_date": "2004"}, "heute", "education")["dash"] is True
    assert period({"start_date": "2005-03"}, "heute")["end"] == "heute"
    assert period({"start_date": "2005-03", "is_current": True}, "heute", "education")["end"] == "heute"


def test_every_cv_template_renders_education_with_the_education_kind():
    from pathlib import Path

    templates = Path(__file__).resolve().parents[2] / "backend" / "applire" / "templates"
    for t in templates.glob("*.html.j2"):
        src = t.read_text()
        if "edu | period(" in src:
            assert 'edu | period(labels.present, "education")' in src, t.name


def test_a_range_that_starts_and_ends_in_the_same_period_renders_once_q3():
    """Sorbet platform-1 delivery run 1 (#835:277): the education block printed
    "2013 – 2013", "2010 – 2010", "2004 – 2004" — the extraction stored one
    completion year as both start and end. One period, one date."""
    assert period({"start_date": "2013", "end_date": "2013"}, "heute", "education") == {
        "start": "2013", "end": "", "dash": False,
    }
    assert period({"start_date": "2019-03", "end_date": "2019-03"}, "heute") == {
        "start": "03/2019", "end": "", "dash": False,
    }
    # Different months of one year are a real range.
    assert period({"start_date": "2019-03", "end_date": "2019-09"}, "heute")["dash"] is True
