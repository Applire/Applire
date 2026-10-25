# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""L-3 = A (789:193, founder 2026-10-24): certification / education entries are
section-editor sections (``certification::<i>`` / ``education::<i>``), one line of
positional fields each; an empty override takes the entry off this CV."""
from applire.schemas.cv import TailoredCVData
from applire.services.cv_section_editor import (
    apply_overrides_to_tailored,
    build_content_snapshot,
    build_section_field_edit,
    entry_line,
    parse_entry_line,
    snapshot_entry_lines,
)


def _tailored() -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "Marcus"},
        "summary": "Produktionsleiter.",
        "work_history": [],
        "skills": ["Lean"],
        "certifications": [
            {"name": "REFA-Grundschein", "date_obtained": "2013"},
            {"name": "Ausgebildeter Sicherheitsbeauftragter"},
        ],
        "education": [
            {"degree": "Industriemeister Metall", "institution": "IHK", "end_date": "2010"},
            {"degree": "Ausbildung Industriemechaniker", "institution": "Rasselstein", "end_date": "2004"},
        ],
    })


def test_an_entry_line_round_trips_with_an_empty_middle_field():
    line = entry_line("certification", {"name": "REFA-Grundschein", "date_obtained": "2013"})
    assert line == "REFA-Grundschein ·  · 2013"
    assert parse_entry_line("certification", line) == {
        "name": "REFA-Grundschein", "issuing_organization": "", "date_obtained": "2013", "expiry_date": "",
    }
    assert parse_entry_line("education", "   ") is None


def test_the_snapshot_carries_one_line_per_entry_and_old_snapshots_rebuild_lazily():
    t = _tailored()
    snap = build_content_snapshot(t)
    assert snap["certifications"] == ["REFA-Grundschein ·  · 2013", "Ausgebildeter Sicherheitsbeauftragter"]
    assert snap["education"][0] == "Industriemeister Metall ·  · IHK ·  · 2010"
    old = {k: v for k, v in snap.items() if k not in ("certifications", "education")}
    assert snapshot_entry_lines(old, t.model_dump()) == snapshot_entry_lines(snap, None)


def test_an_empty_override_takes_the_entry_out_and_an_edit_rewrites_its_fields():
    t = _tailored()
    snap = build_content_snapshot(t)
    out = apply_overrides_to_tailored(t, snap, {
        "certification::1": "",
        "certification::0": "REFA-Grundschein · REFA · 2013",
        "education::0": "",
    })
    assert [(c.name, c.issuing_organization) for c in out.certifications] == [("REFA-Grundschein", "REFA")]
    assert [e.degree for e in out.education] == ["Ausbildung Industriemechaniker"]


def test_an_out_of_range_entry_override_changes_nothing():
    t = _tailored()
    out = apply_overrides_to_tailored(t, build_content_snapshot(t), {"education::7": ""})
    assert len(out.education) == 2


def test_save_to_profile_edits_the_vault_entry_and_never_deletes_one():
    from applire.schemas.profile import MasterProfileData

    profile = MasterProfileData.model_validate({
        "certifications": [
            {"name": "REFA-Grundschein", "date_obtained": "2013", "status": "confirmed"},
            {"name": "Ausgebildeter Sicherheitsbeauftragter", "status": "confirmed"},
        ],
    })
    t = _tailored()
    snap = build_content_snapshot(t)
    edit = build_section_field_edit(
        "certification::1", "Ausgebildeter Sicherheitsbeauftragter · BG ETEM · 2016",
        profile_data=profile, content_snapshot=snap, lang="de",
    )
    assert edit is not None and edit[0] == "certifications"
    assert edit[1][1]["date_obtained"] == "2016" and edit[1][1]["issuing_organization"] == "BG ETEM"
    assert edit[1][0]["name"] == "REFA-Grundschein"
    assert build_section_field_edit(
        "certification::1", "", profile_data=profile, content_snapshot=snap, lang="de",
    ) is None
