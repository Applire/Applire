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

"""US160 (E033 / ADR-041 amended) — deterministic Profile Health assessment.

``assess_health`` composes the three Tier-2 health sources into one structured
read with no LLM:
  - conflict thread  ← unresolved ``pending_conflicts`` (ADR-013)
  - accuracy thread  ← merge reconciliation delta + low confidence (US161/US162)
  - completeness     ← score + the weighted sections still missing data (US104)
"""
from datetime import datetime, timezone

from applire.schemas.profile import (
    Conflict,
    EnrichmentRecord,
    ImportNotApplied,
    MasterProfileData,
    PersonalInfo,
    ProfessionalSummary,
    ProfileMetadata,
    Skill,
    WorkEntry,
)
from applire.services.profile.health import assess_health


def _profile(*, conflicts=None, enrichments=None, **sections) -> MasterProfileData:
    meta = ProfileMetadata(
        pending_conflicts=conflicts or [],
        enrichment_history=enrichments or [],
    )
    return MasterProfileData(metadata=meta, **sections)


def _merge(reconciliation=None, confidence=None) -> EnrichmentRecord:
    return EnrichmentRecord(
        timestamp=datetime.now(timezone.utc),
        source="cv_upload",
        confidence=confidence,
        reconciliation=reconciliation,
    )


# A profile rich enough that every weighted completeness section is satisfied.
def _full_profile(**overrides) -> MasterProfileData:
    base = dict(
        personal_info=PersonalInfo(name="Marcus Weber", email="m@example.com"),
        professional_summary=ProfessionalSummary(de="Zusammenfassung", en="Summary"),
        work_experience=[WorkEntry(
            company="BMW", role="Engineer",
            start_date="2020-01", end_date="2023-12", achievements=["Led migration"])],
        education=[{"institution": "TU", "degree": "BSc", "field": "CS"}],
        skills=[Skill(name="Python", category="technical")],
        languages=[{"language": "German", "proficiency": "native"}],
        certifications=[{"name": "AWS"}],
        publications=[{"title": "A paper"}],
        volunteer_activities=[{"organization": "Red Cross", "role": "Volunteer"}],
    )
    base.update(overrides)
    return _profile(**base)


class TestConflictThread:
    def test_date_conflict_is_review(self):
        c = Conflict(
            section="work_experience",
            field="start_date",
            existing_value="2020-01",
            incoming_value="2019-06",
            source="cv:audi.pdf",
        )
        health = assess_health(_profile(conflicts=[c]))
        issues = [i for i in health.issues if i.thread == "conflict"]
        assert len(issues) == 1
        assert issues[0].profile_mismatch_severity == "review"
        assert issues[0].field_ref == "start_date"
        assert issues[0].source_record_ref == "cv:audi.pdf"
        assert issues[0].id  # stable, deterministic, present

    def test_cosmetic_conflict_is_info(self):
        c = Conflict(
            section="personal_info",
            field="phone",
            existing_value="111",
            incoming_value="222",
            source="cv:x.pdf",
        )
        health = assess_health(_profile(conflicts=[c]))
        issues = [i for i in health.issues if i.thread == "conflict"]
        assert len(issues) == 1
        assert issues[0].profile_mismatch_severity == "info"

    def test_resolved_conflict_is_excluded(self):
        c = Conflict(
            section="work_experience",
            field="title",
            existing_value="A",
            incoming_value="B",
            source="cv:x.pdf",
            resolved=True,
        )
        health = assess_health(_profile(conflicts=[c]))
        assert [i for i in health.issues if i.thread == "conflict"] == []

    def test_conflict_ids_are_distinct_and_stable(self):
        c1 = Conflict(section="work_experience", field="start_date",
                      existing_value="a", incoming_value="b", source="s1")
        c2 = Conflict(section="work_experience", field="end_date",
                      existing_value="c", incoming_value="d", source="s2")
        prof = _profile(conflicts=[c1, c2])
        ids = {i.id for i in assess_health(prof).issues if i.thread == "conflict"}
        assert len(ids) == 2
        # Stable across re-assessment of the same profile.
        ids2 = {i.id for i in assess_health(prof).issues if i.thread == "conflict"}
        assert ids == ids2


