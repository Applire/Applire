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

from datetime import date

import pytest

from applire.schemas.profile import (
    Certification,
    EducationEntry,
    Language,
    MasterProfileData,
    Publication,
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
    UpsertPublication,
    UpsertSkill,
    UpsertVolunteer,
    UpsertWork,
)

SOURCE = "cv_upload"


def _profile_with_education(institution: str, degree: str) -> MasterProfileData:
    return MasterProfileData(education=[EducationEntry(institution=institution, degree=degree)])


def _profile_with_certification(name: str) -> MasterProfileData:
    return MasterProfileData(certifications=[Certification(name=name)])


def _profile_with_language(language: str) -> MasterProfileData:
    return MasterProfileData(languages=[Language(language=language)])


def _profile_with_work(company: str, role: str, start_date: str | None) -> MasterProfileData:
    return MasterProfileData(
        work_experience=[WorkEntry(company=company, role=role, start_date=start_date)]
    )


def _profile_with_publication(title: str) -> MasterProfileData:
    return MasterProfileData(publications=[Publication(title=title)])


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


def test_flag_conflict_absent_side_is_not_a_conflict():
    # Bug 1 regression — absence is not a conflict. A flag_conflict where one
    # side is None/empty must NOT surface as a disputed value (the bogus
    # `team_size: '6' vs 'None'` finding). The value should simply be filled.
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [
        FlagConflict(target=work.id, field="team_size", existing="6", incoming=None),
        FlagConflict(target=work.id, field="team_size", existing=None, incoming="6"),
        FlagConflict(target=work.id, field="team_size", existing="6", incoming=""),
        FlagConflict(target=work.id, field="team_size", existing=[], incoming="x"),
    ]
    result = apply_ops(profile, ops, SOURCE)
    assert result.conflicts == []


def test_flag_conflict_equal_values_is_not_a_conflict():
    # Bug 1 regression — identical values (case/whitespace-insensitive) are not
    # a dispute; only genuinely-differing both-sides values are.
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [FlagConflict(target=work.id, field="company", existing="Acme", incoming=" acme ")]
    result = apply_ops(profile, ops, SOURCE)
    assert result.conflicts == []


def test_flag_conflict_both_sides_differ_still_recorded():
    # Bug 1 regression — the genuine both-present-and-differ case is preserved.
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [
        FlagConflict(target=work.id, field="company", existing="Acme", incoming="Globex")
    ]
    result = apply_ops(profile, ops, SOURCE)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].existing_value == "Acme"
    assert result.conflicts[0].incoming_value == "Globex"


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


def test_upsert_skill_freetext_category_does_not_crash():
    """A real-LLM trace emitted category='Cloud Platforms' (not a valid Literal).
    apply_ops must not crash; the category defaults to 'technical'."""
    profile = MasterProfileData()
    ops = [UpsertSkill(name="Azure", category="Cloud Platforms")]
    result = apply_ops(profile, ops, SOURCE)
    assert len(result.profile.skills) == 1
    assert result.profile.skills[0].name == "Azure"


# ── #172: near-duplicate skill merge on import ────────────────────────────────


def test_upsert_skill_near_dupe_merges_and_keeps_more_specific_name():
    """Incoming 'Team Leadership and Mentorship' strictly contains existing
    'Team Leadership' → merge (no second skill); the more-specific name wins;
    refs union, higher proficiency kept."""
    work = WorkEntry(company="Acme", role="Lead")
    existing = Skill(name="Team Leadership", proficiency="intermediate",
                     experience_refs=["ref-old"])
    profile = MasterProfileData(work_experience=[work], skills=[existing])
    ops = [UpsertSkill(name="Team Leadership and Mentorship", proficiency="advanced",
                       evidence=[work.id])]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.skills) == 1
    sk = result.profile.skills[0]
    assert sk.name == "Team Leadership and Mentorship"
    assert sk.proficiency == "advanced"
    assert set(sk.experience_refs) == {"ref-old", work.id}


