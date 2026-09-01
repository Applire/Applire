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

"""#604 — the live interview's conflict card must name the entry, like #626's.

The collector line this closes: the enrichment interview's ``ConflictCard`` was
still showing the entity-less ``field: old vs new`` shape #626 removed from the
Health hub, because it is fed by a *second* mechanism
(``schemas/session.py``'s ``ConflictSummary``, built in ``interview_bridge``)
that never carried ``entity_id``.

These tests pin the seam (``_to_summary``) and the shared resolution — not the
rendering, which the PQ spec asserts.
"""

import pytest

from applire.schemas.profile import Conflict, EducationEntry, MasterProfileData, WorkEntry
from applire.schemas.session import ConflictSummary
from applire.services.profile.reconcile.interview_bridge import _to_summary


def _profile() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(
                id="we-1",
                company="Acme GmbH",
                role="Senior Software Engineer",
                start_date="2020-01",
                end_date="2023-12",
            )
        ],
        education=[
            EducationEntry(id="edu-1", institution="TU München", degree="M.Sc. Informatik")
        ],
    )


def _conflict(**over) -> Conflict:
    base = dict(
        conflict_id="c1",
        section="work_experience",
        field="end_date",
        existing_value="2023-12",
        incoming_value="2024-03",
        source="cv_upload",
        entity_id="we-1",
    )
    base.update(over)
    return Conflict(**base)


def test_summary_names_the_work_entry_the_dispute_hangs_off() -> None:
    summary = _to_summary(_conflict(), _profile())
    assert summary.entity_label == "Senior Software Engineer @ Acme GmbH"
    assert summary.section == "work_experience"
    assert summary.source == "cv_upload"
    # The pre-#604 contract is untouched — the PQ mocks and the MCP payload
    # still find every key they had.
    assert (summary.conflict_id, summary.field) == ("c1", "end_date")
    assert (summary.old_value, summary.new_value) == ("2023-12", "2024-03")


def test_summary_names_an_education_entry_too() -> None:
    summary = _to_summary(
        _conflict(section="education", field="degree", entity_id="edu-1"), _profile()
    )
    assert summary.entity_label == "M.Sc. Informatik @ TU München"


@pytest.mark.parametrize(
    "entity_id, why",
    [
        (None, "a profile-level dispute (#218: professional_summary has no entity)"),
        ("we-gone", "a STALE id — the entry was edited away after the flag"),
    ],
)
def test_unresolvable_entity_renders_the_general_heading_not_a_crash(
    entity_id: str | None, why: str
) -> None:
    summary = _to_summary(_conflict(entity_id=entity_id), _profile())
    assert summary.entity_label is None, why


def test_a_turn_with_no_profile_still_builds_a_summary() -> None:
    """`interview_bridge` passes `None` when it validated no profile (no conflicts).

    Defensive: the label chain must answer "no label", never raise.
    """
    assert _to_summary(_conflict(), None).entity_label is None


def test_both_conflict_surfaces_resolve_through_the_SAME_module() -> None:
    """The structural guard against re-drift — the defect this issue fixes.

    #626 put the ladder inside `health.py`, so the Health hub got it and the
    interview did not. If a future change re-derives either side locally, this
    identity check fails before the two surfaces can disagree again.
    """
    from applire.services.profile import entity_label as shared
    from applire.services.profile import health
    from applire.services.profile.reconcile import interview_bridge

    assert health._resolve_entity is shared.resolve_entity
    assert health._entity_label is shared.entity_label
    assert interview_bridge.label_for is shared.label_for


def test_summary_is_the_schema_the_session_response_declares() -> None:
    assert isinstance(_to_summary(_conflict(), _profile()), ConflictSummary)
