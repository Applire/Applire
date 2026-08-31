# Copyright (C) 2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""A ``set_field`` op must reach every id-bearing section, not only the three
ExperienceBase ones — and must never fail SILENTLY when it doesn't.

`apply_ops`' `resolve` closure searched `work_experience` / `projects` /
`volunteer_activities` only, and `_apply_set_field` returns on `entity is None`.
A `set_field` aimed at an education, certification, language or publication id
therefore produced NO change, NO conflict, NO `rejected_ops` entry — and the
import witness cannot see it either: `compute_import_not_applied` compares
ENTRIES, so a field left unfilled on an entry that IS present passes its arm (a)
trivially.

The reconciler emits such ops on real runs (#618's LLM log, record 26: four
`set_field` against an education entry), so the effect was a second source's
field enrichment silently not happening on four of the vault's sections.

`resolve` itself stays experience-only on purpose — it also backs `parent`,
`evidence` and `add_bullets`, where an education entry is not a legal referent.
The widening lives in a separate `resolve_any` used by `set_field` alone.
"""

import pytest

from applire.schemas.profile import (
    Certification,
    EducationEntry,
    Language,
    MasterProfileData,
    Publication,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import SetField, UpsertProject


def _profile(**section):
    """Every fixture carries a work entry too, so a test can never pass merely
    because the profile was empty."""
    return MasterProfileData(
        work_experience=[WorkEntry(company="ACME", role="Entwicklerin")], **section
    )


@pytest.mark.parametrize(
    "section,entry,field,value",
    [
        ("education", EducationEntry(institution="Provadis", degree="Fachinformatiker"),
         "end_date", "2005-01"),
        ("certifications", Certification(name="REFA-Grundschein"), "credential_id", "R-123"),
        ("languages", Language(language="Englisch"), "level", "B2"),
        ("publications", Publication(title="Papierlose Fertigung"), "venue", "wt Werkstattstechnik"),
    ],
)
def test_set_field_fills_an_empty_field_on_a_non_experience_section(section, entry, field, value):
    result = apply_ops(
        _profile(**{section: [entry]}),
        [SetField(target=str(entry.id), field=field, value=value)],
        source="linkedin_import",
    )
    assert getattr(getattr(result.profile, section)[0], field) == value


@pytest.mark.parametrize(
    "section,entry,field,value",
    [
        ("education", EducationEntry(institution="Provadis", degree="Fachinformatiker"),
         "end_date", "2005-01"),
        ("certifications", Certification(name="REFA-Grundschein"), "credential_id", "R-123"),
    ],
)
def test_the_change_record_names_the_real_section(section, entry, field, value):
    """`_section_for` returned "" for these kinds, so the audit trail would have
    carried a FieldChange naming no section — a record that cannot be audited."""
    result = apply_ops(
        _profile(**{section: [entry]}),
        [SetField(target=str(entry.id), field=field, value=value)],
        source="linkedin_import",
    )
    assert [c.section for c in result.changes] == [section]


def test_a_work_entry_still_resolves_exactly_as_before():
    """The widening must not disturb the three sections that already worked."""
    prof = _profile()
    work = prof.work_experience[0]
    result = apply_ops(
        prof, [SetField(target=str(work.id), field="location", value="Koblenz")],
        source="linkedin_import",
    )
    assert result.profile.work_experience[0].location == "Koblenz"
    assert [c.section for c in result.changes] == ["work_experience"]


def test_an_unknown_target_is_still_a_no_op_and_not_a_crash():
    """The fix must not turn an unresolvable handle into an exception — a
    reconciler hallucinating an id is a known shape and stays a quiet no-op."""
    result = apply_ops(
        _profile(education=[EducationEntry(institution="Provadis", degree="X")]),
        [SetField(target="00000000-0000-0000-0000-000000000000", field="end_date", value="2005")],
        source="linkedin_import",
    )
    assert result.changes == []


def test_a_project_parent_still_refuses_a_non_experience_referent():
    """`resolve` (parent/evidence/add_bullets) stays experience-only: widening it
    would let a project hang off an education entry. Pinned so a later
    'simplification' does not merge the two resolvers."""
    edu = EducationEntry(institution="Provadis", degree="Fachinformatiker")
    result = apply_ops(
        _profile(education=[edu]),
        [UpsertProject(ref="p1", name="MES-Rollout", parent=str(edu.id))],
        source="linkedin_import",
    )
    assert result.profile.projects[0].associated_experience is None
