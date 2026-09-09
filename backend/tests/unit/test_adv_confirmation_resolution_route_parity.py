# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Adversarial finding (2026-09-09, WP-adv-vault) — founder ruling V-5's
deterministic engagement resolution turn was wired into ONE of the TWO
resolution routes a durably-parked confirmation can reach.

**The two routes.**

1. ``session._handle_interview_confirmation_answer`` — a confirmation raised
   and answered WITHIN the same interview turn (``state["pending_interview_
   confirmation"]``). V-5 wired ``session._apply_engagement_confirmation``
   into this one, and it is covered by
   ``test_v5_engagement_resolution_turn.py``.
2. ``session._handle_confirmation_answer`` — the standalone profile-review
   interview's (US165) resolution of an item durably parked on
   ``metadata.pending_confirmations`` (``session._open_confirmations`` →
   ``build_confirmation_clusters``). This is the route the task's own attack
   surface names: ``submit_testimony`` and ``submit_claims`` are single-shot
   doors with no interview session of their own, so ANY engagement ambiguity
   they raise is only ever answerable via THIS route — as is the same
   ambiguity raised by a CV import, and #686's "Decide now" CTA opens exactly
   this session (``ProfileReviewDrawer``).

Before the fix: route 2 called only ``profile.resolve_confirmation``, which is
bookkeeping by its own docstring ("Recording the user's chosen option marks
the confirmation resolved... Re-running the reconciler... is a richer
follow-up") — it never rebuilt the parked op, so V-5's "the station / project
/ volunteering... were gone" was reproducible AGAIN via this route alone, for
every door that isn't a live interview session. A second, silent defect rode
along: ``_open_confirmations`` carried only the door's plain (always-English)
``question``/``options`` fields, and ``build_confirmation_clusters`` never
rendered against the reader's ``ui_language`` at all (a stale ``# noqa:
ARG001`` on the ``lang`` parameter) — so a German profile-review session saw
an English question for any door-raised ambiguity, undoing #669's own point
one layer up.

Fixed together: ``_open_confirmations`` now carries ``context``/
``option_keys``/the i18n payloads; ``build_confirmation_clusters`` renders
them against ``lang``; ``_handle_confirmation_answer`` calls
``_apply_engagement_confirmation`` for the three engagement families before
clearing the durable park.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, authorized_profile_write
from applire.models.session import InterviewSession
from applire.schemas.profile import MasterProfileData, PendingConfirmation, ProfileMetadata
from applire.schemas.session import SessionMessageResponse
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import AddBullets, UpsertWork

BULLET = "Verantwortlich für die Qualifizierung der MES-Anbindung"


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


def _real_engagement_confirmation() -> PendingConfirmation:
    """The EXACT shape a real door produces: run `apply_ops` against a
    near-dupe vault (source="testimony" — a door with no interview session of
    its own) and capture the parked `RequestConfirmation` verbatim, exactly as
    `import_bridge._to_pending_confirmation` would carry it onto the durable
    channel. No hand-rolled approximation."""
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Lena Fischer"},
            "work_experience": [
                {"id": "w-helv", "company": "Helvetia Pharma GmbH", "role": "Systems Engineer"}
            ],
        }
    )
    result = apply_ops(
        profile,
        [
            UpsertWork(ref="n1", company="Helvetia Pharma", role="Systems Engineer"),
            AddBullets(target="n1", responsibilities=[BULLET]),
        ],
        "testimony",
    )
    assert len(result.pending_confirmations) == 1
    rc = result.pending_confirmations[0]
    return PendingConfirmation(
        question=rc.question,
        options=list(rc.options),
        context=dict(rc.context),
        source="testimony",
        question_i18n=dict(rc.question_i18n) if rc.question_i18n else None,
        options_i18n=[dict(o) for o in rc.options_i18n] if rc.options_i18n else None,
        option_keys=list(rc.option_keys),
    )


async def _seed(db, confirmation: PendingConfirmation) -> MasterProfile:
    profile = MasterProfileData.model_validate(
        {
            "personal_info": {"name": "Lena Fischer"},
            "work_experience": [
                {"id": "w-helv", "company": "Helvetia Pharma GmbH", "role": "Systems Engineer"}
            ],
        }
    )
    profile.metadata = ProfileMetadata(pending_confirmations=[confirmation])
    with authorized_profile_write():
        record = MasterProfile(profile_json=profile.model_dump(mode="json"))
    db.add(record)
    await db.commit()
    return record