def test_upsert_skill_near_dupe_keeps_existing_when_incoming_is_less_specific():
    """Incoming 'Team Leadership' is a subset of existing 'Team Leadership and
    Mentorship' → merge but keep the existing (more specific) name."""
    existing = Skill(name="Team Leadership and Mentorship", proficiency="advanced")
    profile = MasterProfileData(skills=[existing])
    ops = [UpsertSkill(name="Team Leadership", proficiency="expert")]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.skills) == 1
    sk = result.profile.skills[0]
    assert sk.name == "Team Leadership and Mentorship"
    assert sk.proficiency == "expert"  # higher proficiency still wins


def test_upsert_skill_compound_over_two_atoms_asks_confirmation():
    """A compound incoming skill that relates to MULTIPLE existing atoms by
    single-token containment must NOT silently merge — it emits a
    RequestConfirmation and leaves the atoms intact."""
    profile = MasterProfileData(skills=[Skill(name="Docker"), Skill(name="Kubernetes")])
    ops = [UpsertSkill(name="Docker & Kubernetes", proficiency="advanced")]
    result = apply_ops(profile, ops, SOURCE)

    # No silent merge: the two atoms are untouched, no compound appended.
    names = {s.name for s in result.profile.skills}
    assert names == {"Docker", "Kubernetes"}
    assert len(result.pending_confirmations) == 1
    conf = result.pending_confirmations[0]
    assert "Docker" in conf.question and "Kubernetes" in conf.question
    assert conf.options  # the user is offered distinct one-tap answers


def test_upsert_skill_single_token_containment_asks_confirmation_no_rename():
    """'Docker' existing + incoming 'Docker & Kubernetes' relate ONLY by
    single-token containment → confirmation, and the existing 'Docker' is NEVER
    renamed into the compound (that was the fabrication bug, #172)."""
    profile = MasterProfileData(skills=[Skill(name="Docker", proficiency="advanced")])
    ops = [UpsertSkill(name="Docker & Kubernetes", proficiency="expert")]
    result = apply_ops(profile, ops, SOURCE)

    names = {s.name for s in result.profile.skills}
    assert names == {"Docker"}  # unchanged: no rename, no compound appended
    assert result.profile.skills[0].proficiency == "advanced"  # untouched
    assert len(result.pending_confirmations) == 1
    conf = result.pending_confirmations[0]
    assert "Docker & Kubernetes" in conf.question
    assert conf.context.get("incoming_skill") == "Docker & Kubernetes"


def test_upsert_skill_react_native_not_merged_into_react():
    """'React' existing + incoming 'React Native' must NOT merge — both survive as
    distinct skills and the user is asked to confirm (#172 strict predicate)."""
    profile = MasterProfileData(skills=[Skill(name="React", proficiency="advanced")])
    ops = [UpsertSkill(name="React Native", category="technical",
                       proficiency="intermediate")]
    result = apply_ops(profile, ops, SOURCE)

    # The existing skill is untouched; nothing silently collapsed.
    assert {s.name for s in result.profile.skills} == {"React"}
    assert result.profile.skills[0].name == "React"
    assert len(result.pending_confirmations) == 1
    conf = result.pending_confirmations[0]
    # Context must carry everything needed to act on the answer later.
    assert conf.context.get("incoming_skill") == "React Native"
    assert conf.context.get("category") == "technical"
    assert conf.context.get("proficiency") == "intermediate"


@pytest.mark.parametrize("existing,incoming", [
    ("AWS", "AWS Lambda"),
    ("Spring", "Spring Boot"),
    ("Vue", "Vue Router"),
    ("Excel", "Excel VBA"),
])
def test_upsert_skill_single_token_containment_pairs_ask_confirmation(existing, incoming):
    """Every bare single-token containment pair defers to a confirmation instead of
    silently merging or renaming."""
    profile = MasterProfileData(skills=[Skill(name=existing)])
    ops = [UpsertSkill(name=incoming)]
    result = apply_ops(profile, ops, SOURCE)

    assert {s.name for s in result.profile.skills} == {existing}
    assert len(result.pending_confirmations) == 1
    assert result.pending_confirmations[0].context.get("incoming_skill") == incoming


def test_upsert_skill_distinct_skill_still_appends():
    """'JavaScript' must not merge into 'Java' — token-level distinctness holds."""
    profile = MasterProfileData(skills=[Skill(name="Java")])
    ops = [UpsertSkill(name="JavaScript")]
    result = apply_ops(profile, ops, SOURCE)
    assert {s.name for s in result.profile.skills} == {"Java", "JavaScript"}
    assert result.pending_confirmations == []
    assert result.profile.skills[0].category == "technical"


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


