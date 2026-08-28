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

"""#615 — the import doors' carried-predicate (ADR-063 amended 2026-08-28,
second entry of the day).

Unit-level arm tests use small hand-built profiles; the fixture-driven tests
replay the captured #615 record (``tests/files/615_import_witness/fixture.json``)
through the REAL ``apply_ops`` so the merged profile the predicate is checked
against is the genuine output of applying the captured/replayed ops — never a
hand-simulated merge result.
"""
import json
from pathlib import Path

from applire.schemas.profile import (
    EducationEntry,
    MasterProfileData,
    Skill,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import _parse_ops
from applire.services.profile.reconcile.import_witness import (
    compute_import_not_applied,
)

_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = json.loads(
    (_ROOT / "tests" / "files" / "615_import_witness" / "fixture.json").read_text()
)


def _incoming() -> MasterProfileData:
    return MasterProfileData.model_validate(FIXTURE["incoming"])


def _existing_empty() -> MasterProfileData:
    return MasterProfileData.model_validate(FIXTURE["existing_empty"])


def _apply(existing, raw_ops):
    rejected: list[str] = []
    ops = _parse_ops(raw_ops, rejected=rejected)
    return apply_ops(existing, ops, "cv_upload"), rejected


# ---------------------------------------------------------------------------
# Unit-level arms — flat sections (skills, using classify_dupe)
# ---------------------------------------------------------------------------


def test_arm_a_exact_key_match_in_merged_profile_is_carried():
    incoming = MasterProfileData(skills=[Skill(name="Python")])
    merged = MasterProfileData(skills=[Skill(name="Python")])
    items = compute_import_not_applied(incoming, merged, ops=[])
    assert items == []


def test_arm_b_classify_dupe_near_dupe_match_is_carried():
    incoming = MasterProfileData(skills=[Skill(name="Team Leadership and Mentorship")])
    merged = MasterProfileData(skills=[Skill(name="Team Leadership")])
    items = compute_import_not_applied(incoming, merged, ops=[])
    # near-dupe MATCH under the shared skills predicate — not a loss.
    assert items == []


def test_flat_section_no_op_carried_entry_when_nothing_rescues():
    incoming = MasterProfileData(skills=[Skill(name="Kubernetes")])
    merged = MasterProfileData(skills=[Skill(name="Python")])
    items = compute_import_not_applied(incoming, merged, ops=[])
    assert len(items) == 1
    assert items[0].section == "skills"
    assert items[0].reason == "no_op_carried_entry"
    assert "Kubernetes" in items[0].label


def test_arm_c_upsert_op_restating_the_key_is_carried_even_when_parked_ambiguous():
    """A `upsert_education` op can restate an incoming entry's identity and
    still not have landed in `merged` (e.g. it parked as an AMBIGUOUS
    confirmation at apply time) — arm (c) reads that as carried: the fact is
    visible on the confirmation channel, not silently gone."""
    incoming = MasterProfileData(
        education=[EducationEntry(institution="TU München", degree="MSc")]
    )
    merged = MasterProfileData(education=[])  # the op never actually landed
    raw_ops = [
        {"op": "upsert_education", "institution": "TU München", "degree": "MSc"},
    ]
    ops = _parse_ops(raw_ops)
    items = compute_import_not_applied(incoming, merged, ops=ops)
    assert items == []


def test_op_rejected_items_from_raw_rejected_ops():
    items = compute_import_not_applied(
        MasterProfileData(), MasterProfileData(), ops=[],
        rejected_ops=["upsert_skill", "<unknown>"],
    )
    assert len(items) == 2
    assert {i.label for i in items} == {"upsert_skill", "<unknown>"}
    assert all(i.reason == "op_rejected" for i in items)
    assert all(i.section is None for i in items)


# ---------------------------------------------------------------------------
# Unit-level arms — engagement sections (classify_engagement_dupe, MATCH only)
# ---------------------------------------------------------------------------


def test_engagement_arm_b_match_is_carried():
    """Same org, same start MONTH, different role wording — arm (a)'s exact
    (company, role) key fails, but classify_engagement_dupe's date-match
    branch fires regardless of the role text and returns MATCH."""
    incoming = MasterProfileData(
        work_experience=[WorkEntry(company="Acme GmbH", role="Software Engineer", start_date="2019-02")]
    )
    merged = MasterProfileData(
        work_experience=[WorkEntry(company="Acme GmbH", role="Engineer", start_date="2019-02-15")]
    )
    items = compute_import_not_applied(incoming, merged, ops=[])
    assert items == []


def test_engagement_ambiguous_verdict_is_not_carried_and_stays_listed():
    """Same org, but neither the date nor the role confirms one stint —
    classify_engagement_dupe returns AMBIGUOUS, not MATCH, and no op rescues
    it either: the entry stays listed (the caller judges)."""
    incoming = MasterProfileData(
        work_experience=[WorkEntry(company="Acme GmbH", role="Consultant", start_date="2022-01")]
    )
    merged = MasterProfileData(
        work_experience=[WorkEntry(company="Acme GmbH", role="Engineer", start_date="2019-02")]
    )
    items = compute_import_not_applied(incoming, merged, ops=[])
    assert len(items) == 1
    assert items[0].section == "work_experience"
    assert items[0].reason == "no_op_carried_entry"


def test_engagement_arm_c_add_bullets_only_rescues_a_second_source_merge():
    """Refuter B BLOCKER1's exact shape: the model recognises the existing job
    and emits ONLY add_bullets (rule 7's economical form) — no upsert_work
    restating the key at all, and the incoming's own role phrasing ("Controller")
    is abbreviated enough that arms (a)/(b) both fail on role alone. The first
    draft (arms a+b only) would flag this as lost; it is not."""
    existing = MasterProfileData(
        work_experience=[
            WorkEntry(
                id="185b4506-45a8-44ff-9d4f-7f52f2bc9a46",
                company="Schwarzwald Präzision GmbH",
                role="Financial Controller",
                start_date="2019-02",
            )
        ]
    )
    # Second source's own phrasing: abbreviated role, no start_date at all —
    # so classify_engagement_dupe's date-match branch cannot fire either.
    incoming = MasterProfileData(
        work_experience=[
            WorkEntry(company="Schwarzwald Präzision GmbH", role="Controller")
        ]
    )
    raw_ops = [
        {"op": "add_bullets", "target": "185b4506-45a8-44ff-9d4f-7f52f2bc9a46",
         "responsibilities": ["Führte SAP CO/FI Key-User-Schulungen durch."]},
    ]
    ops = _parse_ops(raw_ops)
    applied = apply_ops(existing, ops, "cv_upload")
    # Sanity: the real merge is lossless — the bullet landed on the one entry.
    assert len(applied.profile.work_experience) == 1
    assert applied.profile.work_experience[0].responsibilities

    items = compute_import_not_applied(incoming, applied.profile, ops=ops)
    assert items == [], f"false positive: {items}"


def test_engagement_arm_c_local_ref_resolves_through_an_id_targeted_upsert():
    """The same rescue, but AddBullets targets a LOCAL ref assigned by an
    id-targeted UpsertWork in the SAME batch (prompt rule 3's normal shape),
    not the real id directly."""
    existing = MasterProfileData(
        work_experience=[
            WorkEntry(id="w-real-id", company="Acme GmbH", role="Senior Engineer",
                      start_date="2019-02")
        ]
    )
    incoming = MasterProfileData(
        work_experience=[WorkEntry(company="Acme GmbH", role="Engineer")]
    )
    raw_ops = [
        {"op": "upsert_work", "ref": "w1", "target": "w-real-id",
         "company": "Acme GmbH", "role": "Senior Engineer", "start_date": "2019-02"},
        {"op": "add_bullets", "target": "w1", "responsibilities": ["More detail."]},
    ]
    ops = _parse_ops(raw_ops)
    applied = apply_ops(existing, ops, "cv_upload")

    items = compute_import_not_applied(incoming, applied.profile, ops=ops)
    assert items == [], f"false positive: {items}"


# ---------------------------------------------------------------------------
# Fixture-driven — the captured #615 record and its clean/known-shape siblings
# ---------------------------------------------------------------------------


def test_detector_on_the_captured_record_flags_skills_languages_education():
    incoming = _incoming()
    applied, rejected = _apply(_existing_empty(), FIXTURE["backend6_ops"])
    assert rejected == []
    items = compute_import_not_applied(incoming, applied.profile, ops=[
        op for op in _parse_ops(FIXTURE["backend6_ops"])
    ])
    by_section: dict[str, int] = {}
    for item in items:
        by_section[item.section] = by_section.get(item.section, 0) + 1
    assert by_section == {"skills": 8, "languages": 2, "education": 2}
    assert all(i.reason == "no_op_carried_entry" for i in items)


def test_clean_batch_replay_noid_00_has_no_not_applied_items():
    incoming = _incoming()
    applied, rejected = _apply(_existing_empty(), FIXTURE["replay_noid_00_ops"])
    assert rejected == []
    items = compute_import_not_applied(
        incoming, applied.profile, ops=_parse_ops(FIXTURE["replay_noid_00_ops"])
    )
    assert items == []


def test_local11_sap_split_pins_exactly_one_not_applied_item():
    """The KNOWN false-positive shape (not a bug): the reconciler splits the
    incoming compound skill "SAP CO/FI" into "SAP CO" + "SAP FI" — both halves
    land, so the profile is RICHER, not lossier, but the compound's own label
    is not literally present anywhere."""
    incoming = _incoming()
    applied, rejected = _apply(_existing_empty(), FIXTURE["local11_ops"])
    assert rejected == []
    assert len(applied.profile.skills) == 9
    items = compute_import_not_applied(
        incoming, applied.profile, ops=_parse_ops(FIXTURE["local11_ops"])
    )
    assert len(items) == 1
    assert items[0].section == "skills"
    assert items[0].reason == "no_op_carried_entry"
    assert "SAP CO/FI" in items[0].label
