# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the add_role mutation logic (no DB)."""
import pytest

from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.schemas.profile_roles import AddRoleRequest, CloseRoleEntry
from applire.services.profile.role_add import (
    AddRoleValidationError,
    apply_add_role,
)


def _profile_with(*entries: WorkEntry) -> MasterProfileData:
    return MasterProfileData(work_experience=list(entries))


def test_inserts_new_role_at_top():
    profile = _profile_with(WorkEntry(company="A", role="Old"))
    req = AddRoleRequest(
        title="New",
        company="B",
        start_date="2026-06-01",
        close_roles=[],
        source="manual",
    )
    result = apply_add_role(profile, req)
    assert result.profile.work_experience[0].company == "B"
    assert result.profile.work_experience[0].role == "New"
    assert result.profile.work_experience[1].company == "A"
    assert result.new_role_id == result.profile.work_experience[0].id


def test_closes_specified_role():
    old = WorkEntry(company="A", role="Lead", start_date="2023-01-01", end_date=None)
    profile = _profile_with(old)
    req = AddRoleRequest(
        title="New",
        company="B",
        start_date="2026-06-01",
        close_roles=[CloseRoleEntry(role_id=old.id, end_date="2026-05-31")],
        source="manual",
    )
    result = apply_add_role(profile, req)
    closed = next(w for w in result.profile.work_experience if w.id == old.id)
    assert closed.end_date == "2026-05-31"
    assert result.closed_role_ids == [old.id]


def test_side_role_case_keeps_existing_role_open():
    open_role = WorkEntry(company="A", role="Day Job", start_date="2023-01-01", end_date=None)
    profile = _profile_with(open_role)
    req = AddRoleRequest(
        title="Founder",
        company="MyStartup",
        start_date="2026-06-01",
        close_roles=[],   # parallel — nothing closes
        source="manual",
    )
    result = apply_add_role(profile, req)
    still_open = next(w for w in result.profile.work_experience if w.id == open_role.id)
    assert still_open.end_date is None
    assert result.closed_role_ids == []


def test_rejects_close_of_unknown_role_id():
    profile = _profile_with(WorkEntry(company="A", role="Lead"))
    req = AddRoleRequest(
        title="New",
        company="B",
        start_date="2026-06-01",
        close_roles=[CloseRoleEntry(role_id="does-not-exist", end_date="2026-05-31")],
        source="manual",
    )
    with pytest.raises(AddRoleValidationError, match="unknown role_id"):
        apply_add_role(profile, req)


def test_rejects_close_of_already_closed_role():
    closed = WorkEntry(company="A", role="Old", end_date="2022-12-31")
    profile = _profile_with(closed)
    req = AddRoleRequest(
        title="New",
        company="B",
        start_date="2026-06-01",
        close_roles=[CloseRoleEntry(role_id=closed.id, end_date="2026-05-31")],
        source="manual",
    )
    with pytest.raises(AddRoleValidationError, match="not open"):
        apply_add_role(profile, req)


def test_rejects_end_date_after_new_start_date():
    open_role = WorkEntry(company="A", role="Old", end_date=None)
    profile = _profile_with(open_role)
    req = AddRoleRequest(
        title="New",
        company="B",
        start_date="2026-06-01",
        close_roles=[CloseRoleEntry(role_id=open_role.id, end_date="2026-07-15")],
        source="manual",
    )
    with pytest.raises(AddRoleValidationError, match="end_date"):
        apply_add_role(profile, req)


def test_appends_enrichment_record():
    profile = _profile_with()
    if profile.metadata is None:
        from applire.schemas.profile import ProfileMetadata
        profile.metadata = ProfileMetadata()
    req = AddRoleRequest(
        title="New", company="B", start_date="2026-06-01",
        close_roles=[], source="manual",
    )
    result = apply_add_role(profile, req)
    history = result.profile.metadata.enrichment_history
    assert len(history) == 1
    assert history[-1].source == "manual_role_add"
    sections = {c.section for c in history[-1].changes}
    assert "work_experience" in sections


def test_new_role_marked_current_and_closed_role_marked_ended():
    # #155 — a just-added (hired) role IS the current position; a closed role has
    # a known end. Keeps the enrich loop from re-asking end_date for either.
    old = WorkEntry(company="A", role="Lead", start_date="2023-01-01", end_date=None)
    profile = _profile_with(old)
    req = AddRoleRequest(
        title="New",
        company="B",
        start_date="2026-06-01",
        close_roles=[CloseRoleEntry(role_id=old.id, end_date="2026-05-31")],
        source="manual",
    )
    result = apply_add_role(profile, req)
    assert result.profile.work_experience[0].is_current is True
    closed = next(w for w in result.profile.work_experience if w.id == old.id)
    assert closed.is_current is False