def test_project_parent_volunteer_links_via_id():
    # ADR-044/046: a project may hang off a volunteer role. VolunteerActivity now
    # carries an `id`, so the project stores a back-reference to its volunteer parent.
    vol_ref = "v1"
    profile = MasterProfileData()
    ops = [
        UpsertVolunteer(ref=vol_ref, target=None, organization="NGO", role="Vol"),
        UpsertProject(ref="p1", target=None, name="Outreach", parent=vol_ref),
    ]
    result = apply_ops(profile, ops, SOURCE)
    vol = result.profile.volunteer_activities[0]
    assert vol.id
    assert result.profile.projects[0].associated_experience == vol.id


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


def test_upsert_education_near_dupe_merges_and_fills_dates():
    """#177: 'Diplom ×3' — spelling variants of one institution must not stack."""
    profile = _profile_with_education("Julius-Maximilians-Universität Würzburg", "Diplom Informatik")
    ops = [UpsertEducation(institution="Universität Würzburg", degree="Diplom Informatik",
                           start_date="1998-10", end_date="2004-03")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.education) == 1
    assert result.profile.education[0].start_date == "1998-10"   # empty field filled
    assert not result.pending_confirmations
    # #177 review (finding 1): a merge that actually filled fields must leave an
    # audit trail — exactly one merged FieldChange, not silence.
    merged = [c for c in result.changes if c.action == "merged"]
    assert len(merged) == 1
    assert merged[0].section == "education"


def test_upsert_education_ambiguous_asks_instead_of_appending():
    profile = _profile_with_education("Universität Würzburg", "Diplom Informatik")
    ops = [UpsertEducation(institution="Universität Würzburg", degree="Diplom")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.education) == 1                    # not appended
    assert len(result.pending_confirmations) == 1
    assert result.pending_confirmations[0].context["section"] == "education"


def test_upsert_certification_near_dupe_merges():
    profile = _profile_with_certification("AWS Certified Solutions Architect")
    ops = [UpsertCertification(name="AWS Certified Solutions Architect – Associate",
                               issuing_organization="AWS")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.certifications) == 1
    assert result.profile.certifications[0].issuing_organization == "AWS"
    # #177 review (finding 1): merge-with-fill leaves exactly one merged change.
    merged = [c for c in result.changes if c.action == "merged"]
    assert len(merged) == 1
    assert merged[0].section == "certifications"


def test_upsert_certification_pure_dupe_near_match_no_fill_is_silent():
    """#177 review (finding 1): a near-dupe MATCH where the incoming op carries
    nothing new to fill must NOT leave a change record (unlike a real merge)."""
    profile = _profile_with_certification("AWS Certified Solutions Architect")
    ops = [UpsertCertification(name="AWS Certified Solutions Architect – Associate")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.certifications) == 1
    assert result.changes == []


def test_upsert_language_variant_auto_merges():
    profile = _profile_with_language("German")
    ops = [UpsertLanguage(language="German (Native)", level="native")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.languages) == 1
    assert result.profile.languages[0].level == "native"
    merged = [c for c in result.changes if c.action == "merged"]
    assert len(merged) == 1
    assert merged[0].section == "languages"


def test_upsert_publication_appends_and_dedupes():
    profile = _profile_with_publication("Model-based Testing of Embedded Systems")
    ops = [UpsertPublication(title="Model-Based Testing of Embedded Systems", venue="ETFA 2019")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.publications) == 1
    assert result.profile.publications[0].venue == "ETFA 2019"   # empty filled
    merged = [c for c in result.changes if c.action == "merged"]
    assert len(merged) == 1
    assert merged[0].section == "publications"


def test_publication_partial_date_coerces():
    p = Publication(title="T", published_date="2019")
    assert p.published_date == date(2019, 1, 1)


# ── #177 review (finding 4): raw op date strings must be coerced before fill ──


