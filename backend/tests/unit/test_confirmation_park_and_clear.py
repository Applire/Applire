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

"""#480 PR 5 — the confirmation park+clear lifecycle, completed.

PR 2 could only build half of it. `commit_ops` parks a turn's asks on
`metadata.pending_confirmations` so they become visible vault state, but the
interview resolved its own asks in SESSION STATE (#187) and never touched
metadata — so a durable park would have been re-raised by a LATER session's
`_open_confirmations`, re-asking something the candidate had already answered.
The interview therefore passed `park_confirmations=False` and the parameter
carried an explicit "PR 5 owns this" note.

`ResolveConfirmation` is the missing CLEAR, so this PR:

* removes `park_confirmations` — parking is unconditional again;
* wires the in-session resolution path to clear the metadata park **through
  the op and the committer**, never by mutating the parked list in place.

Both halves are pinned here, plus the two scenarios that make the lifecycle
worth having: an answered ask never resurfaces, and an ABANDONED one does.
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, authorized_profile_write
from applire.schemas.profile import (
    MasterProfileData,
    PendingConfirmation,
    ProfileMetadata,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import ResolveConfirmation
from applire.services.profile.resolution import build_resolve_confirmation_op

SOURCE = "manual_edit"


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _confirmation(**kwargs: Any) -> PendingConfirmation:
    payload: dict[str, Any] = {
        "question": "Is 'Owner' the same role as 'Founder'?",
        "options": ["Same role", "Two roles"],
        "context": {"existing": "Founder"},
        "source": "interview",
    }
    payload.update(kwargs)
    return PendingConfirmation(**payload)


def _profile_json(confirmations: list[PendingConfirmation]) -> dict:
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Max Muster", "email": "max@example.invalid"},
            "work_experience": [
                {"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}
            ],
            "skills": [{"name": "Python", "category": "technical"}],
            "education": [{"institution": "TU", "degree": "MSc", "field": "CS"}],
        }
    )
    profile.metadata = ProfileMetadata(
        completeness_score=0.0, pending_confirmations=list(confirmations)
    )
    return profile.model_dump(mode="json")


async def _seed(db, confirmations: list[PendingConfirmation]) -> MasterProfile:
    with authorized_profile_write():
        record = MasterProfile(profile_json=_profile_json(confirmations))
    db.add(record)
    await db.commit()
    return record


# ── 1. The applier: the CLEAR, guarded like the resolution it is ─────────────


def test_resolving_a_parked_confirmation_removes_it_and_receipts_the_answer():
    confirmation = _confirmation()
    profile = MasterProfileData()
    profile.metadata = ProfileMetadata(pending_confirmations=[confirmation])

    result = apply_ops(
        profile,
        [build_resolve_confirmation_op(confirmation, "Same role")],
        SOURCE,
    )

    assert result.profile.metadata.pending_confirmations == []
    (change,) = result.changes
    assert change.section == "metadata"
    assert change.old_value == confirmation.question
    assert change.new_value == "Same role"


def test_an_unknown_confirmation_id_changes_nothing():
    confirmation = _confirmation()
    profile = MasterProfileData()
    profile.metadata = ProfileMetadata(pending_confirmations=[confirmation])

    result = apply_ops(
        profile,
        [ResolveConfirmation(confirmation_id=str(uuid.uuid4()), chosen_option="x")],
        SOURCE,
    )

    assert [c.confirmation_id for c in result.profile.metadata.pending_confirmations] == [
        confirmation.confirmation_id
    ]
    assert result.changes == []


def test_only_the_named_ask_is_cleared():
    kept, resolved = _confirmation(question="A?"), _confirmation(question="B?")
    profile = MasterProfileData()
    profile.metadata = ProfileMetadata(pending_confirmations=[kept, resolved])

    result = apply_ops(
        profile, [build_resolve_confirmation_op(resolved, "yes")], SOURCE
    )

    assert [c.question for c in result.profile.metadata.pending_confirmations] == ["A?"]


# ── 2. The service gains the recompute the old writer was missing ────────────


@pytest.mark.asyncio
async def test_resolve_confirmation_recomputes_completeness(db_session):
    """Design §4.5 / row 5 — a behavioural delta, not a refactor. The old
    writer moved `last_updated` and left `completeness_score` at whatever the
    last import wrote, so the stored score drifted from the vault."""
    from applire.services.profile import resolve_confirmation

    confirmation = _confirmation()
    record = await _seed(db_session, [confirmation])
    assert record.profile_json["metadata"]["completeness_score"] == 0.0

    await resolve_confirmation(confirmation.confirmation_id, "Same role", db_session)

    stored = MasterProfileData.model_validate(record.profile_json)
    assert stored.metadata.completeness_score > 0.0
    assert stored.metadata.pending_confirmations == []
    assert stored.metadata.enrichment_history[-1].source == "manual_edit"


@pytest.mark.asyncio
async def test_resolve_confirmation_still_raises_for_an_unknown_id(db_session):
    """`_resolve_confirmation_safely` swallows exactly this LookupError to stay
    idempotent on resume — the contract must not change."""
    from applire.services.profile import resolve_confirmation

    await _seed(db_session, [_confirmation()])
    with pytest.raises(LookupError):
        await resolve_confirmation(str(uuid.uuid4()), "Same role", db_session)


# ── 3. Parking is unconditional again ────────────────────────────────────────


def test_commit_ops_has_no_park_confirmations_parameter():
    """The parameter existed only because the CLEAR did not. Leaving it behind
    would let a future intake opt out of visible vault state by accident."""
    import inspect

    from applire.services.profile.commit import commit_ops

    assert "park_confirmations" not in inspect.signature(commit_ops).parameters


# ── 4. The scenarios the lifecycle exists for ────────────────────────────────


@pytest.mark.asyncio
async def test_an_answered_ask_never_resurfaces_in_a_later_session(db_session):
    """Session 1 parks and resolves; session 2 must not rebuild a cluster for
    it. This is the half PR 2 could not have without the clear."""
    from applire.services.profile import resolve_confirmation
    from applire.services.session import _open_confirmations

    confirmation = _confirmation()
    record = await _seed(db_session, [confirmation])
    assert len(await _open_confirmations(record)) == 1

    await resolve_confirmation(confirmation.confirmation_id, "Same role", db_session)

    assert await _open_confirmations(record) == []


@pytest.mark.asyncio
async def test_an_abandoned_ask_survives_to_the_next_session(db_session):
    """The durable benefit, now safe: an ask the candidate never answered is
    still owed to them, whatever happened to the session that raised it."""
    from applire.services.interview_graph import build_confirmation_clusters
    from applire.services.session import _open_confirmations

    confirmation = _confirmation()
    record = await _seed(db_session, [confirmation])

    open_asks = await _open_confirmations(record)
    ids, _categories, by_id = build_confirmation_clusters(open_asks, "en")

    assert len(ids) == 1
    assert by_id[ids[0]]["confirmation_id"] == confirmation.confirmation_id


# ── 5. The in-session (#187) path clears through the committer ───────────────


@pytest.mark.asyncio
async def test_the_in_session_resolution_clears_the_metadata_park(db_session, monkeypatch):
    """The interview answers its own ask in session state. Since the ask is now
    parked durably, the same turn must also CLEAR the park — otherwise the
    candidate is asked again in a later session."""
    import applire.services.session as session_mod
    from applire.models.session import InterviewSession
    from applire.schemas.session import SessionMessageResponse

    confirmation = _confirmation()
    record = await _seed(db_session, [confirmation])

    state = session_mod._build_state(
        mode="guided",
        job_id=None,
        gap_analysis_id=None,
        profile_id=record.id,
        critical_gaps=["cluster-x"],
        gap_categories={},
        gap_clusters_by_id={},
        current_question="Is 'Owner' the same role as 'Founder'?",
        hard_ceiling=9,
    )
    state["pending_interview_confirmation"] = {
        "confirmation_id": confirmation.confirmation_id,
        "question": confirmation.question,
        "options": list(confirmation.options),
        "context": dict(confirmation.context),
    }
    from datetime import datetime, timedelta, timezone

    interview = InterviewSession(
        job_analysis_id=None,
        profile_id=record.id,
        mode="guided",
        status="active",
        state=state,
        hard_ceiling=9,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(interview)
    await db_session.commit()

    monkeypatch.setattr(
        session_mod,
        "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )

    await session_mod._handle_interview_confirmation_answer(
        interview,
        state,
        db_session,
        MagicMock(),
        0,
        state["pending_interview_confirmation"],
        "Same role",
        "en",
    )

    stored = MasterProfileData.model_validate(record.profile_json)
    assert stored.metadata.pending_confirmations == []


def test_the_in_session_state_carries_the_parked_asks_identity():
    """The clear needs the id the committer minted — a session that persisted
    only question/options/context could never address the parked entry."""
    from applire.services.session import _confirmation_state

    confirmation = _confirmation()
    shaped = _confirmation_state(confirmation)
    assert shaped["confirmation_id"] == confirmation.confirmation_id
    assert shaped["question"] == confirmation.question
    assert shaped["options"] == list(confirmation.options)
