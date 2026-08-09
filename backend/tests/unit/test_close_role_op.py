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

"""#480 PR 6 / ADR-063 clause 8(e) — `CloseRole`, the act of ending a role.

ADR-063's 2026-07-29 amendment named three writes the op vocabulary could not
express; **"a boolean flip (`role_add` closing a role)"** is this one.
`_apply_set_field` is fill-only by design (`if not _is_empty(current): return`),
so nothing in the vocabulary could move a populated `is_current` from `True` to
`False` — and the design (#480 §4.3) rejected the obvious primitive: a generic
`SetBool` is a *power* primitive that any future caller could reach for. The op
is therefore named for the ACT.

What this file pins, in the order the design states it:

* **the union split** — `CloseRole` is adapter-only (`DecisionOp`), so a
  hallucinated `{"op": "close_role", …}` cannot reach the applier. Ending a
  role is a negative statement about the candidate's present ("you no longer
  work there"); a model that could emit it could retire a live job nobody
  retired;
* **the #155 tri-state, in ONE place** — `None` unknown / `True` current /
  `False` known-ended. Closing means *known ended*, and that is true whether or
  not a date is known. The two facts are recorded separately, so an undated
  close still leaves the end-date gap open instead of hiding it;
* **fill-only for the date, authoritative for the flag** — the flip IS the act,
  so `is_current` is written unconditionally; re-DATING an already-dated role is
  a correction, which is `ResolveField`/`ReplaceSection` territory, not a close.
"""
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from applire.schemas.profile import (
    MasterProfileData,
    ProjectEntry,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import (
    CloseRole,
    CommitOp,
    DecisionOp,
    ReconcileOp,
)

_HALLUCINATED_CLOSE: dict[str, Any] = {
    "op": "close_role",
    "target": "w1",
    "end_date": "2026-05-31",
    "reason": "superseded_by_new_role",
}


def _profile(*entries: WorkEntry, projects: list[ProjectEntry] | None = None):
    return MasterProfileData(
        work_experience=list(entries), projects=list(projects or [])
    )


def _open_role(**overrides) -> WorkEntry:
    payload: dict[str, Any] = {
        "company": "Rheinwerk GmbH",
        "role": "Automation Engineer",
        "start_date": "2018-01",
        "end_date": None,
        "is_current": True,
    }
    payload.update(overrides)
    return WorkEntry(**payload)


def _close(target: str, **overrides) -> CloseRole:
    payload: dict[str, Any] = {
        "target": target,
        "end_date": "2026-05-31",
        "reason": "superseded_by_new_role",
    }
    payload.update(overrides)
    return CloseRole(**payload)


# ── The union split — adapter-only, exactly like its DecisionOp siblings ──────


def test_reconcile_op_union_refuses_close_role():
    """The model-emittable union must never carry it: a hallucinated close
    would retire a job the candidate still holds."""
    adapter: TypeAdapter = TypeAdapter(ReconcileOp)
    with pytest.raises(ValidationError):
        adapter.validate_python(_HALLUCINATED_CLOSE)


def test_decision_op_union_accepts_close_role():
    adapter: TypeAdapter = TypeAdapter(DecisionOp)
    assert isinstance(adapter.validate_python(_HALLUCINATED_CLOSE), CloseRole)


def test_commit_op_union_accepts_close_role():
    adapter: TypeAdapter = TypeAdapter(CommitOp)
    assert isinstance(adapter.validate_python(_HALLUCINATED_CLOSE), CloseRole)


def test_hallucinated_close_role_in_model_output_is_dropped():
    """The parse seam, where raw model JSON becomes ops."""
    from applire.services.profile.reconcile.engine import _parse_ops

    ops = _parse_ops(
        [_HALLUCINATED_CLOSE, {"op": "upsert_skill", "name": "Go", "category": "technical"}]
    )

    assert not any(isinstance(o, CloseRole) for o in ops)
    assert len(ops) == 1


def test_close_role_requires_a_target():
    with pytest.raises(ValidationError):
        CloseRole(reason="superseded_by_new_role")  # type: ignore[call-arg]


def test_close_role_requires_a_reason():
    """An adapter-only act states WHY; the reason reaches the candidate's
    "what changed" surface as the receipt's rationale."""
    with pytest.raises(ValidationError):
        CloseRole(target="w1", end_date="2026-05-31")  # type: ignore[call-arg]


def test_close_role_end_date_is_optional():
    op = CloseRole(target="w1", reason="left_the_company")
    assert op.end_date is None


# ── The #155 tri-state, in one place ─────────────────────────────────────────


def test_closing_a_role_marks_it_known_ended_and_dates_it():
    role = _open_role()
    result = apply_ops(_profile(role), [_close(role.id)], "manual_role_add")

    closed = result.profile.work_experience[0]
    assert closed.is_current is False
    assert closed.end_date == "2026-05-31"


def test_closing_flips_is_current_the_fill_only_rule_could_not_reach():
    """The whole reason this op exists (ADR-063 amended 2026-07-29, finding 2):
    `is_current=True` is a POPULATED field, so `set_field` returns early and the
    flag stays `True` forever. `CloseRole` is authoritative about the flag."""
    from applire.services.profile.reconcile.ops import SetField

    role = _open_role()
    viaset = apply_ops(
        _profile(role.model_copy(deep=True)),
        [SetField(target=role.id, field="is_current", value=False)],
        "manual_role_add",
    )
    assert viaset.profile.work_experience[0].is_current is True  # refused: fill-only

    viaclose = apply_ops(
        _profile(role.model_copy(deep=True)), [_close(role.id)], "manual_role_add"
    )
    assert viaclose.profile.work_experience[0].is_current is False


def test_closing_without_a_date_still_records_known_ended():
    """`is_current=False` and `end_date=None` are not a contradiction — they are
    "this role ended, and we do not know when". The tri-state exists precisely
    so that state is expressible."""
    role = _open_role()
    result = apply_ops(
        _profile(role), [_close(role.id, end_date=None)], "manual_role_add"
    )

    closed = result.profile.work_experience[0]
    assert closed.is_current is False
    assert closed.end_date is None


def test_an_undated_close_receipts_only_the_flag():
    """A close with no date must not manufacture an `end_date: null → null`
    receipt. The trail is what the candidate reads as "what changed"; a change
    that did not happen has no business in it (and it is what makes
    `discarded_later_edits` and the undo warning trustworthy)."""
    role = _open_role()
    result = apply_ops(
        _profile(role), [_close(role.id, end_date=None)], "manual_role_add"
    )

    assert [c.field for c in result.changes] == [f"[{role.id}].is_current"]


def test_an_undated_close_leaves_the_end_date_gap_open():
    """The tri-state must not become a way to HIDE a gap. `is_current=True`
    suppresses the end-date gap (#155, an ongoing role has no end date); a
    dateless CLOSE must not inherit that suppression."""
    role = _open_role()
    before = _profile(role.model_copy(deep=True)).calculate_completeness()

    closed = apply_ops(
        _profile(role), [_close(role.id, end_date=None)], "manual_role_add"
    ).profile

    from applire.services.profile.completeness import field_present

    entry = closed.work_experience[0].model_dump(mode="json")
    assert field_present(entry, "end_date") is False
    # And the score reflects the newly-visible gap rather than staying put.
    assert closed.calculate_completeness() < before


def test_a_dated_close_closes_the_end_date_gap():
    role = _open_role()
    closed = apply_ops(_profile(role), [_close(role.id)], "manual_role_add").profile

    from applire.services.profile.completeness import field_present

    entry = closed.work_experience[0].model_dump(mode="json")
    assert field_present(entry, "end_date") is True


# ── Reach: exactly the one role named, and nothing else ──────────────────────


def test_another_current_role_is_left_alone():
    """A parallel engagement (the side-role case `add_role` already supports)
    must survive: this op closes the role it names, never "every other one"."""
    target = _open_role(company="Rheinwerk GmbH")
    side = _open_role(company="MyStartup", role="Founder")

    result = apply_ops(_profile(target, side), [_close(target.id)], "manual_role_add")

    still_open = next(w for w in result.profile.work_experience if w.id == side.id)
    assert still_open.is_current is True
    assert still_open.end_date is None


def test_an_unknown_target_writes_nothing():
    role = _open_role()
    result = apply_ops(
        _profile(role), [_close("no-such-role")], "manual_role_add"
    )

    assert result.profile.work_experience[0].is_current is True
    assert result.profile.work_experience[0].end_date is None
    assert result.changes == []


def test_a_project_is_not_a_role():
    """`is_current` lives on `ExperienceBase`, so projects and volunteering carry
    it too — but this op is named for closing a ROLE and resolves against
    `work_experience` alone. Reach that is wider than the act's name is how a
    named op quietly becomes the power primitive the design rejected."""
    project = ProjectEntry(name="Side Project", is_current=True)
    role = _open_role()

    result = apply_ops(
        _profile(role, projects=[project]), [_close(project.id)], "manual_role_add"
    )

    assert result.profile.projects[0].is_current is True
    assert result.changes == []


# ── Idempotence and the fill-only date rule ──────────────────────────────────


def test_closing_an_already_closed_role_is_a_silent_no_op():
    role = _open_role(end_date="2026-05-31", is_current=False)
    result = apply_ops(_profile(role), [_close(role.id)], "manual_role_add")

    assert result.profile.work_experience[0].end_date == "2026-05-31"
    assert result.changes == []


def test_a_close_never_re_dates_a_role_that_already_has_an_end_date():
    """Re-dating a dated role is a CORRECTION of an attested fact, which only an
    authorised overwrite (`ResolveField`) or a human section edit may perform.
    The close still asserts the flag, which is its own act."""
    role = _open_role(end_date="2024-03-31", is_current=None)
    result = apply_ops(
        _profile(role), [_close(role.id, end_date="2026-05-31")], "manual_role_add"
    )

    closed = result.profile.work_experience[0]
    assert closed.end_date == "2024-03-31"
    assert closed.is_current is False


# ── Receipts ─────────────────────────────────────────────────────────────────


def test_the_close_is_receipted_per_field():
    role = _open_role()
    result = apply_ops(_profile(role), [_close(role.id)], "manual_role_add")

    fields = {c.field: c for c in result.changes}
    assert f"[{role.id}].end_date" in fields
    assert f"[{role.id}].is_current" in fields
    end = fields[f"[{role.id}].end_date"]
    assert end.section == "work_experience"
    assert end.action == "updated"
    assert end.old_value is None
    assert end.new_value == "2026-05-31"


def test_the_receipt_carries_the_reason():
    role = _open_role()
    result = apply_ops(
        _profile(role), [_close(role.id, reason="left_the_company")], "manual_role_add"
    )

    assert any("left_the_company" in (c.rationale or "") for c in result.changes)
    assert {c.rationale_key for c in result.changes} == {"role_closed"}


def test_a_close_is_positive_content_not_a_retraction():
    """Invariant 7 separates RETRACTIONS (demotions, denials, re-floorings) from
    gap-addressing content. Closing a role is neither a retraction nor a denial:
    it is the candidate stating a fact about their history, so it belongs on
    `changes` — where an "addressed" gate can see it."""
    role = _open_role()
    result = apply_ops(_profile(role), [_close(role.id)], "manual_role_add")

    assert result.demotions == []
    assert bool(result.changes) is True


# ── Through the one write path ───────────────────────────────────────────────
#
# Inventory row 6 (`add_role`) carries a ❌ in the `compl.` column: it computes
# a completeness score for its RESPONSE and never writes it back, so the stored
# `metadata.completeness_score` drifts every time a role is closed. That is not
# fixed by patching row 6 — it is fixed by the write travelling the one path
# that owns invariant 4. These tests pin that the op inherits it.

_SEED_JSON = {
    "personal_info": {"full_name": "Daniel Kovač", "email": "daniel@example.invalid"},
    "work_experience": [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "company": "Rheinwerk GmbH",
            "role": "Automation Engineer",
            "start_date": "2018-01",
            "end_date": None,
            "is_current": True,
            "responsibilities": ["Ran the build"],
            "achievements": ["Cut deploy time in half"],
            "team_size": 6,
            "industry_context": "Industrial automation",
        }
    ],
    "education": [{"institution": "TU Wien", "degree": "MSc", "field": "Informatik"}],
    "skills": [{"name": "Terraform", "category": "technical", "status": "confirmed"}],
    "metadata": {
        "completeness_score": 0.0,
        "created_via": "cv_upload",
        "created_at": "2020-01-01T00:00:00Z",
        "last_updated": "2020-01-01T00:00:00Z",
    },
}
_SEED_ROLE_ID = _SEED_JSON["work_experience"][0]["id"]


