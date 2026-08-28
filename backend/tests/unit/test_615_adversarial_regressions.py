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

"""#615 — regressions from the adversarial fresh-eyes pass (2026-08-28, bug-batch-2).

B1 (BLOCKER): the witness keyed engagements on the committer's (org, role) alone,
so a REPEAT STINT — same employer, same role, another year — collapsed into one
`seen` entry; if either copy sat in the merged profile the other was skipped before
any carried-arm ran, `extracted` counted two CV lines as one, and neither
`not_applied` nor the Health hub saw the loss (reproduced through the real
`apply_ops`). Fix: `WITNESS_KEYS` carries `start_date` on the engagement sections
and arm (c)'s target-resolution requires the same start month.

N1 (MINOR): certifications used the generic label dupe in arm (b) while the real
applier merges with `classify_certification_dupe` (®/™ fold, credential-id anchor) —
a cert the applier merged was reported `not_applied`.
"""
from __future__ import annotations

from applire.schemas.profile import Certification, MasterProfileData, ProjectEntry, WorkEntry
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.import_witness import compute_import_not_applied
from applire.services.profile.reconcile.ops import (
    AddBullets,
    UpsertCertification,
    UpsertProject,
    UpsertWork,
)
from applire.services.profile.reconciliation import compute_merge_reconciliation


def _labels(items, section):
    return [i.label for i in items if i.section == section]


def test_b1_repeat_stint_same_company_and_role_is_a_distinct_data_point():
    """Two stints at Foo Corp as Engineer (2013, 2020); only the 2020 one landed."""
    incoming = MasterProfileData(work_experience=[
        WorkEntry(company="Foo Corp", role="Engineer", start_date="2013-01", end_date="2015-06"),
        WorkEntry(company="Foo Corp", role="Engineer", start_date="2020-01", end_date="2022-06"),
    ])
    merged = MasterProfileData(work_experience=[
        WorkEntry(company="Foo Corp", role="Engineer", start_date="2020-01", end_date="2022-06"),
    ])
    ops = [UpsertWork(ref="w1", target=None, company="Foo Corp", role="Engineer",
                      start_date="2020-01", end_date="2022-06")]
    items = compute_import_not_applied(incoming, merged, ops)
    assert _labels(items, "work_experience") == ["Foo Corp / Engineer / 2013-01"], items
    rec = compute_merge_reconciliation(incoming, merged, items)
    assert rec["work_experience"] == {"extracted": 2, "stored": 1, "delta": 1}


def test_b1_add_bullets_against_the_other_stint_does_not_rescue_the_lost_one():
    """Driven through the real apply_ops: an add_bullets targets the pre-existing 2020
    entity; nothing names the 2013 stint — arm (c)'s org rescue must not fire for a
    different start month."""
    existing_entity = WorkEntry(company="Foo Corp", role="Engineer", start_date="2020-01", end_date="2022-06")
    existing = MasterProfileData(work_experience=[existing_entity])
    incoming = MasterProfileData(work_experience=[
        WorkEntry(company="Foo Corp", role="Engineer", start_date="2013-01", end_date="2015-06"),
        WorkEntry(company="Foo Corp", role="Engineer", start_date="2020-01", end_date="2022-06"),
    ])
    ops = [AddBullets(target=existing_entity.id, responsibilities=["Shipped the 2020-era rollout"])]
    applied = apply_ops(existing, ops, source="test")
    assert [w.start_date for w in applied.profile.work_experience] == ["2020-01"]
    items = compute_import_not_applied(incoming, applied.profile, ops)
    assert _labels(items, "work_experience") == ["Foo Corp / Engineer / 2013-01"], items
    rec = compute_merge_reconciliation(incoming, applied.profile, items)
    assert rec["work_experience"] == {"extracted": 2, "stored": 1, "delta": 1}


def test_b1_undated_second_source_entry_is_still_rescued_by_a_targeted_op():
    """The wildcard: an incoming entry WITHOUT a date (LinkedIn text) whose stint the
    model merged into by id must not be listed — the month check only bites when both
    sides carry a date."""
    existing_entity = WorkEntry(company="Foo Corp", role="Engineer", start_date="2020-01")
    existing = MasterProfileData(work_experience=[existing_entity])
    incoming = MasterProfileData(work_experience=[WorkEntry(company="Foo Corp", role="Eng.")])
    ops = [AddBullets(target=existing_entity.id, responsibilities=["Built the thing"])]
    applied = apply_ops(existing, ops, source="test")
    assert compute_import_not_applied(incoming, applied.profile, ops) == []


def test_b1_same_named_projects_with_different_dates_are_distinct():
    incoming = MasterProfileData(projects=[
        ProjectEntry(name="Website Relaunch", role="Lead", start_date="2016-01"),
        ProjectEntry(name="Website Relaunch", role="Contributor", start_date="2021-01"),
    ])
    merged = MasterProfileData(projects=[
        ProjectEntry(name="Website Relaunch", role="Contributor", start_date="2021-01"),
    ])
    ops = [UpsertProject(ref="p1", target=None, name="Website Relaunch", role="Contributor", start_date="2021-01")]
    items = compute_import_not_applied(incoming, merged, ops)
    assert _labels(items, "projects") == ["Website Relaunch / 2016-01"], items
    rec = compute_merge_reconciliation(incoming, merged, items)
    assert rec["projects"] == {"extracted": 2, "stored": 1, "delta": 1}


def test_n1_certification_merged_by_the_cert_aware_dupe_is_carried():
    """The applier folds 'ITIL® Foundation' into 'ITIL Foundation Level'; the op the
    model emitted is neither string verbatim — arm (b) must use the same instrument."""
    merged = MasterProfileData(certifications=[
        Certification(name="ITIL Foundation Level", issuing_organization="AXELOS"),
    ])
    incoming = MasterProfileData(certifications=[Certification(name="ITIL® Foundation")])
    ops = [UpsertCertification(name="ITIL Foundation", issuing_organization="AXELOS")]
    assert compute_import_not_applied(incoming, merged, ops) == []
