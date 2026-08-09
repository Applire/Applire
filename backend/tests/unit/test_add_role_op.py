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

"""#480 PR 6 / ADR-063 amended 2026-08-09 (third entry) — `AddRole`.

The post-hire act: *"I started a new job."* PR 6's first pass STOPped here,
because the design assumed `add_role` was expressible with the reconciler's
`UpsertWork` and code contact refuted it twice over:

* **ordering** — `role_add` inserts at index 0 and `_apply_upsert_work`
  appends, and NOTHING in the backend or the frontend sorts
  `work_experience`. Routing through the upsert would render a just-started
  job at the BOTTOM of the CV;
* **identity** — `_apply_upsert_work` runs `classify_engagement_dupe` for any
  entry the reconciler did not target. On an internal promotion (same
  employer, new title) the verdict is AMBIGUOUS: a confirmation is parked and
  **no entry is created**, so the door has no `new_role_id` to return.

The PO ruling (ADR-063's third 2026-08-09 amendment) follows the
`ApplyImportMerge` precedent: when an intake is not expressible in the existing
vocabulary, the ACT becomes its own adapter-only op rather than a bypass
parameter or a widened model-emittable one. The dupe guard exists because
**the LLM owns entity identity** (ADR-046) — for a direct human act the human
owns it, and §7.4's ruling already says the committer never re-adjudicates
direct user input.

So `AddRole` is deliberately narrow: it appends nothing, decides nothing, and
adjudicates nothing. It states one role, at the top, marked current.
"""
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import (
    AddRole,
    CloseRole,
    CommitOp,
    DecisionOp,
    ReconcileOp,
)

_HALLUCINATED_ADD: dict[str, Any] = {
    "op": "add_role",
    "company": "Rheinwerk GmbH",
    "role": "Head of Everything",
    "start_date": "2026-06-01",
}


def _profile(*entries: WorkEntry) -> MasterProfileData:
    return MasterProfileData(work_experience=list(entries))


def _add(**overrides) -> AddRole:
    payload: dict[str, Any] = {
        "company": "Meridian Systems",
        "role": "Principal Engineer",
        "start_date": "2026-06-01",
    }
    payload.update(overrides)
    return AddRole(**payload)


# ── The union split ──────────────────────────────────────────────────────────


def test_reconcile_op_union_refuses_add_role():
    """The reconciler already has `upsert_work` for "this role exists". It does
    not also get the un-adjudicated form, which would let one hallucinated op
    mint a job at the top of the CV with no dupe check in front of it."""
    adapter: TypeAdapter = TypeAdapter(ReconcileOp)
    with pytest.raises(ValidationError):
        adapter.validate_python(_HALLUCINATED_ADD)


def test_decision_op_union_accepts_add_role():
    adapter: TypeAdapter = TypeAdapter(DecisionOp)
    assert isinstance(adapter.validate_python(_HALLUCINATED_ADD), AddRole)


def test_commit_op_union_accepts_add_role():
    adapter: TypeAdapter = TypeAdapter(CommitOp)
    assert isinstance(adapter.validate_python(_HALLUCINATED_ADD), AddRole)


def test_hallucinated_add_role_in_model_output_is_dropped():
    from applire.services.profile.reconcile.engine import _parse_ops

    ops = _parse_ops(
        [_HALLUCINATED_ADD, {"op": "upsert_skill", "name": "Go", "category": "technical"}]
    )

    assert not any(isinstance(o, AddRole) for o in ops)
    assert len(ops) == 1


def test_add_role_requires_a_company_and_a_role():
    with pytest.raises(ValidationError):
        AddRole(company="Meridian Systems")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AddRole(role="Principal Engineer")  # type: ignore[call-arg]


# ── Insert-at-0: the ordering the vault relies on ────────────────────────────


def test_the_new_role_lands_at_the_top():
    """Nothing sorts `work_experience` — array order is what the profile page
    renders and what the CV generator is handed. A just-started job belongs
    first, which is why the reconciler's appending `upsert_work` could not
    stand in for this act."""
    older = WorkEntry(company="Rheinwerk GmbH", role="Automation Engineer")
    result = apply_ops(_profile(older), [_add()], "manual_role_add")

    assert [w.company for w in result.profile.work_experience] == [
        "Meridian Systems",
        "Rheinwerk GmbH",
    ]