def test_upsert_certification_match_fill_coerces_partial_date():
    """A near-dupe MATCH that fills date_obtained with a partial 'YYYY' string
    must land as a real `date`, not the raw string surviving until round-trip."""
    profile = _profile_with_certification("AWS Certified Solutions Architect")
    ops = [UpsertCertification(name="AWS Certified Solutions Architect – Associate",
                               date_obtained="2019")]
    result = apply_ops(profile, ops, source="test")
    entry = result.profile.certifications[0]
    assert entry.date_obtained == date(2019, 1, 1)
    assert isinstance(entry.date_obtained, date)
    _roundtrips(result.profile)


def test_upsert_certification_match_fill_unparseable_date_fills_nothing():
    """An unparseable date string ('Q2 2019') must not corrupt the field — it
    stays None (nothing filled), and the profile still loads cleanly."""
    profile = _profile_with_certification("AWS Certified Solutions Architect")
    ops = [UpsertCertification(name="AWS Certified Solutions Architect – Associate",
                               date_obtained="Q2 2019")]
    result = apply_ops(profile, ops, source="test")
    entry = result.profile.certifications[0]
    assert entry.date_obtained is None
    assert result.changes == []  # nothing was actually filled
    _roundtrips(result.profile)


# ── #239: cross-language / symbol / cognate-stem certification dupes ─────────
# Founder-acceptance F6: a two-source import (CV PDF + LinkedIn PDF) produced
# three EN/DE or symbol/morphological duplicate pairs that the lexical-only
# near-dupe machinery let through as silent new entries (no confirmation ever
# — worse than AMBIGUOUS).


def test_upsert_certification_cross_language_pair_merges():
    """'Expert for Computersystemvalidation' (EN) / 'Experte für
    Computervalidierung' (DE) — disjoint token sets under skill_tokens alone,
    must merge via the certifications-only cross-language fold."""
    profile = _profile_with_certification("Expert for Computersystemvalidation")
    ops = [UpsertCertification(name="Experte für Computervalidierung")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.certifications) == 1
    assert not result.pending_confirmations


def test_upsert_certification_symbol_variant_merges():
    """'ITIL Foundation Level' / 'ITIL® Foundation' — the ® fuses onto the
    adjacent token ('itil®'), pushing the pair below the near-dupe threshold
    unless the trademark symbol is stripped first."""
    profile = _profile_with_certification("ITIL Foundation Level")
    ops = [UpsertCertification(name="ITIL® Foundation")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.certifications) == 1
    assert not result.pending_confirmations


def test_upsert_certification_cognate_stem_variant_merges():
    """'...Software Architect...' / '...Software Architecture...' — a Jaccard
    overlap of 0.71, just under the 0.75 near-dupe threshold, resolved by
    folding the architect/architecture cognate stem."""
    profile = _profile_with_certification(
        "Certified Professional Software Architect Foundation Level"
    )
    ops = [UpsertCertification(
        name="Certified Professional for Software Architecture Foundation Level"
    )]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.certifications) == 1
    assert not result.pending_confirmations


def test_upsert_certification_credential_id_matches_despite_different_names():
    """Same credential_id is a definitive identity anchor — matches even when
    the two sides used completely different display names for it."""
    profile = MasterProfileData(certifications=[
        Certification(name="ITIL Foundation", credential_id="GR123456789")
    ])
    ops = [UpsertCertification(name="Information Technology Infrastructure Library",
                               credential_id="gr123456789")]  # case-insensitive match
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.certifications) == 1
    assert not result.pending_confirmations


def test_upsert_certification_same_issuer_different_cert_does_not_merge():
    """Two genuinely different AWS certifications from the same issuer must
    NOT merge just because the issuer matches (#239 negative case)."""
    profile = MasterProfileData(certifications=[
        Certification(name="AWS Certified Solutions Architect",
                      issuing_organization="Amazon Web Services")
    ])
    ops = [UpsertCertification(name="AWS Certified Developer",
                               issuing_organization="Amazon Web Services")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.certifications) == 2


def test_upsert_certification_itil_foundation_vs_expert_does_not_merge():
    """'ITIL Foundation' and 'ITIL Expert' are different certification levels
    — must not merge (#239 negative case)."""
    profile = _profile_with_certification("ITIL Foundation")
    ops = [UpsertCertification(name="ITIL Expert")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.certifications) == 2