@pytest.mark.asyncio
async def test_commit_ops_applies_a_close_and_recomputes_completeness():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.db.session import Base
    from applire.models.profile import (
        MasterProfile,
        ProfileSnapshot,
        authorized_profile_write,
    )
    from applire.services.profile.commit import CommitProvenance, commit_ops

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            with authorized_profile_write():
                record = MasterProfile(profile_json=dict(_SEED_JSON))
            session.add(record)
            await session.commit()

            result = await commit_ops(
                session,
                [
                    CloseRole(
                        target=_SEED_ROLE_ID,
                        end_date="2026-05-31",
                        reason="superseded_by_new_role",
                    )
                ],
                CommitProvenance(
                    source="manual_role_add", intake="role_add", actor="candidate"
                ),
            )

        stored = record.profile_json
        assert stored["work_experience"][0]["is_current"] is False
        assert stored["work_experience"][0]["end_date"] == "2026-05-31"
        # Invariant 4 — the recompute row 6 never performed.
        assert stored["metadata"]["completeness_score"] == result.completeness
        assert stored["metadata"]["completeness_score"] != 0.0
        # Invariant 3 — the trail, and it names the act.
        history = stored["metadata"]["enrichment_history"]
        assert len(history) == 1
        assert history[0]["source"] == "manual_role_add"
        assert {c["rationale_key"] for c in history[0]["changes"]} == {"role_closed"}
    finally:
        await engine.dispose()
