# Copyright (C) 2024-2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the add_role intake adapter (no DB).

Since #480 PR 6 this intake is an ADAPTER: `build_add_role_ops` validates the
request and returns the typed act (`AddRole` + one `CloseRole` per closure),
and `apply_add_role` folds that batch through the shared applier. The behaviour
pinned here before the routing is unchanged and deliberately still pinned —
insert-at-0, the closures, the #155 tri-state — because "unchanged" is the
claim the migration makes.

What DID move is the enrichment trail: `apply_add_role` used to append its own
`EnrichmentRecord` and set `last_updated`. Both are the committer's invariants
now (ADR-063 clause 5, invariants 3 + 5), so the adapter must NOT write them —
a second hand-rolled record would double-count the act on the candidate's "what
changed" surface. The trail is pinned where it now lives: at the doors, in
`tests/unit/test_commit_ops_pr6_door_writes.py`.
"""
import pytest

from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.schemas.profile_roles import AddRoleRequest, CloseRoleEntry
from applire.services.profile.reconcile.ops import AddRole, CloseRole
from applire.services.profile.role_add import (
    AddRoleValidationError,
    apply_add_role,
    build_add_role_ops,
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


def test_the_adapter_writes_no_trail_of_its_own():
    """Invariant 3 belongs to the committer. This adapter used to append its own
    `EnrichmentRecord`; routed, a surviving hand-rolled append would show the
    candidate the same act twice and give the undo warning a phantom edit to
    count."""
    profile = _profile_with()
    from applire.schemas.profile import ProfileMetadata

    profile.metadata = ProfileMetadata()
    req = AddRoleRequest(
        title="New", company="B", start_date="2026-06-01",
        close_roles=[], source="manual",
    )
    result = apply_add_role(profile, req)

    assert result.profile.metadata.enrichment_history == []


def test_the_adapter_does_not_move_last_updated():
    """Invariant 5, same reasoning: the committer owns the clocks, so an
    adapter that moved them would make "when did the vault last change" depend
    on which door was used."""
    from datetime import datetime, timezone

    from applire.schemas.profile import ProfileMetadata

    stamp = datetime(2020, 1, 1, tzinfo=timezone.utc)
    profile = _profile_with()
    profile.metadata = ProfileMetadata(last_updated=stamp)
    req = AddRoleRequest(
        title="New", company="B", start_date="2026-06-01",
        close_roles=[], source="manual",
    )
    result = apply_add_role(profile, req)

    assert result.profile.metadata.last_updated == stamp


# ── The adapter is pure, and its output is the typed act ─────────────────────


def test_the_adapter_emits_the_typed_act():
    old = WorkEntry(company="A", role="Lead", start_date="2023-01-01", end_date=None)
    profile = _profile_with(old)
    req = AddRoleRequest(
        title="New",
        company="B",
        start_date="2026-06-01",
        location="Wien",
        industry="Automation",
        close_roles=[CloseRoleEntry(role_id=old.id, end_date="2026-05-31")],
        source="manual",
    )

    built = build_add_role_ops(profile, req)

    assert [type(o) for o in built.ops] == [AddRole, CloseRole]
    add, close = built.ops
    assert (add.company, add.role, add.start_date) == ("B", "New", "2026-06-01")
    assert (add.location, add.industry_context) == ("Wien", "Automation")
    assert built.new_role_id == add.id
    assert close.target == old.id
    assert close.end_date == "2026-05-31"
    assert built.closed_role_ids == [old.id]


def test_the_adapter_leaves_the_profile_untouched():
    """Pure `(payload, profile) -> ops`: no I/O, no LLM, and no mutation. The
    committer is the only thing that changes state."""
    old = WorkEntry(company="A", role="Lead", start_date="2023-01-01", end_date=None)
    profile = _profile_with(old)
    before = profile.model_dump(mode="json")
    req = AddRoleRequest(
        title="New", company="B", start_date="2026-06-01",
        close_roles=[CloseRoleEntry(role_id=old.id, end_date="2026-05-31")],
        source="manual",
    )

    build_add_role_ops(profile, req)

    assert profile.model_dump(mode="json") == before


def test_an_internal_promotion_creates_the_role_and_answers_with_its_id():
    """The door-contract regression that blocked the first routing attempt: same
    employer, new title. Through `upsert_work` this parks a confirmation and
    creates nothing, leaving `new_role_id` with no value."""
    old = WorkEntry(
        company="Rheinwerk GmbH",
        role="Automation Engineer",
        start_date="2018-01",
        end_date=None,
    )
    profile = _profile_with(old)
    req = AddRoleRequest(
        title="Senior Automation Engineer",
        company="Rheinwerk GmbH",
        start_date="2026-06-01",
        close_roles=[CloseRoleEntry(role_id=old.id, end_date="2026-05-31")],
        source="manual",
    )

    result = apply_add_role(profile, req)

    assert len(result.profile.work_experience) == 2
    assert result.new_role_id == result.profile.work_experience[0].id
    assert result.profile.work_experience[0].role == "Senior Automation Engineer"


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