def test_upsert_certification_weak_similarity_asks_instead_of_appending():
    """A bare single-token containment on the folded names, with no shared
    issuer to confirm identity either way, must ask rather than silently
    decide (#239 direction 3: prefer AMBIGUOUS over silent merge AND over
    silent append)."""
    profile = _profile_with_certification("AWS")
    ops = [UpsertCertification(name="AWS Certified Developer")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.certifications) == 1          # not appended
    assert len(result.pending_confirmations) == 1
    assert result.pending_confirmations[0].context["section"] == "certifications"


def test_upsert_publication_match_fill_coerces_partial_date():
    profile = _profile_with_publication("Model-based Testing of Embedded Systems")
    ops = [UpsertPublication(title="Model-Based Testing of Embedded Systems",
                             published_date="2019")]
    result = apply_ops(profile, ops, source="test")
    entry = result.profile.publications[0]
    assert entry.published_date == date(2019, 1, 1)
    _roundtrips(result.profile)


def test_upsert_publication_match_fill_unparseable_date_fills_nothing():
    profile = _profile_with_publication("Model-based Testing of Embedded Systems")
    ops = [UpsertPublication(title="Model-Based Testing of Embedded Systems",
                             published_date="Q2 2019")]
    result = apply_ops(profile, ops, source="test")
    entry = result.profile.publications[0]
    assert entry.published_date is None
    assert result.changes == []
    _roundtrips(result.profile)


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


def test_upsert_work_without_target_adopts_same_org_and_start():
    """LLM missed the target: same company (2-token short/legal form) + same
    start month = same stint. #177 review (finding 3): the short form must be
    at least 2 tokens — bare single-token containment is never identity (see
    test_upsert_work_without_target_single_token_org_never_auto_matches)."""
    profile = _profile_with_work(company="Continental Automotive GmbH", role="Software Engineer",
                                 start_date="2015-04-01")
    ops = [UpsertWork(ref="w1", company="Continental Automotive", role="Senior Software Engineer",
                      start_date="2015-04-15")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.work_experience) == 1
    assert "Senior Software Engineer" in result.profile.work_experience[0].role_aliases
    assert not result.pending_confirmations


def test_upsert_work_without_target_single_token_org_never_auto_matches():
    """#177 review (finding 3), ADR-046 strict ruling: bare single-token org
    containment ('Ford' ⊂ 'Ford Foundation') is NEVER identity — two distinct
    employers can share one token. Even with an equal start month, this must
    ask rather than silently merge."""
    profile = _profile_with_work(company="Ford Foundation", role="Program Officer",
                                 start_date="2015-04-01")
    ops = [UpsertWork(ref="w1", company="Ford", role="Engineer", start_date="2015-04-15")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.work_experience) == 1          # nothing merged, nothing appended
    assert len(result.pending_confirmations) == 1


def test_upsert_work_without_target_same_org_no_dates_asks():
    profile = _profile_with_work(company="Continental Automotive GmbH", role="Software Engineer",
                                 start_date=None)
    ops = [UpsertWork(ref="w1", company="Continental", role="Software Engineer")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.work_experience) == 1          # nothing appended
    assert len(result.pending_confirmations) == 1


def test_upsert_work_different_org_still_appends():
    profile = _profile_with_work(company="Continental Automotive GmbH", role="Software Engineer",
                                 start_date="2015-04-01")
    ops = [UpsertWork(ref="w1", company="Bosch", role="Software Engineer", start_date="2019-01-01")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.work_experience) == 2


def test_upsert_work_unresolved_target_still_runs_near_dup_guard():
    """#181 pin (item 2): op.target is SET but resolves to nothing — a stale or
    hallucinated id that matches no existing entry and no ref_map ref. `target`
    stays None, so the deterministic near-dup guard still fires rather than blindly
    appending a duplicate. Here the same-org/same-month signal adopts the stint.
    This behavior is deliberate, not only the op.target-is-None case."""
    profile = _profile_with_work(company="Continental Automotive GmbH", role="Software Engineer",
                                 start_date="2015-04-01")
    ops = [UpsertWork(ref="w1", target="ghost-id-not-in-this-profile",
                      company="Continental Automotive", role="Senior Software Engineer",
                      start_date="2015-04-15")]
    result = apply_ops(profile, ops, source="test")
    assert len(result.profile.work_experience) == 1          # adopted, not duplicated
    assert "Senior Software Engineer" in result.profile.work_experience[0].role_aliases


