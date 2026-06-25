# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""ADR-046 — deterministic applier acceptance + edge tests."""
from __future__ import annotations

from applire.schemas.profile import (
    MasterProfileData,
    Skill,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import ApplyResult, apply_ops
from applire.services.profile.reconcile.ops import (
    AddBullets,
    FlagConflict,
    RequestConfirmation,
    SetField,
    SetPersonalInfo,
    SetSummary,
    UpsertCertification,
    UpsertEducation,
    UpsertLanguage,
    UpsertProject,
    UpsertSkill,
    UpsertVolunteer,
    UpsertWork,
)

SOURCE = "cv_upload"


# ── Acceptance fixtures ───────────────────────────────────────────────────────


def test_synonym_role_fold():
    work = WorkEntry(company="Applire", role="Founder & Lead Developer")
    profile = MasterProfileData(work_experience=[work])

    ops = [UpsertWork(ref="w1", target=work.id, company="applire", role="Owner")]
    result = apply_ops(profile, ops, SOURCE)

    assert isinstance(result, ApplyResult)
    assert len(result.profile.work_experience) == 1
    entry = result.profile.work_experience[0]
    assert "Owner" in entry.role_aliases
    # original role preserved (non-empty role never overwritten)
    assert entry.role == "Founder & Lead Developer"
    # company not overwritten by lowercase variant
    assert entry.company == "Applire"
    assert any(c.action == "merged" for c in result.changes)


def test_project_under_position():
    work = WorkEntry(company="BioNTech", role="Associate Director")
    profile = MasterProfileData(work_experience=[work])

    ops = [
        UpsertProject(
            ref="p1",
            target=None,
            name="QC LIMS",
            parent=work.id,
            role="Solution Architect",
        ),
        AddBullets(target="p1", achievements=["Led LIMS validation"]),
    ]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.work_experience) == 1
    assert len(result.profile.projects) == 1
    proj = result.profile.projects[0]
    assert proj.associated_experience == work.id
    assert proj.role == "Solution Architect"
    assert "Led LIMS validation" in proj.achievements


def test_de_en_employer_semantic_match():
    work = WorkEntry(company="Blutspendedienst")
    profile = MasterProfileData(work_experience=[work])

    ops = [
        UpsertWork(
            ref="w1",
            target=work.id,
            company="Bavarian blood donation service",
            role="IT Qualitätsbeauftragter",
        )
    ]
    result = apply_ops(profile, ops, SOURCE)

    # applier trusts the LLM-chosen target — no new entry
    assert len(result.profile.work_experience) == 1
    entry = result.profile.work_experience[0]
    # company was empty? no — it's "Blutspendedienst", so not overwritten
    assert entry.company == "Blutspendedienst"
    # role was empty, so the incoming role fills it
    assert entry.role == "IT Qualitätsbeauftragter"


def test_undated_bullets_at_known_employer():
    work = WorkEntry(company="BioNTech", role="Engineer")
    profile = MasterProfileData(work_experience=[work])

    ops = [AddBullets(target=work.id, responsibilities=["Mentored juniors"])]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.work_experience) == 1
    entry = result.profile.work_experience[0]
    assert entry.responsibilities.count("Mentored juniors") == 1


# ── Edge tests ────────────────────────────────────────────────────────────────


def test_local_ref_project_parent():
    profile = MasterProfileData()
    ops = [
        UpsertWork(ref="w1", target=None, company="Acme", role="Dev"),
        UpsertProject(ref="p1", target=None, name="Migration", parent="w1"),
    ]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.work_experience) == 1
    assert len(result.profile.projects) == 1
    new_work_id = result.profile.work_experience[0].id
    assert result.profile.projects[0].associated_experience == new_work_id


def test_standalone_project_parent_none():
    profile = MasterProfileData()
    ops = [UpsertProject(ref="p1", target=None, name="OSS Tool", parent=None)]
    result = apply_ops(profile, ops, SOURCE)
    assert result.profile.projects[0].associated_experience is None


