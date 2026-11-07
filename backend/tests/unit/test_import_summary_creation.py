# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""783:260 (UX audit round 3, Priya) — the first import's summary said "We added
1 item to your profile" for a profile of ~40 entries: the record of the upload
that CREATED the profile carries one bookkeeping change ("initial import"), and
the summary counted that change. It now says the import created the profile and
lists the profile's current section sizes."""
from datetime import datetime, timezone

from applire.schemas.profile import (
    EducationEntry,
    EnrichmentRecord,
    FieldChange,
    MasterProfileData,
    ProfileMetadata,
    Skill,
    WorkEntry,
)
from applire.services.profile.receipts import summarize_last_import

_T0 = datetime(2026, 11, 7, 9, 0, tzinfo=timezone.utc)


def _profile(changes: list[FieldChange]) -> MasterProfileData:
    p = MasterProfileData(
        work_experience=[WorkEntry(company="Northwind Health", role="Nurse"), WorkEntry(company="Bluefin Care", role="Nurse")],
        education=[EducationEntry(institution="Riverside College", degree="BSc Nursing")],
        skills=[Skill(name="Triage"), Skill(name="Wound care"), Skill(name="Ventilation")],
    )
    p.metadata = ProfileMetadata(enrichment_history=[
        EnrichmentRecord(timestamp=_T0, source="cv_upload", changes=changes),
    ])
    return p


def test_the_creating_import_reports_created_and_the_profile_sizes():
    s = summarize_last_import(_profile([
        FieldChange(section="*", field="*", action="added", new_value="initial import"),
    ]), file_name="cv.pdf")
    assert s.created is True
    assert [(g.section, g.count) for g in s.groups] == [
        ("work_experience", 2), ("skills", 3), ("education", 1),
    ]
    assert s.added_total == 6
    assert s.facts == []


def test_a_merging_import_is_unchanged():
    s = summarize_last_import(_profile([
        FieldChange(section="skills", field="name", action="added", new_value="Triage"),
    ]))
    assert s.created is False
    assert s.added_total == 1
    assert [(g.section, g.count) for g in s.groups] == [("skills", 1)]