# ── #177 review (finding 2): ambiguous engagement must not drop co-batched bullets ──


def test_ambiguous_work_upsert_addbullets_on_its_ref_carries_into_confirmation():
    """The reconciler emits UpsertWork (goes AMBIGUOUS, no target resolved) then
    AddBullets(target=<same local ref>) in the same batch. ref_map never gets
    populated for an ambiguous op, so the bullets must not be silently dropped —
    they land in the pending confirmation's context for the resolution turn."""
    profile = _profile_with_work(company="Continental Automotive GmbH", role="Software Engineer",
                                 start_date=None)
    ops = [
        UpsertWork(ref="w1", company="Continental", role="Software Engineer"),
        AddBullets(target="w1", responsibilities=["Led migration"], technologies=["Rust"]),
    ]
    result = apply_ops(profile, ops, source="test")

    assert len(result.profile.work_experience) == 1           # nothing appended/merged
    assert len(result.pending_confirmations) == 1
    conf = result.pending_confirmations[0]
    assert conf.context.get("incoming", {}).get("ref") == "w1"
    assert conf.context.get("pending_bullets") == {
        "responsibilities": ["Led migration"],
        "technologies": ["Rust"],
    }


def test_ambiguous_work_upsert_multiple_addbullets_extend_not_overwrite():
    """Two AddBullets ops in the same batch targeting the same unresolved ref
    must extend the carried lists, not overwrite one with the other."""
    profile = _profile_with_work(company="Continental Automotive GmbH", role="Software Engineer",
                                 start_date=None)
    ops = [
        UpsertWork(ref="w1", company="Continental", role="Software Engineer"),
        AddBullets(target="w1", responsibilities=["Led migration"]),
        AddBullets(target="w1", responsibilities=["Mentored juniors"], achievements=["Shipped v2"]),
    ]
    result = apply_ops(profile, ops, source="test")

    conf = result.pending_confirmations[0]
    assert conf.context.get("pending_bullets") == {
        "responsibilities": ["Led migration", "Mentored juniors"],
        "achievements": ["Shipped v2"],
    }


def test_add_bullets_unknown_ref_with_no_pending_confirmation_still_noops():
    """Control: an AddBullets targeting a ref with NO matching pending
    confirmation at all must keep today's silent, defensive skip."""
    profile = MasterProfileData()
    ops = [AddBullets(target="does-not-exist", responsibilities=["x"])]
    result = apply_ops(profile, ops, SOURCE)
    assert result.changes == []
    assert result.pending_confirmations == []


# ── Field-type coercion (data-corruption regression) ──────────────────────────
#
# A reconciler answering an interview budget question honestly emitted
#   set_field(target=<work>, field="budget_managed", value=1800000)  # an int
# WorkEntry.budget_managed is `str | None`. setattr-ing the int produced a
# profile that fails MasterProfileData.model_validate on the next load (500 on
# GET /api/profile, raw Pydantic error in CV gen, UI-unrecoverable). SetField/
# SetPersonalInfo.value are typed `Any`, so they bypass op-parse coercion.
# Coerce the scalar op value to the target field's declared type before writing.


def _roundtrips(profile: MasterProfileData) -> MasterProfileData:
    """The load path: dump → JSON → re-validate. Must never raise."""
    return MasterProfileData.model_validate(profile.model_dump(mode="json"))


def test_set_field_coerces_int_into_str_budget():
    # The exact UAT corruption: an int into a `str | None` field.
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [SetField(target=work.id, field="budget_managed", value=1800000)]
    result = apply_ops(profile, ops, SOURCE)
    entry = result.profile.work_experience[0]
    assert entry.budget_managed == "1800000"
    assert isinstance(entry.budget_managed, str)
    # the resulting profile must round-trip through the load path without error
    _roundtrips(result.profile)


def test_set_field_coerces_float_into_str_budget_cleanly():
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [SetField(target=work.id, field="budget_managed", value=1800000.0)]
    result = apply_ops(profile, ops, SOURCE)
    entry = result.profile.work_experience[0]
    # a whole-number float stringifies without a trailing ".0"
    assert entry.budget_managed == "1800000"
    assert isinstance(entry.budget_managed, str)
    _roundtrips(result.profile)