def test_bullet_dedup_case_insensitive():
    work = WorkEntry(company="Acme", role="Dev", responsibilities=["Built API"])
    profile = MasterProfileData(work_experience=[work])
    ops = [
        AddBullets(target=work.id, responsibilities=["built api", "Built API", "New thing"])
    ]
    result = apply_ops(profile, ops, SOURCE)
    resp = result.profile.work_experience[0].responsibilities
    assert resp.count("Built API") == 1
    assert "built api" not in resp
    assert "New thing" in resp


def test_set_field_fills_empty_end_date():
    work = WorkEntry(company="Acme", role="Dev", end_date=None)
    profile = MasterProfileData(work_experience=[work])
    ops = [SetField(target=work.id, field="end_date", value="2021-06")]
    result = apply_ops(profile, ops, SOURCE)
    assert result.profile.work_experience[0].end_date == "2021-06"
    assert any(c.action == "updated" for c in result.changes)


def test_set_field_ignored_on_non_empty():
    work = WorkEntry(company="Acme", role="Dev", end_date="2019")
    profile = MasterProfileData(work_experience=[work])
    ops = [SetField(target=work.id, field="end_date", value="2021")]
    result = apply_ops(profile, ops, SOURCE)
    assert result.profile.work_experience[0].end_date == "2019"
    assert not any(c.action == "updated" and c.field == "end_date" for c in result.changes)


def test_flag_conflict_no_mutation():
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [
        FlagConflict(target=work.id, field="company", existing="Acme", incoming="Acme Corp")
    ]
    result = apply_ops(profile, ops, SOURCE)
    assert len(result.conflicts) == 1
    c = result.conflicts[0]
    assert c.existing_value == "Acme"
    assert c.incoming_value == "Acme Corp"
    assert c.source == SOURCE
    # entity unchanged
    assert result.profile.work_experience[0].company == "Acme"


def test_request_confirmation_collected():
    profile = MasterProfileData()
    ops = [RequestConfirmation(question="Same employer?", options=["yes", "no"])]
    result = apply_ops(profile, ops, SOURCE)
    assert len(result.pending_confirmations) == 1
    assert result.pending_confirmations[0].question == "Same employer?"


def test_input_profile_not_mutated():
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [AddBullets(target=work.id, responsibilities=["New bullet"])]
    result = apply_ops(profile, ops, SOURCE)

    # input untouched
    assert profile.work_experience[0].responsibilities == []
    # returned is a distinct object
    assert result.profile is not profile
    assert result.profile.work_experience[0].responsibilities == ["New bullet"]


def test_unknown_ref_skipped_no_crash():
    profile = MasterProfileData()
    ops = [
        AddBullets(target="does-not-exist", responsibilities=["x"]),
        SetField(target="ghost", field="end_date", value="2020"),
        UpsertProject(ref="p1", target=None, name="P", parent="ghost-work"),
    ]
    result = apply_ops(profile, ops, SOURCE)
    # nothing for the bullets / setfield, project created with parent=None
    assert len(result.profile.projects) == 1
    assert result.profile.projects[0].associated_experience is None


def test_upsert_skill_unions_evidence_and_keeps_higher_proficiency():
    work = WorkEntry(company="Acme", role="Dev")
    existing = Skill(name="Python", proficiency="intermediate", experience_refs=["old-ref"])
    profile = MasterProfileData(work_experience=[work], skills=[existing])

    ops = [UpsertSkill(name="python", proficiency="expert", evidence=[work.id])]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.skills) == 1
    sk = result.profile.skills[0]
    assert sk.proficiency == "expert"
    assert "old-ref" in sk.experience_refs
    assert work.id in sk.experience_refs


def test_upsert_skill_does_not_downgrade_proficiency():
    existing = Skill(name="Python", proficiency="expert")
    profile = MasterProfileData(skills=[existing])
    ops = [UpsertSkill(name="Python", proficiency="basic")]
    result = apply_ops(profile, ops, SOURCE)
    assert result.profile.skills[0].proficiency == "expert"


