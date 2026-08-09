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

"""#480 PR 5 — `ResolveField`, the AUTHORISED overwrite (design §4.2).

`_apply_set_field` is fill-only on purpose: the reconciler may fill an empty
field but must never overwrite a populated one — a real disagreement goes to
the conflict channel instead. `ResolveField` is the one op allowed to overwrite,
and what authorises it is **not a flag on the op**: the `conflict_id` must
resolve to an OPEN conflict parked on the profile. The authority is a dispute
the system raised and the candidate answered.

The #218 bullet-list surgery moved out of `services/profile/__init__.py` and
into the applier with this PR, which is what makes it unit-testable at all —
before this every one of its behaviours needed a database and a service call.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from applire.schemas.profile import Conflict, MasterProfileData, ProfileMetadata
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import (
    CommitOp,
    ReconcileOp,
    ResolveConfirmation,
    ResolveField,
    SetField,
)
from applire.services.profile.resolution import build_resolve_field_op

SOURCE = "manual_edit"

_WORK_A = "11111111-1111-1111-1111-111111111111"
_WORK_B = "22222222-2222-2222-2222-222222222222"
_PROJECT = "33333333-3333-3333-3333-333333333333"

_OLD_BULLET = "Reduced processing time by 60%"
_NEW_BULLET = "Reduced processing time by 80%"


def _profile(conflicts: list[Conflict] | None = None) -> MasterProfileData:
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Max Muster", "email": "max@example.invalid"},
            "work_experience": [
                {
                    "id": _WORK_A,
                    "company": "Acme GmbH",
                    "role": "Engineer",
                    "start_date": "2020-01",
                    "achievements": [_OLD_BULLET],
                },
                {
                    "id": _WORK_B,
                    "company": "Acme GmbH",
                    "role": "Analyst",
                    "start_date": "2016-01",
                    "achievements": [_OLD_BULLET],
                },
            ],
            "projects": [
                {"id": _PROJECT, "name": "Pipeline", "achievements": [_OLD_BULLET]}
            ],
        }
    )
    profile.metadata = ProfileMetadata(pending_conflicts=list(conflicts or []))
    return profile


def _conflict(**kwargs: Any) -> Conflict:
    payload: dict[str, Any] = {
        "conflict_id": str(uuid.uuid4()),
        "section": "personal_info",
        "field": "name",
        "existing_value": "Max Muster",
        "incoming_value": "Markus Brandt",
        "source": "cv_upload",
    }
    payload.update(kwargs)
    return Conflict(**payload)


def _resolve(profile: MasterProfileData, op: ResolveField):
    return apply_ops(profile, [op], SOURCE)


# ── 1. The union split: a resolution is an act, never a model utterance ───────


def test_resolve_field_is_adapter_only():
    """A resolution OVERWRITES attested data. If the model could emit one, a
    hallucinated `resolve_field` would be a silent authorised overwrite."""
    payload: dict[str, Any] = {
        "op": "resolve_field",
        "conflict_id": str(uuid.uuid4()),
        "section": "personal_info",
        "field": "name",
        "resolution": "incoming",
    }
    with pytest.raises(ValidationError):
        TypeAdapter(ReconcileOp).validate_python(payload)
    assert isinstance(TypeAdapter(CommitOp).validate_python(payload), ResolveField)


def test_resolve_confirmation_is_adapter_only():
    payload: dict[str, Any] = {
        "op": "resolve_confirmation",
        "confirmation_id": str(uuid.uuid4()),
        "chosen_option": "Same role",
    }
    with pytest.raises(ValidationError):
        TypeAdapter(ReconcileOp).validate_python(payload)
    assert isinstance(
        TypeAdapter(CommitOp).validate_python(payload), ResolveConfirmation
    )


def test_hallucinated_resolution_ops_are_dropped_at_the_parse_seam():
    from applire.services.profile.reconcile.engine import _parse_ops

    ops = _parse_ops(
        [
            {
                "op": "resolve_field",
                "conflict_id": str(uuid.uuid4()),
                "section": "personal_info",
                "field": "name",
                "resolution": "incoming",
            },
            {"op": "resolve_confirmation", "confirmation_id": "x", "chosen_option": "y"},
            {"op": "upsert_skill", "name": "Go", "category": "technical"},
        ]
    )
    assert not any(isinstance(o, (ResolveField, ResolveConfirmation)) for o in ops)
    assert len(ops) == 1


# ── 2. The load-bearing guard: an OPEN dispute, or nothing happens ────────────


def test_an_unknown_conflict_id_writes_nothing():
    """The authority is the dispute. No dispute, no authorised overwrite —
    otherwise `ResolveField` degenerates into a free overwrite primitive."""
    profile = _profile([])
    op = ResolveField(
        conflict_id=str(uuid.uuid4()),
        section="personal_info",
        field="name",
        value="Markus Brandt",
        resolution="manual",
    )
    result = _resolve(profile, op)
    assert result.profile.personal_info.name == "Max Muster"
    assert result.changes == []


def test_an_already_resolved_conflict_writes_nothing():
    """Answering the same dispute twice must not re-authorise a second
    overwrite — a resolved conflict is spent authority."""
    conflict = _conflict(resolved=True)
    profile = _profile([conflict])
    op = ResolveField(
        conflict_id=conflict.conflict_id,
        section="personal_info",
        field="name",
        value="Someone Else",
        resolution="manual",
    )
    result = _resolve(profile, op)
    assert result.profile.personal_info.name == "Max Muster"
    assert result.changes == []


def test_a_resolution_that_names_a_different_field_than_the_dispute_is_refused():
    """The op must describe the dispute it claims to resolve. Otherwise one
    open conflict about `name` would authorise an overwrite of `email`."""
    conflict = _conflict(field="name")
    profile = _profile([conflict])
    op = ResolveField(
        conflict_id=conflict.conflict_id,
        section="personal_info",
        field="email",
        value="attacker@example.invalid",
        resolution="manual",
    )
    result = _resolve(profile, op)
    assert result.profile.personal_info.email == "max@example.invalid"
    assert result.changes == []
    # …and the dispute is still open, because nothing was answered.
    assert [c.conflict_id for c in result.profile.metadata.pending_conflicts] == [
        conflict.conflict_id
    ]


def test_a_resolution_that_names_a_different_entity_than_the_dispute_is_refused():
    conflict = _conflict(
        section="work_experience",
        field="company",
        entity_id=_WORK_A,
        existing_value="Acme GmbH",
        incoming_value="Acme AG",
    )
    profile = _profile([conflict])
    op = ResolveField(
        conflict_id=conflict.conflict_id,
        target=_WORK_B,
        section="work_experience",
        field="company",
        resolution="incoming",
    )
    result = _resolve(profile, op)
    assert [w.company for w in result.profile.work_experience] == [
        "Acme GmbH",
        "Acme GmbH",
    ]
    assert result.changes == []


def test_metadata_is_unreachable_through_a_resolution():
    """`metadata` holds `denied_concepts` and `enrichment_history`. A dispute
    that names it must never become a write — the same structural refusal
    `ReplaceSection` and `SetProfileMeta` make."""
    conflict = _conflict(section="metadata", field="denied_concepts")
    profile = _profile([conflict])
    profile.metadata.denied_concepts = []
    op = ResolveField(
        conflict_id=conflict.conflict_id,
        section="metadata",
        field="denied_concepts",
        value=[],
        resolution="manual",
    )
    result = _resolve(profile, op)
    assert result.changes == []
    # The dispute stays open — refusing the write must not silently swallow it.
    assert [c.conflict_id for c in result.profile.metadata.pending_conflicts] == [
        conflict.conflict_id
    ]


# ── 3. The authorised overwrite itself ────────────────────────────────────────


def test_resolution_overwrites_a_populated_field_where_set_field_refuses():
    """The one-line statement of what this op is FOR, pinned as a contrast."""
    conflict = _conflict(
        section="work_experience",
        field="company",
        entity_id=_WORK_A,
        existing_value="Acme GmbH",
        incoming_value="Acme AG",
    )
    profile = _profile([conflict])

    # `SetField` is fill-only: a populated field is left alone.
    refused = apply_ops(
        profile, [SetField(target=_WORK_A, field="company", value="Acme AG")], SOURCE
    )
    assert refused.profile.work_experience[0].company == "Acme GmbH"

    allowed = _resolve(
        profile,
        build_resolve_field_op(conflict, resolution="incoming", value=None),
    )
    by_id = {w.id: w for w in allowed.profile.work_experience}
    assert by_id[_WORK_A].company == "Acme AG"
    assert by_id[_WORK_B].company == "Acme GmbH"


def test_resolution_clears_the_dispute_from_the_pending_list():
    conflict = _conflict()
    other = _conflict(field="email")
    profile = _profile([conflict, other])
    result = _resolve(
        profile, build_resolve_field_op(conflict, resolution="incoming", value=None)
    )
    assert result.profile.personal_info.name == "Markus Brandt"
    assert [c.conflict_id for c in result.profile.metadata.pending_conflicts] == [
        other.conflict_id
    ]


def test_resolution_is_receipted_with_both_sides():
    conflict = _conflict()
    profile = _profile([conflict])
    result = _resolve(
        profile, build_resolve_field_op(conflict, resolution="incoming", value=None)
    )
    (change,) = result.changes
    assert change.section == "personal_info"
    assert change.field == "name"
    assert change.action == "updated"
    assert change.old_value == "Max Muster"
    assert change.new_value == "Markus Brandt"


def test_existing_keeps_the_stored_value():
    conflict = _conflict()
    profile = _profile([conflict])
    result = _resolve(
        profile, build_resolve_field_op(conflict, resolution="existing", value=None)
    )
    assert result.profile.personal_info.name == "Max Muster"
    assert result.profile.metadata.pending_conflicts == []


def test_manual_writes_the_candidates_own_value():
    conflict = _conflict()
    profile = _profile([conflict])
    result = _resolve(
        profile,
        build_resolve_field_op(conflict, resolution="manual", value="Max Brandt"),
    )
    assert result.profile.personal_info.name == "Max Brandt"


# ── 4. The #218 bullet surgery, unit-testable for the first time ──────────────


def test_bullet_resolution_rewrites_only_the_named_entrys_bullet():
    conflict = _conflict(
        section="work_experience",
        field="achievements",
        entity_id=_WORK_A,
        existing_value=_OLD_BULLET,
        incoming_value=_NEW_BULLET,
    )
    profile = _profile([conflict])
    result = _resolve(
        profile, build_resolve_field_op(conflict, resolution="incoming", value=None)
    )
    by_id = {w.id: w for w in result.profile.work_experience}
    assert by_id[_WORK_A].achievements == [_NEW_BULLET]
    # The same-worded bullet on another role is untouched.
    assert by_id[_WORK_B].achievements == [_OLD_BULLET]


def test_bullet_resolution_drops_the_losing_variant_when_both_are_stored():
    conflict = _conflict(
        section="work_experience",
        field="achievements",
        entity_id=_WORK_A,
        existing_value=_OLD_BULLET,
        incoming_value=_NEW_BULLET,
    )
    profile = _profile([conflict])
    profile.work_experience[0].achievements = [_OLD_BULLET, "Unrelated win", _NEW_BULLET]
    result = _resolve(
        profile, build_resolve_field_op(conflict, resolution="incoming", value=None)
    )
    assert result.profile.work_experience[0].achievements == [
        _NEW_BULLET,
        "Unrelated win",
    ]


def test_bullet_resolution_works_on_a_project():
    conflict = _conflict(
        section="projects",
        field="achievements",
        entity_id=_PROJECT,
        existing_value=_OLD_BULLET,
        incoming_value=_NEW_BULLET,
    )
    profile = _profile([conflict])
    result = _resolve(
        profile, build_resolve_field_op(conflict, resolution="incoming", value=None)
    )
    assert result.profile.projects[0].achievements == [_NEW_BULLET]


def test_a_conflict_without_an_entity_id_falls_back_to_value_matching():
    """Conflicts parked before `entity_id` existed carry no identity — the
    pre-#218 behaviour (first entry still holding the old value) is preserved."""
    conflict = _conflict(
        section="work_experience",
        field="achievements",
        entity_id=None,
        existing_value=_OLD_BULLET,
        incoming_value=_NEW_BULLET,
    )
    profile = _profile([conflict])
    result = _resolve(
        profile, build_resolve_field_op(conflict, resolution="incoming", value=None)
    )
    by_id = {w.id: w for w in result.profile.work_experience}
    assert by_id[_WORK_A].achievements == [_NEW_BULLET]
    assert by_id[_WORK_B].achievements == [_OLD_BULLET]