class TestAccuracyThread:
    def test_small_dataloss_is_review(self):
        rec = _merge({"work_experience": {"extracted": 5, "stored": 3, "delta": 2}})
        health = assess_health(_profile(enrichments=[rec]))
        issues = [i for i in health.issues if i.thread == "accuracy"]
        assert len(issues) == 1
        assert issues[0].profile_mismatch_severity == "review"
        assert issues[0].source_record_ref == rec.id

    def test_large_dataloss_is_critical(self):
        rec = _merge({"work_experience": {"extracted": 10, "stored": 3, "delta": 7}})
        issues = [i for i in assess_health(_profile(enrichments=[rec])).issues
                  if i.thread == "accuracy"]
        assert len(issues) == 1
        assert issues[0].profile_mismatch_severity == "critical"

    def test_clean_merge_emits_no_issue(self):
        rec = _merge(
            {"work_experience": {"extracted": 3, "stored": 3, "delta": 0}},
            confidence=0.95,
        )
        assert [i for i in assess_health(_profile(enrichments=[rec])).issues
                if i.thread == "accuracy"] == []

    def test_low_confidence_merge_is_review_even_without_loss(self):
        rec = _merge(confidence=0.4)
        issues = [i for i in assess_health(_profile(enrichments=[rec])).issues
                  if i.thread == "accuracy"]
        assert len(issues) == 1
        assert issues[0].profile_mismatch_severity == "review"

    def test_manual_edit_without_reconciliation_is_clean(self):
        rec = EnrichmentRecord(
            timestamp=datetime.now(timezone.utc), source="manual_edit"
        )
        assert [i for i in assess_health(_profile(enrichments=[rec])).issues
                if i.thread == "accuracy"] == []

    def test_wider_nine_section_reconciliation_and_not_applied_do_not_change_the_mechanism(self):
        """#615 (ADR-041 amended 2026-08-28) — `_accuracy_issue`/
        `classify_reconciliation` are UNCHANGED in mechanism; only their input
        widened (5 -> 9 entities) and `EnrichmentRecord` gained a sibling
        `not_applied` field this thread never reads. A record shaped like the
        captured #615 loss (8 skills + 2 languages + 2 education, delta 12,
        above MERGE_DATALOSS_CRITICAL_THRESHOLD=3) is still a SINGLE
        `critical` accuracy issue naming the affected sections — exactly the
        pre-existing contract, just fed by the richer computation."""
        rec = EnrichmentRecord(
            timestamp=datetime.now(timezone.utc),
            source="cv_upload",
            reconciliation={
                "skills": {"extracted": 8, "stored": 0, "delta": 8},
                "languages": {"extracted": 2, "stored": 0, "delta": 2},
                "education": {"extracted": 2, "stored": 0, "delta": 2},
                "work_experience": {"extracted": 3, "stored": 3, "delta": 0},
                "certifications": {"extracted": 0, "stored": 0, "delta": 0},
                "projects": {"extracted": 0, "stored": 0, "delta": 0},
                "publications": {"extracted": 0, "stored": 0, "delta": 0},
                "volunteer_activities": {"extracted": 0, "stored": 0, "delta": 0},
                "signature_stories": {"extracted": 0, "stored": 0, "delta": 0},
            },
            not_applied=[
                ImportNotApplied(section="skills", label="SAP CO/FI", reason="no_op_carried_entry"),
            ],
        )
        issues = [i for i in assess_health(_profile(enrichments=[rec])).issues
                  if i.thread == "accuracy"]
        assert len(issues) == 1
        assert issues[0].profile_mismatch_severity == "critical"
        assert "education" in issues[0].summary
        assert "languages" in issues[0].summary
        assert "skills" in issues[0].summary
        assert "work_experience" not in issues[0].summary  # delta 0 — not affected


class TestCompleteness:
    def test_score_is_present_and_never_severity_tagged(self):
        health = assess_health(_full_profile())
        assert 0.0 <= health.completeness.score <= 1.0
        # The completeness block is score+gaps only; severity lives on issues[].
        assert not hasattr(health.completeness, "profile_mismatch_severity")

    def test_full_profile_has_no_gaps(self):
        health = assess_health(_full_profile())
        assert health.completeness.gaps == []
        # All floor fields present → work richness = 1.0 → full score across all sections.
        assert health.completeness.score == 1.0

    def test_sparse_profile_lists_missing_sections_as_gaps(self):
        prof = _profile(personal_info=PersonalInfo(name="Marcus Weber"))
        gaps = assess_health(prof).completeness.gaps
        assert "work_experience" in gaps
        assert "education" in gaps
        assert "skills" in gaps
        # A satisfied section is not a gap.
        assert "personal_info" not in gaps


class TestHealthyProfile:
    def test_clean_full_profile_has_zero_issues(self):
        health = assess_health(_full_profile())
        assert health.issues == []

    def test_empty_profile_has_no_issues(self):
        health = assess_health(MasterProfileData())
        assert health.issues == []
        assert health.completeness.score == 0.0