def test_upsert_skill_new():
    profile = MasterProfileData()
    ops = [UpsertSkill(name="Rust", category="technical", proficiency="advanced")]
    result = apply_ops(profile, ops, SOURCE)
    assert len(result.profile.skills) == 1
    assert result.profile.skills[0].name == "Rust"
    assert any(c.action == "added" and c.section == "skills" for c in result.changes)


def test_upsert_skill_evidence_resolved_via_ref():
    profile = MasterProfileData()
    ops = [
        UpsertWork(ref="w1", target=None, company="Acme", role="Dev"),
        UpsertSkill(name="Go", evidence=["w1"]),
    ]
    result = apply_ops(profile, ops, SOURCE)
    new_id = result.profile.work_experience[0].id
    assert result.profile.skills[0].experience_refs == [new_id]


def test_upsert_volunteer():
    profile = MasterProfileData()
    ops = [
        UpsertVolunteer(
            ref="v1", target=None, organization="Red Cross", role="Helper", cause="Health"
        )
    ]
    result = apply_ops(profile, ops, SOURCE)
    assert len(result.profile.volunteer_activities) == 1
    v = result.profile.volunteer_activities[0]
    assert v.organization == "Red Cross"
    assert v.cause == "Health"


def test_project_parent_volunteer_has_no_id():
    # SCHEMA NOTE: VolunteerActivity has no `id` field (unlike WorkEntry/
    # ProjectEntry), so a project parented to a volunteer cannot store a
    # back-reference id. The applier resolves the parent but, finding no id,
    # leaves associated_experience None rather than crashing.
    vol_ref = "v1"
    profile = MasterProfileData()
    ops = [
        UpsertVolunteer(ref=vol_ref, target=None, organization="NGO", role="Vol"),
        UpsertProject(ref="p1", target=None, name="Outreach", parent=vol_ref),
    ]
    result = apply_ops(profile, ops, SOURCE)
    assert result.profile.projects[0].associated_experience is None


def test_upsert_certification_dedup():
    profile = MasterProfileData()
    ops = [
        UpsertCertification(name="AWS SAA"),
        UpsertCertification(name="aws saa"),  # case-insensitive dup
    ]
    result = apply_ops(profile, ops, SOURCE)
    assert len(result.profile.certifications) == 1


def test_upsert_language_dedup():
    profile = MasterProfileData()
    ops = [
        UpsertLanguage(language="German", level="C2"),
        UpsertLanguage(language="german"),
    ]
    result = apply_ops(profile, ops, SOURCE)
    assert len(result.profile.languages) == 1


def test_upsert_education_dedup():
    profile = MasterProfileData()
    ops = [
        UpsertEducation(institution="TUM", degree="BSc", field="CS"),
        UpsertEducation(institution="tum", degree="bsc"),
    ]
    result = apply_ops(profile, ops, SOURCE)
    assert len(result.profile.education) == 1


def test_set_personal_info_fills_empty():
    profile = MasterProfileData()
    ops = [SetPersonalInfo(field="phone", value="+49 123")]
    result = apply_ops(profile, ops, SOURCE)
    assert result.profile.personal_info.phone == "+49 123"


def test_set_personal_info_ignored_when_present():
    profile = MasterProfileData()
    profile.personal_info.phone = "existing"
    ops = [SetPersonalInfo(field="phone", value="new")]
    result = apply_ops(profile, ops, SOURCE)
    assert result.profile.personal_info.phone == "existing"


def test_set_summary_replaces():
    profile = MasterProfileData()
    profile.professional_summary.de = "alt"
    ops = [SetSummary(lang="de", text="neu")]
    result = apply_ops(profile, ops, SOURCE)
    assert result.profile.professional_summary.de == "neu"


def test_upsert_work_new_creates_entry():
    profile = MasterProfileData()
    ops = [UpsertWork(ref="w1", target=None, company="NewCo", role="CTO", start_date="2020")]
    result = apply_ops(profile, ops, SOURCE)
    assert len(result.profile.work_experience) == 1
    assert result.profile.work_experience[0].company == "NewCo"
    assert any(c.action == "added" and c.section == "work_experience" for c in result.changes)