def test_a_field_the_schema_has_no_slot_for_is_a_quiet_no_op():
    """The `field` on a conflict is the reconciler model's own string. One the
    schema cannot hold must resolve quietly, never raise."""
    conflict = _conflict(
        section="work_experience",
        field="outcomes",
        entity_id=_WORK_A,
        existing_value=_OLD_BULLET,
        incoming_value=_NEW_BULLET,
    )
    profile = _profile([conflict])
    result = _resolve(
        profile, build_resolve_field_op(conflict, resolution="incoming", value=None)
    )
    by_id = {w.id: w for w in result.profile.work_experience}
    assert by_id[_WORK_A].achievements == [_OLD_BULLET]
    # The dispute is still answered — the candidate did decide.
    assert result.profile.metadata.pending_conflicts == []


def test_a_conflict_whose_entity_could_not_be_resolved_still_closes():
    """`_apply_flag_conflict` records `section=""` when the target did not
    resolve. There is nowhere to write, but the answer must still land."""
    conflict = _conflict(section="", field="role", existing_value="A", incoming_value="B")
    profile = _profile([conflict])
    result = _resolve(
        profile, build_resolve_field_op(conflict, resolution="incoming", value=None)
    )
    assert result.profile.metadata.pending_conflicts == []


# ── 5. The pure adapter ───────────────────────────────────────────────────────


def test_adapter_refuses_an_unknown_resolution_with_the_doors_message():
    conflict = _conflict()
    with pytest.raises(ValueError, match="Invalid resolution"):
        build_resolve_field_op(conflict, resolution="whatever", value=None)


def test_adapter_carries_the_disputes_own_identity():
    conflict = _conflict(
        section="work_experience", field="company", entity_id=_WORK_A
    )
    op = build_resolve_field_op(conflict, resolution="existing", value=None)
    assert op.conflict_id == conflict.conflict_id
    assert op.section == "work_experience"
    assert op.field == "company"
    assert op.target == _WORK_A
    assert op.resolution == "existing"
