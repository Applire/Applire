# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q3 (#835:277): the CV writer ended a bullet with U+FFFC (Sorbet
platform-1 delivery run 1, luna) and the PDF printed a box glyph on page 2.
One instrument, two seams: the CV v2 compose and the letter compose."""
from __future__ import annotations

from applire.utils.stray_glyphs import strip_stray_glyphs

_BULLET = "Als Key-User die Einführung von SAP in der Fertigung begleitet.￼"


def test_the_instrument_removes_only_stray_characters():
    assert strip_stray_glyphs(_BULLET) == "Als Key-User die Einführung von SAP in der Fertigung begleitet."
    kept = "Größe – „Zitat“ · 6 Mio. € · C# · CI/CD\nzweite Zeile\tTab"
    assert strip_stray_glyphs(kept) == kept
    assert strip_stray_glyphs({"a": ["x​y", 3, None]}) == {"a": ["xy", 3, None]}


def test_cv_v2_compose_strips_the_glyph():
    from applire.services.cv_v2 import compose_v2

    profile = {
        "personal_info": {"name": "Stefan"},
        "work_experience": [{"id": "w1", "company": "Rasselstein", "role": "Schichtleiter",
                             "start_date": "2011-08", "end_date": "2017-03"}],
        "skills": [], "education": [],
    }
    prose = {"summary": "Leiter.￼", "work": [{"id": "w1", "bullets": [_BULLET], "projects": []}],
             "skills": ["SAP PP﻿"]}
    out = compose_v2(prose, profile)
    assert out.summary == "Leiter."
    assert out.work_history[0].bullets == ["Als Key-User die Einführung von SAP in der Fertigung begleitet."]
    assert out.skills == ["SAP PP"]


def test_letter_compose_strips_the_glyph():
    from applire.services.cover_letter import _compose_letter

    data = {"header": {}, "recipient": {}, "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "Absatz.￼"]},
            "signature": {}}
    out = _compose_letter(data, profile_json={}, cv_data={}, profile=None, pre_gen={}, language="de", pipeline="v2")
    text = str(out)
    assert "￼" not in text