def test_set_field_coerces_numeric_string_into_int_team_size():
    # The reverse risk: a "6" string into `team_size: int | None`.
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [SetField(target=work.id, field="team_size", value="6")]
    result = apply_ops(profile, ops, SOURCE)
    entry = result.profile.work_experience[0]
    assert entry.team_size == 6
    assert isinstance(entry.team_size, int)
    _roundtrips(result.profile)


def test_set_field_skips_uncoercible_value_without_corruption():
    # A genuinely non-numeric string into `team_size: int | None` must NOT be
    # written (no corruption); the field stays empty and the profile loads.
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [SetField(target=work.id, field="team_size", value="a whole team")]
    result = apply_ops(profile, ops, SOURCE)
    entry = result.profile.work_experience[0]
    assert entry.team_size is None
    _roundtrips(result.profile)


def test_set_personal_info_coerces_to_field_type():
    # SetPersonalInfo.value is `Any` too; a number into `phone: str | None`.
    profile = MasterProfileData()
    ops = [SetPersonalInfo(field="phone", value=491234567)]
    result = apply_ops(profile, ops, SOURCE)
    assert result.profile.personal_info.phone == "491234567"
    assert isinstance(result.profile.personal_info.phone, str)
    _roundtrips(result.profile)


def test_apply_ops_result_always_revalidates():
    # Defense in depth: whatever the ops, apply_ops must never return a profile
    # that won't load.
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [
        SetField(target=work.id, field="budget_managed", value=1800000),
        SetField(target=work.id, field="team_size", value="6"),
    ]
    result = apply_ops(profile, ops, SOURCE)
    _roundtrips(result.profile)


# ── #155: is_current marker (current-position convergence) ───────────────────


def test_set_field_fills_is_current_marker():
    # "This is my current position" → set_field is_current=true; end_date stays
    # null (the extraction convention), and the fill-only rule is satisfied
    # because None → True fills an empty tri-state field.
    work = WorkEntry(company="Acme", role="Dev", end_date=None)
    profile = MasterProfileData(work_experience=[work])
    ops = [SetField(target=work.id, field="is_current", value=True)]
    result = apply_ops(profile, ops, "interview")
    entry = result.profile.work_experience[0]
    assert entry.is_current is True
    assert entry.end_date is None
    assert any(c.field == "is_current" for c in result.changes)
    _roundtrips(result.profile)


def test_set_field_is_current_fill_only_never_flips_known_false():
    # False = "known ended" is a real value; fill-only means it is never overwritten.
    work = WorkEntry(company="Acme", role="Dev", is_current=False)
    profile = MasterProfileData(work_experience=[work])
    ops = [SetField(target=work.id, field="is_current", value=True)]
    result = apply_ops(profile, ops, "interview")
    assert result.profile.work_experience[0].is_current is False
    assert not any(c.field == "is_current" for c in result.changes)


def test_set_field_coerces_string_true_into_is_current():
    # LLMs sometimes emit "true" as a string; Pydantic lax-bool accepts it.
    work = WorkEntry(company="Acme", role="Dev")
    profile = MasterProfileData(work_experience=[work])
    ops = [SetField(target=work.id, field="is_current", value="true")]
    result = apply_ops(profile, ops, "interview")
    assert result.profile.work_experience[0].is_current is True
    _roundtrips(result.profile)


def test_upsert_work_fills_is_current_on_existing_entry():
    work = WorkEntry(company="Acme", role="Dev", end_date=None)
    profile = MasterProfileData(work_experience=[work])
    ops = [
        UpsertWork(ref="w1", target=work.id, company="Acme", role="Dev", is_current=True)
    ]
    result = apply_ops(profile, ops, SOURCE)
    entry = result.profile.work_experience[0]
    assert entry.is_current is True
    assert entry.end_date is None
    _roundtrips(result.profile)


def test_upsert_work_new_entry_carries_is_current():
    profile = MasterProfileData()
    ops = [UpsertWork(ref="w1", target=None, company="Neu GmbH", role="Lead", is_current=True)]
    result = apply_ops(profile, ops, SOURCE)
    assert result.profile.work_experience[0].is_current is True
    _roundtrips(result.profile)
