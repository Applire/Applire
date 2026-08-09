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

"""The post-hire intake — "I started a new job" — as an ADR-063 adapter.

#480 PR 6 / ADR-063 amended 2026-08-09 (third entry). This was inventory row 6:
a writer that assigned ``master_profiles.profile_json`` itself, with no reconcile,
no stance guard, no denial floor, no snapshot — and, the defect the matrix made
visible, **no completeness recompute**. It computed ``calculate_completeness()``
for its RESPONSE and never wrote the value back, so the stored score drifted
every time the candidate changed jobs.

Three public entry points, in the shape ADR-063 clause 3 requires:

- ``build_add_role_ops(profile, req)`` — the PURE adapter.
  ``(payload, profile) -> ops``: no I/O, no LLM, no mutation. Validates the
  request all-or-nothing and returns the typed act — one ``AddRole`` plus one
  ``CloseRole`` per closure — together with the ids the door must answer with.
  The ``AddRole`` op mints the new entry's id, so the door knows it before the
  committer runs.

- ``apply_add_role(profile, req)`` — the same act folded through the shared
  applier, in memory and without a database. Kept for the unit tests that pin
  this intake's behaviour and for callers that need the resulting profile.

- ``add_role_to_profile(req, db)`` — the DB-aware door service, shared by
  ``POST /api/profile/roles`` and the MCP ``add_role`` tool. Routes the batch
  through ``commit_ops`` and owns the transaction boundary.
  Raises ``LookupError`` (no profile) and ``AddRoleValidationError`` (invalid
  request).

**What routing changed, deliberately.** The trail and the clocks are no longer
written here: an ``EnrichmentRecord`` append and a ``last_updated`` stamp are
the committer's invariants 3 and 5, and a surviving hand-rolled append would
show the candidate the same act twice. The completeness recompute (invariant 4)
arrives with the routing rather than as a patch. ``project_role_facts`` is no
longer called by hand either — ``apply_ops`` projects unconditionally over the
whole profile, which is strictly more correct than projecting one new entry.

**What routing deliberately did NOT change:** insert-at-0, the absence of any
dupe adjudication, the ``is_current`` tri-state on both halves, the validation
refusals and the two doors' error mappings. Those are the properties that made
``UpsertWork`` an unusable stand-in and produced the ``AddRole`` ruling; see
that op's docstring for the two refutations.
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.profile import MasterProfile
from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.schemas.profile_roles import AddRoleRequest, AddRoleResponse
from applire.services.profile.commit import CommitProvenance, commit_ops
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import AddRole, CloseRole, CommitOp

#: The durable `EnrichmentRecord.source` for this intake — it reaches the
#: candidate's "what changed & why" surface, so it is unchanged by the routing.
SOURCE = "manual_role_add"


class AddRoleValidationError(ValueError):
    """Raised when the request cannot be applied (router should map to HTTP 422)."""


@dataclass
class AddRoleOps:
    """The typed act, plus the ids the door has to answer with."""

    ops: list[CommitOp] = field(default_factory=list)
    new_role_id: str = ""
    closed_role_ids: list[str] = field(default_factory=list)


@dataclass
class AddRoleResult:
    profile: MasterProfileData
    new_role_id: str
    closed_role_ids: list[str]


def build_add_role_ops(profile: MasterProfileData, req: AddRoleRequest) -> AddRoleOps:
    """Validate the request and return it as typed ops. Pure — nothing mutates.

    Validation is all-or-nothing and happens HERE, before anything reaches the
    write path: any failure raises ``AddRoleValidationError`` and no op is
    built, so a caller can never commit half a request. The three refusals are
    the ones this intake has always made — an unknown role id, a role that is
    not open, and a closure dated after the new role starts.
    """
    by_id: dict[str, WorkEntry] = {w.id: w for w in profile.work_experience}
    for entry in req.close_roles:
        we = by_id.get(entry.role_id)
        if we is None:
            raise AddRoleValidationError(f"unknown role_id: {entry.role_id}")
        if we.end_date is not None:
            raise AddRoleValidationError(f"role_id {entry.role_id} is not open")
        if entry.end_date > req.start_date:
            raise AddRoleValidationError(
                f"end_date {entry.end_date} must be on or before new start_date {req.start_date}"
            )

    add = AddRole(
        company=req.company,
        role=req.title,
        start_date=req.start_date,
        location=req.location,
        industry_context=req.industry,
    )
    ops: list[CommitOp] = [add]
    ops.extend(
        # One committer invocation for the whole act, so the two halves can
        # never half-apply. `reason` reaches the receipt's rationale.
        CloseRole(
            target=entry.role_id,
            end_date=entry.end_date,
            reason="superseded_by_a_new_role",
        )
        for entry in req.close_roles
    )
    return AddRoleOps(
        ops=ops,
        new_role_id=add.id,
        closed_role_ids=[entry.role_id for entry in req.close_roles],
    )


def apply_add_role(profile: MasterProfileData, req: AddRoleRequest) -> AddRoleResult:
    """Build the act and fold it through the shared applier. No DB, no trail.

    The trail, the clocks and the completeness recompute belong to
    ``commit_ops`` (invariants 3–5), so this returns the vault content of the
    act and nothing else. ``apply_ops`` never mutates its input; the result
    carries a fresh profile.
    """
    built = build_add_role_ops(profile, req)  # raises AddRoleValidationError
    applied = apply_ops(profile, built.ops, SOURCE)
    return AddRoleResult(
        profile=applied.profile,
        new_role_id=built.new_role_id,
        closed_role_ids=built.closed_role_ids,
    )


async def add_role_to_profile(req: AddRoleRequest, db: AsyncSession) -> AddRoleResponse:
    """Load the latest profile, commit the post-hire act, and answer the door.

    Shared by ``POST /api/profile/roles`` and the MCP ``add_role`` tool, so both
    channels inherit one implementation AND one invariant set (ADR-058 clause 2).

    ``grounding=None``: starting a job is a DIRECT act. There is no turn text to
    adjudicate and the committer never re-adjudicates direct user input
    (§7.4 / ADR-061 clause 2) — which is also why the reconciler's dupe guard
    has no business in front of it.

    ``snapshot=None``: this intake captured no ADR-042 snapshot before and
    captures none now; the omission is a parameter that says so, not a silent
    gap (ADR-063 amendment (5) / #339).
    """
    result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise LookupError("No master profile found")

    profile_data = MasterProfileData.model_validate(record.profile_json)
    # The pure adapter first: an invalid request is refused before anything
    # touches the write path, exactly as it was.
    built = build_add_role_ops(profile_data, req)  # raises AddRoleValidationError

    committed = await commit_ops(
        db,
        built.ops,
        CommitProvenance(source=SOURCE, intake="role_add", actor="candidate"),
        record=record,
        grounding=None,
        snapshot=None,
    )

    # Flush-not-commit (ADR-063 amended clause 6): the POST-HIRE door owns its
    # transaction — dropping this line is a silent no-write, and the candidate
    # is told they changed jobs while the vault still says otherwise.
    await db.commit()

    # TODO US179: manually-added roles get the lean-floor expectation set until a
    # provider is threaded here (fast-follow). Floor fallback is safe (under-asks).
    return AddRoleResponse(
        profile_id=str(record.id),
        new_role_id=built.new_role_id,
        closed_role_ids=built.closed_role_ids,
        # Invariant 4 — the committer recomputed AND stored it; the response now
        # reports the value the vault actually holds, which is what row 6's
        # `compl.` ❌ meant.
        completeness_score=committed.completeness,
    )
