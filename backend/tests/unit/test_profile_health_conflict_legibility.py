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

"""#626 — "CV merge issues not easy to understand".

The reported defect, verbatim: a Health-hub conflict said "There are two
different end dates for one work entry" and asked which to keep, but never
said WHICH work entry. Traced to `_conflict_issue` (services/profile/health.py):
it built its summary from `conflict.section` + `conflict.field` only, never
resolving `Conflict.entity_id` (#218) — added for exactly this reason, per its
own docstring ("`section` + `field` alone cannot address it").

This file pins the resolution: `_conflict_issue` / `_resolve_entity` /
`_entity_label` must name the entry when `entity_id` resolves, must not invent
a label when there is legitimately none (`entity_id is None` — a profile-level
dispute), and must degrade gracefully — not crash — when `entity_id` is set
but no longer resolves against the current profile (a stale id).

Also pins the negative finding from the same investigation: `_confirmation_issue`
does NOT share this defect (the entity is already named in `question`'s own
prose at construction time), so it is deliberately left unchanged.
"""
from __future__ import annotations

from applire.schemas.profile import Conflict, MasterProfileData, PendingConfirmation
from applire.services.profile.health import _conflict_issue, assess_health


def _profile_with_work_entry(**overrides: object) -> MasterProfileData:
    entry = {
        "id": "w-1",
        "company": "Acme Corp",
        "role": "Senior Developer",
        "start_date": "2019-01",
        "end_date": "2019-12",
    }
    entry.update(overrides)
    return MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Anna Bauer"},
            "work_experience": [entry],
        }
    )


def test_conflict_issue_names_the_work_entry_it_belongs_to():
    """The reported defect, verbatim, now fixed: the entity_id (#218) resolves
    against the live profile and the entry is named."""
    profile = _profile_with_work_entry()
    conflict = Conflict(
        section="work_experience",
        field="end_date",
        entity_id="w-1",
        existing_value="2019-12",
        incoming_value="2020-01",
        source="cv_upload",
    )

    issue = _conflict_issue(conflict, profile)

    assert issue.entity_label == "Senior Developer @ Acme Corp"
    assert issue.section == "work_experience"
    assert issue.field == "end_date"
    assert issue.existing_value_display == "2019-12"
    assert issue.incoming_value_display == "2020-01"
    assert issue.incoming_source == "cv_upload"
    # The existing side's provenance is not a stored fact (see the
    # HealthIssue.existing_source docstring) — never fabricated.
    assert issue.existing_source is None
    # The English fallback `summary` is kept for any consumer this issue could
    # not reach, and is itself improved to carry the label.
    assert "Senior Developer @ Acme Corp" in issue.summary
    assert issue.field_ref == "end_date"  # unchanged — existing readers rely on this
    assert issue.source_record_ref == "cv_upload"  # unchanged


def test_conflict_issue_entity_id_none_is_a_legitimate_profile_level_dispute():
    """A `professional_summary` (or `personal_info`) conflict never carries an
    `entity_id` (#218: profile-level disputes have no entity) — must not
    invent a label, and must not crash."""
    profile = MasterProfileData.model_validate(
        {"personal_info": {"full_name": "Anna Bauer"}}
    )
    conflict = Conflict(
        section="professional_summary",
        field="de",
        entity_id=None,
        existing_value="Alte Zusammenfassung",
        incoming_value="Neue Zusammenfassung",
        source="cv_upload",
    )

    issue = _conflict_issue(conflict, profile)

    assert issue.entity_label is None
    assert issue.section == "professional_summary"
    assert issue.field == "de"
    assert issue.existing_value_display == "Alte Zusammenfassung"
    assert issue.incoming_value_display == "Neue Zusammenfassung"
    # No label prefix when there is none to show.
    assert not issue.summary.startswith(":")
    assert "None" not in issue.summary