@pytest.mark.asyncio
async def test_the_durable_park_route_renders_german_and_carries_context(db_session):
    """Both halves of the surfacing bug, proven together: the reader's
    `ui_language` is honoured, and the confirmation cluster carries what V-5's
    resolution turn needs."""
    from applire.services.interview_graph import build_confirmation_clusters
    from applire.services.session import _open_confirmations

    confirmation = _real_engagement_confirmation()
    record = await _seed(db_session, confirmation)

    open_asks = await _open_confirmations(record)
    assert open_asks[0]["context"]["section"] == "work_experience"
    assert open_asks[0]["option_keys"] == ["merge", "distinct"]

    _ids, _categories, by_id = build_confirmation_clusters(open_asks, "de")
    entry = next(iter(by_id.values()))
    assert "Dieselbe Position" in entry["question"] or "ähnelt" in entry["question"]
    assert "zusammenführen" in " ".join(entry["choices"])
    assert entry["context"]["section"] == "work_experience"
    assert entry["option_keys"] == ["merge", "distinct"]


@pytest.mark.asyncio
async def test_answering_merge_through_the_standalone_profile_review_route_lands_the_entity(
    db_session, monkeypatch
):
    """THE reproduction: `submit_testimony`/`submit_claims` raise this exact
    confirmation and have no interview session of their own to resolve it
    in-turn — the candidate can only answer it later, here. Before the fix,
    `_handle_confirmation_answer` never called `_apply_engagement_confirmation`
    at all: the merge text was recorded as the "chosen_option" and the vault
    never moved. The German merge option is used deliberately (#669 must
    resolve on the stable key, not an English substring)."""
    import applire.services.session as session_mod

    confirmation = _real_engagement_confirmation()
    record = await _seed(db_session, confirmation)
    assert len(record.profile_json["work_experience"]) == 1

    open_asks = await session_mod._open_confirmations(record)
    from applire.services.interview_graph import build_confirmation_clusters

    ids, categories, by_id = build_confirmation_clusters(open_asks, "de")
    cid = ids[0]

    state = session_mod._build_state(
        mode="guided", job_id=None, gap_analysis_id=None, profile_id=record.id,
        critical_gaps=[cid], gap_categories=categories, gap_clusters_by_id=by_id,
        current_question=by_id[cid]["question"], hard_ceiling=9,
    )
    state["entry"] = "profile_review"
    state["confirmation_clusters"] = by_id

    interview = InterviewSession(
        job_analysis_id=None, profile_id=record.id, mode="guided", status="active",
        state=state, hard_ceiling=9,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(interview)
    await db_session.commit()

    monkeypatch.setattr(
        session_mod, "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )

    german_merge_text = by_id[cid]["choices"][0]  # "Dieselbe Position — zusammenführen"
    await session_mod._handle_confirmation_answer(
        interview, state, db_session, MagicMock(), 0, by_id[cid], german_merge_text,
    )

    stored = MasterProfileData.model_validate(record.profile_json)
    # The entity did NOT duplicate (merge, not distinct) ...
    assert len(stored.work_experience) == 1
    # ... and the candidate's bullet actually landed on it, not lost.
    assert BULLET in stored.work_experience[0].responsibilities
    # ... and the durable park is spent, so a later session does not re-ask it.
    assert stored.metadata.pending_confirmations == []


@pytest.mark.asyncio
async def test_answering_distinct_through_the_standalone_profile_review_route_creates_the_entity(
    db_session, monkeypatch
):
    """"Different — keep both", answered in English this time (door parity:
    the resolution must not depend on which language the answer arrives in)."""
    import applire.services.session as session_mod
    from applire.services.interview_graph import build_confirmation_clusters

    confirmation = _real_engagement_confirmation()
    record = await _seed(db_session, confirmation)

    open_asks = await session_mod._open_confirmations(record)
    ids, categories, by_id = build_confirmation_clusters(open_asks, "en")
    cid = ids[0]

    state = session_mod._build_state(
        mode="guided", job_id=None, gap_analysis_id=None, profile_id=record.id,
        critical_gaps=[cid], gap_categories=categories, gap_clusters_by_id=by_id,
        current_question=by_id[cid]["question"], hard_ceiling=9,
    )
    state["entry"] = "profile_review"
    state["confirmation_clusters"] = by_id

    interview = InterviewSession(
        job_analysis_id=None, profile_id=record.id, mode="guided", status="active",
        state=state, hard_ceiling=9,
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db_session.add(interview)
    await db_session.commit()

    monkeypatch.setattr(
        session_mod, "_ask_or_complete_at",
        AsyncMock(return_value=SessionMessageResponse(complete=True, gaps_remaining=0)),
    )

    english_distinct_text = by_id[cid]["choices"][1]  # "Different — keep both"
    await session_mod._handle_confirmation_answer(
        interview, state, db_session, MagicMock(), 0, by_id[cid], english_distinct_text,
    )

    stored = MasterProfileData.model_validate(record.profile_json)
    assert len(stored.work_experience) == 2
    created = [w for w in stored.work_experience if w.id != "w-helv"]
    assert len(created) == 1
    assert BULLET in created[0].responsibilities
    assert stored.metadata.pending_confirmations == []
