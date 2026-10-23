# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hazelnut build 3, WP-FR (delivery F12) — a skill decision answered on the
standalone profile-review route must APPLY.

Daniel (delivery run 2026-10-22, `it_backend_daniel`): the import parked a
family-3 confirmation ("'SQL' shares a word with skills already on your profile
(SQL performance tuning) …") — `_apply_upsert_skill` returns WITHOUT writing the
incoming skill when it parks that ask. He answered "Add 'SQL' as a separate skill"
in the "Review profile" drawer (the standalone route, `_handle_confirmation_answer`).
The trail shows ONE change — `metadata|pending_confirmations|updated`, "Recorded
your answer to a confirmation question." — and no skill "SQL" exists afterwards.

Cause: the standalone route had only the REFUSAL half of the skill arm (#730);
`distinct` / `merge` fell through to the bookkeeping resolve (`_apply_resolve_
confirmation`, bookkeeping by design for the skill families: it assumes the
reconciler already wrote something — for family 3 it wrote nothing). The in-turn
door has always written through `_apply_interview_confirmation`. Neither WP-A's
`name_taken` refusal (#801, only on a model-bound `target`, never with a user
decision) nor G's decisions take part.

The fix routes the standalone door's skill answers through the SAME applier.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, authorized_profile_write
from applire.models.session import InterviewSession
from applire.schemas.profile import MasterProfileData, PendingConfirmation, ProfileMetadata
from applire.schemas.session import SessionMessageResponse
from applire.services.profile.reconcile.confirmations import skill_containment_confirmation

INCOMING = "SQL"
EXISTING = "SQL performance tuning"


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


def _parked() -> PendingConfirmation:
    """Daniel's confirmation, built by the REAL builder with the context
    `_apply_upsert_skill` gives it."""
    op = skill_containment_confirmation(
        incoming_skill=INCOMING,
        related=[EXISTING],
        context={
            "incoming_skill": INCOMING,
            "related_skills": [EXISTING],
            "category": "technical",
            "proficiency": None,
            "evidence_refs": [],
        },
    )
    return PendingConfirmation(
        question=op.question,
        options=list(op.options),
        context=dict(op.context),
        source="cv_upload",
        question_i18n=dict(op.question_i18n or {}),
        options_i18n=[dict(p) for p in (op.options_i18n or [])],
        option_keys=list(op.option_keys or []),
    )


async def _seed(db) -> tuple[MasterProfile, dict[str, Any]]:
    parked = _parked()
    profile = MasterProfileData.model_validate({
        "personal_info": {"name": "Daniel Kovac", "email": "kontakt@applire.de"},
        "work_experience": [{"company": "Cargonaut Logistics GmbH", "role": "Senior Backend Engineer", "start_date": "2021-01"}],
        "skills": [{"name": EXISTING, "category": "technical"}, {"name": "PostgreSQL", "category": "technical"}],
    })
    profile.metadata = ProfileMetadata(completeness_score=0.0, pending_confirmations=[parked])
    with authorized_profile_write():
        record = MasterProfile(profile_json=profile.model_dump(mode="json"))
    db.add(record)
    await db.commit()
    from applire.services.session import _confirmation_state

    stored = MasterProfileData.model_validate(record.profile_json).metadata.pending_confirmations[0]
    entry = _confirmation_state(stored)
    return record, entry


async def _interview(db, record, entry) -> tuple[InterviewSession, dict]:
    import applire.services.session as session_mod

    state = session_mod._build_state(
        mode="guided", job_id=None, gap_analysis_id=None, profile_id=record.id,
        critical_gaps=["cluster-x"], gap_categories={}, gap_clusters_by_id={},
        current_question=entry["question"], hard_ceiling=9,
    )
    state["questions_asked"] = 0
    interview = InterviewSession(
        job_analysis_id=None, profile_id=record.id, mode="guided", status="active",
        state=state, hard_ceiling=9, questions_asked=0,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(interview)
    await db.commit()
    return interview, state


def _skills(record) -> list[str]:
    return [s.name for s in MasterProfileData.model_validate(record.profile_json).skills]


def _open_asks(record) -> int:
    md = MasterProfileData.model_validate(record.profile_json).metadata
    return len([c for c in md.pending_confirmations if not c.resolved])


async def _answer(db, record, entry, answer, monkeypatch):
    import applire.services.session as session_mod

    interview, state = await _interview(db, record, entry)
    monkeypatch.setattr(
        session_mod, "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )
    await session_mod._handle_confirmation_answer(interview, state, db, MagicMock(), 0, entry, answer)
    await db.refresh(record)


@pytest.mark.asyncio
async def test_add_as_a_separate_skill_writes_the_skill_on_the_standalone_route(db_session, monkeypatch):
    record, entry = await _seed(db_session)
    assert INCOMING not in _skills(record)  # the import parked it, wrote nothing
    await _answer(db_session, record, entry, entry["options"][0], monkeypatch)
    assert INCOMING in _skills(record)
    assert EXISTING in _skills(record)
    assert _open_asks(record) == 0  # the ask is spent


@pytest.mark.asyncio
async def test_the_german_rendering_of_the_option_applies_too(db_session, monkeypatch):
    record, entry = await _seed(db_session)
    await _answer(db_session, record, entry, entry["options_i18n"][0]["de"], monkeypatch)
    assert INCOMING in _skills(record)


@pytest.mark.asyncio
async def test_merge_folds_it_into_the_existing_skill(db_session, monkeypatch):
    record, entry = await _seed(db_session)
    await _answer(db_session, record, entry, entry["options"][1], monkeypatch)
    names = _skills(record)
    assert INCOMING not in names
    assert EXISTING in names
    assert _open_asks(record) == 0
    md = MasterProfileData.model_validate(record.profile_json).metadata
    merged = [
        ch for ev in md.enrichment_history for ch in ev.changes
        if ch.section == "skills" and ch.action == "merged"
    ]
    assert merged, "the merge decision must be applied and receipted"


@pytest.mark.asyncio
async def test_neither_writes_nothing_and_spends_the_ask(db_session, monkeypatch):
    record, entry = await _seed(db_session)
    await _answer(db_session, record, entry, entry["options"][2], monkeypatch)
    assert _skills(record) == [EXISTING, "PostgreSQL"]
    assert _open_asks(record) == 0


@pytest.mark.asyncio
async def test_the_write_is_receipted_on_the_trail(db_session, monkeypatch):
    record, entry = await _seed(db_session)
    await _answer(db_session, record, entry, entry["options"][0], monkeypatch)
    md = MasterProfileData.model_validate(record.profile_json).metadata
    added = [
        ch for ev in md.enrichment_history for ch in ev.changes
        if ch.section == "skills" and ch.action == "added" and ch.new_value == INCOMING
    ]
    assert added, "the skill write must carry a receipt, not only the bookkeeping line"