def test_conflict_issue_stale_entity_id_degrades_gracefully():
    """The entity a conflict was flagged against can be edited/removed after
    the conflict was parked — nothing sweeps `pending_conflicts` when its
    target entity disappears. The health read must not crash on an
    `entity_id` that no longer resolves against the current profile."""
    profile = _profile_with_work_entry()  # only contains id "w-1"
    conflict = Conflict(
        section="work_experience",
        field="end_date",
        entity_id="w-does-not-exist-anymore",
        existing_value="2019-12",
        incoming_value="2020-01",
        source="cv_upload",
    )

    issue = _conflict_issue(conflict, profile)  # must not raise

    assert issue.entity_label is None
    assert issue.existing_value_display == "2019-12"
    assert issue.incoming_value_display == "2020-01"


def test_conflict_issue_resolves_education_entity_label():
    """Every id-bearing section gets a label, not just work_experience —
    education: institution + degree, per the task's own example."""
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Anna Bauer"},
            "education": [
                {"id": "e-1", "institution": "TU München", "degree": "M.Sc. Informatik"}
            ],
        }
    )
    conflict = Conflict(
        section="education",
        field="end_date",
        entity_id="e-1",
        existing_value="2018",
        incoming_value="2019",
        source="cv_upload",
    )

    issue = _conflict_issue(conflict, profile)

    assert issue.entity_label == "M.Sc. Informatik @ TU München"


def test_conflict_issue_resolves_project_and_volunteer_entity_labels():
    """Projects and volunteer activities are the other two sections
    `_apply_flag_conflict` can actually attach an `entity_id` to today (via
    its experience-only `resolve()`) — both must resolve correctly."""
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Anna Bauer"},
            "projects": [{"id": "p-1", "name": "Website Relaunch", "role": "Lead"}],
            "volunteer_activities": [
                {"id": "v-1", "organization": "Rotes Kreuz", "role": "Helfer"}
            ],
        }
    )
    project_conflict = Conflict(
        section="projects", field="end_date", entity_id="p-1",
        existing_value="2020", incoming_value="2021", source="cv_upload",
    )
    volunteer_conflict = Conflict(
        section="volunteer_activities", field="end_date", entity_id="v-1",
        existing_value="2020", incoming_value="2021", source="cv_upload",
    )

    assert _conflict_issue(project_conflict, profile).entity_label == "Lead @ Website Relaunch"
    assert _conflict_issue(volunteer_conflict, profile).entity_label == "Helfer @ Rotes Kreuz"


def test_confirmation_issue_already_names_its_entity_in_the_question():
    """#626 investigation finding: pending confirmations do NOT share the
    conflict thread's defect — there is no separate id to resolve because the
    entity identity already rides in `question`'s own prose. Regression pin
    for that finding (not a behaviour change)."""
    confirmation = PendingConfirmation(
        question=(
            "'Senior Developer at Acme Corp' looks close to an existing "
            "position (Developer at Acme Corp). Is it the same position?"
        ),
        options=["Same position — merge them", "Different — keep both"],
        source="cv_upload",
    )
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Anna Bauer"},
            "metadata": {
                "pending_confirmations": [confirmation.model_dump(mode="json")]
            },
        }
    )

    health = assess_health(profile)

    issues = [i for i in health.issues if i.thread == "confirmation"]
    assert len(issues) == 1
    assert "Acme Corp" in issues[0].summary


def test_assess_health_wires_profile_through_to_conflict_issues():
    """End-to-end through `assess_health` (not just the unit under test
    directly) — the real call site must pass `profile` through."""
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Anna Bauer"},
            "work_experience": [
                {
                    "id": "w-1",
                    "company": "Acme Corp",
                    "role": "Senior Developer",
                    "start_date": "2019-01",
                    "end_date": "2019-12",
                }
            ],
            "metadata": {
                "pending_conflicts": [
                    {
                        "section": "work_experience",
                        "field": "end_date",
                        "entity_id": "w-1",
                        "existing_value": "2019-12",
                        "incoming_value": "2020-01",
                        "source": "cv_upload",
                    }
                ]
            },
        }
    )

    health = assess_health(profile)

    conflict_issues = [i for i in health.issues if i.thread == "conflict"]
    assert len(conflict_issues) == 1
    assert conflict_issues[0].entity_label == "Senior Developer @ Acme Corp"
