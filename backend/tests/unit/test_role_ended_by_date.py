# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#822 (ADR-063 amended 2026-11-07) — a role is never stored both ended and
current.

UX audit round 3 (Emma, four CV vintages merged): an older vintage marked a role
current, a newer one closed it with an end date, and `_apply_upsert_work`'s
fill-only merge filled the end date but kept `is_current = True` (a populated
flag is never "empty"). The profile then showed three jobs as "→ heute".

The fix is a committer-owned closing pass at the end of `apply_ops`: an entry
whose stated end date has passed has its flag cleared, with a receipt. It is a
date fact (ADR-062 cl. 1), not an "only one current role" rule — a parallel role
without an end date stays open.
"""
from datetime import date

import pytest

from applire.schemas.profile import MasterProfileData, ProjectEntry, WorkEntry
from applire.services.profile.reconcile.apply import apply_ops, close_ended_roles
from applire.services.profile.reconcile.ops import UpsertWork

TODAY = date(2026, 11, 7)


def _role(company: str, **kw) -> WorkEntry:
    payload = {"company": company, "role": "Product Lead", "start_date": "2016-04"}
    payload.update(kw)
    return WorkEntry(**payload)


# ── The seam the audit hit: a newer vintage's end date merges into an old "current" ──


def test_upsert_work_merge_with_an_end_date_clears_a_stale_current_flag():
    existing = _role("Northwind Health", is_current=True)
    profile = MasterProfileData(work_experience=[existing])
    op = UpsertWork(
        ref="w1", target=existing.id, company="Northwind Health", role="Product Lead",
        start_date="2016-04", end_date="2020-01", is_current=False,
    )

    result = apply_ops(profile, [op], "cv_upload")

    entry = result.profile.work_experience[0]
    assert entry.end_date == "2020-01"
    assert entry.is_current is False
    receipts = [c for c in result.changes if c.field == f"[{entry.id}].is_current"]
    assert len(receipts) == 1
    assert receipts[0].old_value is True and receipts[0].new_value is False
    assert receipts[0].rationale_key == "role_ended_by_date"


def test_merge_without_an_incoming_flag_still_clears_it():
    """The incoming op need not say `is_current` — the end date alone settles it."""
    existing = _role("Northwind Health", is_current=True)
    profile = MasterProfileData(work_experience=[existing])
    op = UpsertWork(ref="w1", target=existing.id, company="Northwind Health", end_date="2020-01")

    result = apply_ops(profile, [op], "cv_upload")

    assert result.profile.work_experience[0].is_current is False


def test_a_parallel_role_without_an_end_date_stays_current():
    """Not an "only one current role" rule (ADR-063 2026-08-09 note)."""
    main_job = _role("Northwind Health", is_current=True)
    side_job = _role("Lecturer at a university", start_date="2021-10", is_current=True)
    profile = MasterProfileData(work_experience=[main_job, side_job])

    result = apply_ops(profile, [], "cv_upload")

    assert [w.is_current for w in result.profile.work_experience] == [True, True]
    assert not any(c.field.endswith(".is_current") for c in result.changes)


def test_a_role_already_stored_with_both_is_repaired_by_any_write():
    """Every commit re-establishes the invariant, whatever the batch touched."""
    stale = _role("Northwind Health", end_date="2020-01", is_current=True)
    profile = MasterProfileData(work_experience=[stale])

    result = apply_ops(profile, [], "interview")

    assert result.profile.work_experience[0].is_current is False


# ── The predicate: "ended" at the precision the date states ────────────────


@pytest.mark.parametrize(
    "end_date, expected_current",
    [
        ("2020-01", False),          # a past month
        ("2026-10", False),          # last month
        ("2026-11", True),           # this month: still running, conservative
        ("2026-12", True),           # future month: a fixed-term contract
        ("2026-11-06", False),       # a past day
        ("2026-11-07", True),        # today: still running
        ("2027-03-31", True),        # a future day
        ("2025", False),             # a past year
        ("2026", True),              # this year: ambiguous, left alone
        ("heute", True),             # unparseable: left alone
        ("present", True),
    ],
)
def test_the_ended_predicate(end_date, expected_current):
    profile = MasterProfileData(work_experience=[_role("Acme", end_date=end_date, is_current=True)])
    changes: list = []

    close_ended_roles(profile, changes, today=TODAY)

    assert profile.work_experience[0].is_current is expected_current
    assert len(changes) == (0 if expected_current else 1)


def test_unknown_and_ended_flags_are_never_touched():
    profile = MasterProfileData(work_experience=[
        _role("A", end_date="2020-01", is_current=None),
        _role("B", end_date="2020-01", is_current=False),
        _role("C", end_date=None, is_current=True),
    ])
    changes: list = []

    close_ended_roles(profile, changes, today=TODAY)

    assert [w.is_current for w in profile.work_experience] == [None, False, True]
    assert changes == []


def test_projects_with_a_past_end_date_are_closed_too():
    project = ProjectEntry(name="Telehealth rollout", start_date="2019-01", end_date="2019-09", is_current=True)
    profile = MasterProfileData(projects=[project])
    changes: list = []

    close_ended_roles(profile, changes, today=TODAY)

    assert profile.projects[0].is_current is False
    assert changes[0].section == "projects"


def test_the_pass_is_idempotent():
    profile = MasterProfileData(work_experience=[_role("Acme", end_date="2020-01", is_current=True)])
    first: list = []
    second: list = []

    close_ended_roles(profile, first, today=TODAY)
    close_ended_roles(profile, second, today=TODAY)

    assert len(first) == 1 and second == []
