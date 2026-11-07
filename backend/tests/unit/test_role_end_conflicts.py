# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""UX audit round 4 (Emma, Sonnet 5.5) — a role's end is a fact, not a dispute.

On a four-vintage merge the reconciler raised `flag_conflict` on `is_current`
("true (current position, no end date)" vs "false (ended 2024-02)") and on
`end_date` ("none (current position)" vs "2024-02") instead of writing the end
date. The role stayed "→ heute", and the prose values were later written
verbatim into the boolean field: every answer 409'd on validation.

Pinned here: (1) the end-of-role shape is written, not parked; real disputes
stay; (4) typed values on creation, uncoercible → not admitted; (5) a stored
prose dispute resolves; (3) stored disputes of the shape heal on the next write.
"""
from datetime import date

import pytest

from applire.schemas.profile import (
    Conflict,
    MasterProfileData,
    ProfileMetadata,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import apply_ops, settle_role_end_conflicts
from applire.services.profile.reconcile.ops import FlagConflict, ResolveField
from applire.services.profile.reconcile.role_end import (
    UNREADABLE,
    coerce_conflict_value,
    role_end_verdict,
)

TODAY = date(2026, 11, 7)


def _current_role(**kw) -> WorkEntry:
    payload = {"company": "Mediora Health", "role": "Nurse", "start_date": "2020-02", "is_current": True}
    payload.update(kw)
    return WorkEntry(**payload)


def _sonnet_pair(target: str, ended: str = "2024-02") -> list[FlagConflict]:
    """The two ops Sonnet emitted in round 4, in its order (is_current first)."""
    return [
        FlagConflict(target=target, field="is_current",
                     existing="true (current position, no end date)", incoming=f"false (ended {ended})"),
        FlagConflict(target=target, field="end_date",
                     existing="none (current position)", incoming=ended),
    ]


# ── (1) the end-of-role shape is the fact, written ──────────────────────────────


def test_the_sonnet_pair_writes_the_end_date_and_closes_the_role():
    role = _current_role()
    profile = MasterProfileData(work_experience=[role, _current_role(company="Cerova Clinic", start_date="2024-03")])

    result = apply_ops(profile, _sonnet_pair(role.id), "cv_upload")

    entry = result.profile.work_experience[0]
    assert (entry.end_date, entry.is_current) == ("2024-02", False)
    assert result.conflicts == []
    keys = {(c.field, c.rationale_key) for c in result.changes}
    assert (f"[{role.id}].end_date", "role_ended_by_date") in keys
    assert (f"[{role.id}].is_current", "role_ended_by_date") in keys
    # The parallel current role is untouched (#822: not an "only one current" rule).
    assert result.profile.work_experience[1].is_current is True


def test_end_date_first_order_lands_the_same_way():
    role = _current_role()
    profile = MasterProfileData(work_experience=[role])
    result = apply_ops(profile, list(reversed(_sonnet_pair(role.id))), "cv_upload")
    entry = result.profile.work_experience[0]
    assert (entry.end_date, entry.is_current, result.conflicts) == ("2024-02", False, [])


# ── real disputes stay real ─────────────────────────────────────────────────────


def test_two_different_past_end_dates_stay_a_dispute():
    role = _current_role(end_date="2023-05", is_current=False)
    profile = MasterProfileData(work_experience=[role])
    result = apply_ops(profile, [FlagConflict(target=role.id, field="end_date", existing="2023-05", incoming="2024-02")], "cv_upload")
    assert [(c.field, c.existing_value, c.incoming_value) for c in result.conflicts] == [("end_date", "2023-05", "2024-02")]
    assert result.profile.work_experience[0].end_date == "2023-05"


def test_a_later_source_calling_an_ended_role_current_stays_a_dispute():
    role = _current_role(end_date="2024-02", is_current=False)
    profile = MasterProfileData(work_experience=[role])
    result = apply_ops(
        profile,
        [FlagConflict(target=role.id, field="is_current", existing="false (ended 2024-02)", incoming="true (current position)")],
        "cv_upload",
    )
    assert [(c.field, c.existing_value, c.incoming_value) for c in result.conflicts] == [("is_current", False, True)]
    assert result.profile.work_experience[0].is_current is False


def test_a_future_end_date_is_not_written_as_ended():
    role = _current_role()
    profile = MasterProfileData(work_experience=[role])
    result = apply_ops(profile, [FlagConflict(target=role.id, field="end_date", existing="none", incoming="2027-06")], "cv_upload")
    assert result.profile.work_experience[0].end_date is None
    assert result.profile.work_experience[0].is_current is True


# ── (4) typed values at creation ────────────────────────────────────────────────


def test_a_real_bool_dispute_is_stored_typed_never_as_prose():
    role = _current_role(end_date=None, is_current=True)
    profile = MasterProfileData(work_experience=[role])
    # incoming "not current" WITHOUT any end date: not the fact shape → a real dispute
    result = apply_ops(profile, [FlagConflict(target=role.id, field="is_current", existing="true (current)", incoming="false (no longer there)")], "cv_upload")
    assert [(c.existing_value, c.incoming_value) for c in result.conflicts] == [(True, False)]


def test_an_uncoercible_typed_value_is_not_admitted(caplog):
    role = _current_role()
    profile = MasterProfileData(work_experience=[role])
    with caplog.at_level("WARNING"):
        result = apply_ops(profile, [FlagConflict(target=role.id, field="is_current", existing="true", incoming="maybe part-time")], "cv_upload")
    assert result.conflicts == []
    assert "not admitted" in caplog.text


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("is_current", True, True),
        ("is_current", "false", False),
        ("is_current", "false (ended 2024-02)", False),
        ("is_current", "true (current position, no end date)", True),
        ("is_current", "Ja", True),
        ("is_current", "maybe", UNREADABLE),
        ("is_current", None, UNREADABLE),
        ("end_date", "2024-02", "2024-02"),
        ("end_date", "02/2024", "2024-02"),
        ("end_date", "none (current position)", None),
        ("end_date", "null", None),
        ("end_date", "", None),
        ("end_date", None, None),
        ("end_date", "2024-02 (left for Cerova)", "2024-02"),
        ("end_date", "sometime later", UNREADABLE),
        ("company", "Mediora (old name)", "Mediora (old name)"),
    ],
)
def test_coerce_conflict_value(field, value, expected):
    assert coerce_conflict_value(field, value) is expected or coerce_conflict_value(field, value) == expected


@pytest.mark.parametrize(
    ("field", "entity_end", "incoming", "verdict"),
    [
        ("end_date", None, "2024-02", "fill_end"),
        ("end_date", None, "2026-11", None),  # the current month has not passed
        ("end_date", "2023-05", "2024-02", None),
        ("end_date", None, None, "settled"),  # MD7-14: the entry already has no end date
        ("is_current", "2024-02", False, "settled"),
        ("is_current", None, False, None),
        ("is_current", "2024-02", True, None),
        ("is_current", "2027-01", False, None),
        ("company", None, "X", None),
    ],
)
def test_role_end_verdict(field, entity_end, incoming, verdict):
    assert role_end_verdict(field, entity_end, incoming, TODAY) == verdict


# ── (5) a stored prose dispute resolves (was a 409 on every answer) ─────────────


def _stored(role: WorkEntry, field: str, existing, incoming) -> MasterProfileData:
    profile = MasterProfileData(work_experience=[role])
    profile.metadata = ProfileMetadata(pending_conflicts=[
        Conflict(section="work_experience", field=field, entity_id=role.id,
                 existing_value=existing, incoming_value=incoming, source="cv_upload"),
    ])
    return profile


@pytest.mark.parametrize("resolution", ["existing", "incoming"])
def test_resolving_a_stored_prose_bool_dispute_writes_the_bool(resolution):
    # A dispute of the bad shape that is NOT the fact shape (no end date stated).
    role = _current_role()
    profile = _stored(role, "is_current", "true (current position, no end date)", "false (left)")
    cid = profile.metadata.pending_conflicts[0].conflict_id
    op = ResolveField(conflict_id=cid, target=role.id, section="work_experience", field="is_current", resolution=resolution)
    result = apply_ops(profile, [op], "manual_edit")
    assert result.profile.work_experience[0].is_current is (resolution == "existing")
    assert result.profile.metadata.pending_conflicts == []


# ── (3) stored disputes of the end-of-role shape heal on the next write ─────────


def test_stored_role_end_disputes_heal_on_any_write():
    role = _current_role()
    profile = MasterProfileData(work_experience=[role])
    profile.metadata = ProfileMetadata(pending_conflicts=[
        Conflict(section="work_experience", field="is_current", entity_id=role.id,
                 existing_value="true (current position, no end date)", incoming_value="false (ended 2024-02)", source="cv_upload"),
        Conflict(section="work_experience", field="end_date", entity_id=role.id,
                 existing_value="none (current position)", incoming_value="2024-02", source="cv_upload"),
        Conflict(section="work_experience", field="role", entity_id=role.id,
                 existing_value="Nurse", incoming_value="Senior Nurse", source="cv_upload"),
    ])
    result = apply_ops(profile, [], "manual_edit")
    entry = result.profile.work_experience[0]
    assert (entry.end_date, entry.is_current) == ("2024-02", False)
    assert [c.field for c in result.profile.metadata.pending_conflicts] == ["role"]


def test_settle_counts_and_leaves_unrelated_disputes():
    role = _current_role()
    profile = _stored(role, "end_date", "none (current position)", "2024-02")
    changes: list = []
    assert settle_role_end_conflicts(profile, [], changes, today=TODAY) == 1
    assert settle_role_end_conflicts(profile, [], changes, today=TODAY) == 0


# ── MD7-14 — a dispute the vault already answers is not a question ─────────────
# Round-4 pass 3: the candidate set VidaCare's end date by hand (2020-01, not
# current); the stored dispute "none (current position)" vs "2020-01" stayed.


@pytest.mark.parametrize(
    ("field", "entity_end", "entity_current", "incoming", "verdict"),
    [
        ("end_date", "2020-01", False, "2020-01", "settled"),
        ("end_date", "01.2020", False, "2020-01", "settled"),  # normalised equal
        ("end_date", "2020-01-31", False, "2020-01", None),  # a different precision is a different value
        ("end_date", "2020-01", False, "2020-02", None),  # different → a real dispute
        ("end_date", "2027-06", True, "2027-06", "settled"),  # a future date the entry already holds
        ("end_date", None, True, "2027-06", None),  # a future date the entry does not hold
        ("is_current", None, True, True, "settled"),
        ("is_current", "2020-01", False, False, "settled"),
        ("is_current", None, False, False, "settled"),
        ("is_current", "2027-06", True, True, "settled"),  # ends in the future: still current
        ("is_current", "2020-01", True, True, None),  # "current" on an ended role: the committer closes it
        ("is_current", None, True, False, None),  # different → a real dispute
        ("is_current", "2024-02", False, True, None),
        ("is_current", None, None, True, None),  # the entry states nothing
    ],
)
def test_md7_14_the_verdict_settles_what_the_entry_holds(field, entity_end, entity_current, incoming, verdict):
    assert role_end_verdict(field, entity_end, incoming, TODAY, entity_current) == verdict


def test_md7_14_a_stored_end_date_dispute_the_entry_answers_heals_on_any_write():
    role = _current_role(company="VidaCare", end_date="2020-01", is_current=False)
    profile = _stored(role, "end_date", "none (current position)", "2020-01")
    result = apply_ops(profile, [], "manual_edit")
    entry = result.profile.work_experience[0]
    assert (entry.end_date, entry.is_current) == ("2020-01", False)
    assert result.profile.metadata.pending_conflicts == []
    assert not [c for c in result.changes if c.field == f"[{role.id}].end_date"], "nothing is rewritten"


def test_md7_14_a_stored_dispute_with_a_different_value_stays():
    role = _current_role(company="VidaCare", end_date="2020-01", is_current=False)
    profile = _stored(role, "end_date", "2020-01", "2020-03")
    result = apply_ops(profile, [], "manual_edit")
    assert [(c.field, c.incoming_value) for c in result.profile.metadata.pending_conflicts] == [("end_date", "2020-03")]
    assert result.profile.work_experience[0].end_date == "2020-01"


def test_md7_14_a_stored_is_current_dispute_the_entry_answers_heals():
    role = _current_role()
    profile = _stored(role, "is_current", "false (ended)", "true (current position)")
    assert settle_role_end_conflicts(profile, [], [], today=TODAY) == 1
    assert profile.metadata.pending_conflicts == []
    assert profile.work_experience[0].is_current is True


def test_md7_14_a_new_dispute_restating_the_entry_is_not_admitted():
    role = _current_role(company="VidaCare", end_date="2020-01", is_current=False)
    profile = MasterProfileData(work_experience=[role])
    result = apply_ops(
        profile,
        [FlagConflict(target=role.id, field="end_date", existing="none (current position)", incoming="2020-01")],
        "cv_upload",
    )
    assert result.conflicts == []
    assert result.profile.work_experience[0].end_date == "2020-01"