def test_the_new_role_is_marked_current():
    """#155 — a just-started role IS the current position, and the tri-state
    says so explicitly rather than leaving a null end date to be re-asked."""
    entry = apply_ops(_profile(), [_add()], "manual_role_add").profile.work_experience[0]

    assert entry.is_current is True
    assert entry.end_date is None


def test_the_op_carries_the_id_it_will_create():
    """The door must answer with `new_role_id`, and it must know that id from a
    PURE adapter — before the committer runs and without scraping receipts."""
    op = _add()
    result = apply_ops(_profile(), [op], "manual_role_add")

    assert result.profile.work_experience[0].id == op.id


def test_the_optional_fields_are_carried_through():
    op = _add(location="Wien", industry_context="Industrial automation")
    entry = apply_ops(_profile(), [op], "manual_role_add").profile.work_experience[0]

    assert entry.location == "Wien"
    assert entry.industry_context == "Industrial automation"


# ── No dupe adjudication: the human owns identity here ───────────────────────


def test_an_internal_promotion_creates_a_second_role_at_the_same_employer():
    """The exact case that blocked the first routing attempt. Through
    `upsert_work` this parks a confirmation and creates NOTHING; the human said
    "I was promoted", so the act is performed and the door has an id to return."""
    old = WorkEntry(
        company="Rheinwerk GmbH",
        role="Automation Engineer",
        start_date="2018-01",
        end_date=None,
        is_current=True,
    )
    op = _add(company="Rheinwerk GmbH", role="Senior Automation Engineer")

    result = apply_ops(_profile(old), [op], "manual_role_add")

    assert len(result.profile.work_experience) == 2
    assert result.profile.work_experience[0].id == op.id
    assert result.pending_confirmations == []


def test_an_exact_repeat_is_still_created_not_merged():
    """Same employer, same title, same start month — `upsert_work` would MATCH
    and merge. This op never merges: two identical-looking stints are the
    human's statement, and silently folding them would lose one."""
    old = WorkEntry(
        company="Meridian Systems", role="Principal Engineer", start_date="2026-06-01"
    )
    result = apply_ops(_profile(old), [_add()], "manual_role_add")

    assert len(result.profile.work_experience) == 2


def test_no_confirmation_is_ever_parked_by_an_add():
    old = WorkEntry(company="Meridian Sytems", role="Principal Enginer")  # near-dupes
    result = apply_ops(_profile(old), [_add()], "manual_role_add")

    assert result.pending_confirmations == []
    assert result.conflicts == []


# ── Reach and receipts ───────────────────────────────────────────────────────


def test_the_add_is_receipted():
    op = _add()
    result = apply_ops(_profile(), [op], "manual_role_add")

    change = next(c for c in result.changes if c.field == f"[{op.id}]")
    assert change.section == "work_experience"
    assert change.action == "added"
    assert change.new_value == {
        "company": "Meridian Systems",
        "role": "Principal Engineer",
        "start_date": "2026-06-01",
    }


def test_the_add_writes_nothing_outside_work_experience():
    """The op's own reach. Everything else on the profile — and in particular
    `metadata.denied_concepts` and `metadata.enrichment_history` — is written by
    the committer's invariants, never by this op."""
    before = MasterProfileData(
        work_experience=[],
        skills=[],
    )
    result = apply_ops(before, [_add()], "manual_role_add")

    after = result.profile.model_dump(mode="json")
    baseline = before.model_dump(mode="json")
    differing = {k for k in after if after[k] != baseline.get(k)}
    assert differing == {"work_experience"}


# ── The whole act, in one batch ──────────────────────────────────────────────


def test_an_add_and_its_closes_travel_as_one_batch():
    """The post-hire act is "I started here AND left there" — one committer
    invocation, receipts for both halves, so the two can never half-apply."""
    old = WorkEntry(
        company="Rheinwerk GmbH",
        role="Automation Engineer",
        start_date="2018-01",
        end_date=None,
        is_current=True,
    )
    op = _add()
    result = apply_ops(
        _profile(old),
        [op, CloseRole(target=old.id, end_date="2026-05-31", reason="new_role_started")],
        "manual_role_add",
    )

    entries = result.profile.work_experience
    assert entries[0].id == op.id
    assert entries[0].is_current is True
    closed = next(w for w in entries if w.id == old.id)
    assert closed.is_current is False
    assert closed.end_date == "2026-05-31"
    fields = {c.field for c in result.changes}
    assert f"[{op.id}]" in fields
    assert f"[{old.id}].end_date" in fields
