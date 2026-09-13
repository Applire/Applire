# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Vault collector #674 — "`photo_url` is not reachable through this door" was
stated in TWO places with no shared constant (ADR-066 cl. 2; PR #663 (D)).

`field_edit.py` RAISED on the section door; `reconcile/apply.py` SKIPPED on the
import door, each with its own literal. The two BEHAVIOURS are deliberately
different and stay different — a section edit is the candidate saying "this is
my personal_info", so refusing is honest, while an import parking "which photo
is right?" would be a question about a file the import never saw. What may not
differ is the SET, and it is the set that would have drifted: adding a second
user-managed field would have protected one door and not the other, and nothing
would have looked wrong.

These tests are parametrised over the constant itself, so a future member is
covered on both doors the moment it is added — the property is "one set", not
"photo_url".
"""
import pytest

from applire.schemas.profile import USER_MANAGED_PERSONAL_INFO_FIELDS


def test_both_doors_read_the_same_object():
    from applire.services.profile.reconcile.apply import (
        _USER_MANAGED_PERSONAL_INFO_FIELDS as import_door,
    )

    assert import_door is USER_MANAGED_PERSONAL_INFO_FIELDS, (
        "the import door re-declared the set instead of importing it — that is "
        "the defect this line was opened for"
    )
    assert "photo_url" in USER_MANAGED_PERSONAL_INFO_FIELDS


@pytest.mark.parametrize("field", sorted(USER_MANAGED_PERSONAL_INFO_FIELDS))
def test_the_section_door_refuses_every_user_managed_field(field):
    from applire.services.profile.field_edit import build_replace_section_op

    with pytest.raises(ValueError) as exc:
        build_replace_section_op("personal_info", {"name": "Marcus Schmidt", field: "x"})
    assert field in str(exc.value)


def test_the_section_door_still_accepts_a_clean_personal_info_edit():
    from applire.services.profile.field_edit import build_replace_section_op

    op = build_replace_section_op("personal_info", {"name": "Marcus Schmidt"})
    assert op.section == "personal_info"
    assert op.value["name"] == "Marcus Schmidt"


@pytest.mark.parametrize("field", sorted(USER_MANAGED_PERSONAL_INFO_FIELDS))
def test_the_import_door_drops_every_user_managed_field_without_a_dispute(field):
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.apply import _apply_set_personal_info
    from applire.services.profile.reconcile.ops import SetPersonalInfo

    profile = MasterProfileData.model_validate(
        {"personal_info": {"name": "Marcus Schmidt", field: "/uploads/mine.png"}}
    )
    changes: list = []
    conflicts: list = []
    _apply_set_personal_info(
        SetPersonalInfo(field=field, value="http://elsewhere.example/theirs.png"),
        profile, "cv_upload", changes, conflicts,
    )

    assert getattr(profile.personal_info, field) == "/uploads/mine.png", "the import overwrote it"
    assert changes == [], "a field the import may not write must leave no change receipt"
    assert conflicts == [], (
        "and no dispute either — the candidate is not the party who would answer it"
    )


def test_a_contested_ordinary_field_still_parks_a_conflict():
    """The guard must be scoped to the set, not to `personal_info` as a whole —
    otherwise it would silence the #602/#620 receipt it sits inside."""
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.apply import _apply_set_personal_info
    from applire.services.profile.reconcile.ops import SetPersonalInfo

    profile = MasterProfileData.model_validate(
        {"personal_info": {"name": "Marcus Schmidt", "email": "marcus@example.com"}}
    )
    changes: list = []
    conflicts: list = []
    _apply_set_personal_info(
        SetPersonalInfo(field="email", value="m.schmidt@example.org"),
        profile, "cv_upload", changes, conflicts,
    )
    assert len(conflicts) == 1 and conflicts[0].field == "email"
