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
    assert strip_stray_glyphs({"a": ["x\x07y", 3, None]}) == {"a": ["xy", 3, None]}


def test_cv_v2_compose_strips_the_glyph():
    from applire.services.cv_v2 import compose_v2

    profile = {
        "personal_info": {"name": "Stefan"},
        "work_experience": [{"id": "w1", "company": "Rasselstein", "role": "Schichtleiter",
                             "start_date": "2011-08", "end_date": "2017-03"}],
        "skills": [], "education": [],
    }
    prose = {"summary": "Leiter.￼", "work": [{"id": "w1", "bullets": [_BULLET], "projects": []}],
             "skills": ["SA\x07P PP"]}
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


def test_joiners_in_persian_devanagari_and_emoji_survive_md_q7_f4():
    """MD-Q7 F4 (adversarial 1b probe): ZWNJ/ZWJ carry meaning; only U+FFFC and
    control characters are stray."""
    for kept in ("شرکت می\u200cخواهم", "क्\u200dष", "👨\u200d👩\u200d👧"):
        assert strip_stray_glyphs(kept) == kept


def test_a_recorded_name_with_a_joiner_is_still_recorded_md_q7_f4():
    """The probe's consequence: a vault employer written with a ZWNJ, returned exactly
    by the writer, must compose as a recorded name (no unsatisfiable finding)."""
    from applire.services.cv_v2 import ComposeNotes, compose_v2

    name = "شرکت می\u200cخواهم"
    profile = {"personal_info": {"name": "S"}, "work_experience": [
        {"id": "w1", "company": name, "role": "Engineer", "start_date": "2020-01"}],
        "skills": [], "education": []}
    prose = {"summary": "x", "work": [{"id": "w1", "company": name, "role": "Engineer",
                                        "bullets": ["Built it."], "projects": []}], "skills": []}
    notes = ComposeNotes()
    out = compose_v2(prose, profile, notes=notes, document_language="en")
    assert out.work_history[0].company == name
    assert not notes.names_not_recorded
