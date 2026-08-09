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

"""Profile enrichment endpoints — Mode C interview sessions (no JD required)."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.constants import INTERVIEW_SESSION_TTL_DAYS
from applire.db.session import get_db
from applire.models.profile import MasterProfile
from applire.models.session import InterviewSession
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.schemas.enrich import (
    EnrichActionResponse,
    EnrichRespondRequest,
    EnrichRespondResponse,
    EnrichStartRequest,
    EnrichStartResponse,
    GapItem,
)
from applire.schemas.session import ConfirmationPrompt
from applire.services.interview.signals import is_termination_signal
from applire.services.interview_graph import (
    gap_detector_mode_c,
    question_generator_with_profile,
)
from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn
from applire.services.session import get_ui_language

router = APIRouter(prefix="/api/profile/enrich", tags=["profile-enrich"])

_ENRICH_HARD_CEILING_PER_GAP = 3
_ENRICH_MODE = "profile_enrich"


def _get_provider() -> LLMProvider:
    return get_provider()


async def _load_profile(db: AsyncSession) -> MasterProfile:
    result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="No master profile found")
    return record


async def _active_enrich_session(
    profile_id: uuid.UUID, db: AsyncSession
) -> InterviewSession | None:
    """The in-flight Mode-C enrichment session for this profile, if any.

    Mode-C sessions are job-less (``job_analysis_id IS NULL``) so the partial
    unique index ``uq_*_active_per_job`` does not cover them — uniqueness is
    enforced here instead. At most one ``profile_enrich`` session per profile may
    be ``active`` at a time; a re-launch must resume it rather than mint a
    duplicate (issue #74 / finding F6)."""
    result = await db.execute(
        select(InterviewSession)
        .where(
            InterviewSession.profile_id == profile_id,
            InterviewSession.job_analysis_id.is_(None),
            InterviewSession.mode == _ENRICH_MODE,
            InterviewSession.status == "active",
            InterviewSession.deleted_at.is_(None),
        )
        .order_by(InterviewSession.created_at.desc())
    )
    return result.scalars().first()


def _resume_response(session: InterviewSession) -> EnrichStartResponse:
    """Re-render the launch payload for an already in-flight enrich session,
    reflecting its current question and gap progress (resume, not restart)."""
    state: dict = dict(session.state)
    all_gaps: list[str] = state.get("critical_gaps", [])
    gap_items = _build_gap_items(
        all_gaps,
        state.get("current_gap_index", 0),
        state.get("addressed_gaps", []),
        state.get("na_gaps", []),
        state.get("skipped_gaps", []),
    )
    return EnrichStartResponse(
        session_id=session.id,
        first_question=state.get("current_question", ""),
        gaps=gap_items,
        estimated_questions=len(all_gaps),
    )


async def _load_session(session_id: uuid.UUID, db: AsyncSession) -> InterviewSession:
    result = await db.execute(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.mode == _ENRICH_MODE,
            InterviewSession.status == "active",
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=404,
            detail="Enrichment session not found or already complete",
        )
    return session


def _build_gap_items(
    all_gaps: list[str],
    current_index: int,
    addressed: list[str],
    na_gaps: list[str],
    skipped: list[str],
) -> list[GapItem]:
    items = []
    for i, gap in enumerate(all_gaps):
        if gap in na_gaps:
            s = "na"
        elif gap in addressed:
            s = "done"
        elif gap in skipped:
            s = "skipped"
        elif i == current_index:
            s = "active"
        else:
            s = "pending"
        items.append(GapItem(id=gap, label=gap, status=s))
    return items


async def _next_question_or_done(
    session: InterviewSession,
    profile_data: dict,
    provider: LLMProvider,
    db: AsyncSession,
) -> tuple[str | None, bool]:
    """Advance to next valid gap and generate question. Returns (question, done)."""
    state: dict = dict(session.state)
    all_gaps: list[str] = state["critical_gaps"]
    addressed: list[str] = state["addressed_gaps"]
    na_gaps: list[str] = state.get("na_gaps", [])
    skipped: list[str] = state.get("skipped_gaps", [])
    exhausted = set(addressed) | set(na_gaps) | set(skipped)

    # Advance index past exhausted gaps
    idx = state["current_gap_index"] + 1
    while idx < len(all_gaps) and all_gaps[idx] in exhausted:
        idx += 1

    if idx >= len(all_gaps):
        session.status = "complete"
        session.state = state
        return None, True

    state["current_gap_index"] = idx
    lang = await get_ui_language(db)
    q_data = await question_generator_with_profile(state, profile_data, provider, lang=lang)
    question = q_data["question"]
    state["current_question"] = question
    state["messages"].append({"role": "assistant", "content": question})
    session.state = state
    return question, False


@router.post(
    "/start",
    response_model=EnrichStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_enrich_session(
    body: EnrichStartRequest,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> EnrichStartResponse:
    """Create a new Mode C profile enrichment session.

    Scans the master profile for completeness gaps and starts an interactive
    interview to fill them. No job description required.

    Resume-safe: if an enrichment session is already in flight for this profile,
    that session is resumed (its current question + gap progress returned) rather
    than minting a duplicate (issue #74 / finding F6).

    Returns the first question, gap list, and session ID.
    """
    profile_record = await _load_profile(db)
    profile_data: dict = profile_record.profile_json or {}

    existing = await _active_enrich_session(profile_record.id, db)
    if existing is not None:
        return _resume_response(existing)

    gaps = gap_detector_mode_c(profile_data, scope=body.scope)
    if not gaps:
        raise HTTPException(
            status_code=404,
            detail="No completeness gaps detected in profile",
        )

    state: dict = {
        "mode": _ENRICH_MODE,
        "job_id": None,
        "gap_analysis_id": None,
        "profile_id": str(profile_record.id),
        "critical_gaps": gaps,
        "gap_categories": {},
        "addressed_gaps": [],
        "current_gap_index": 0,
        "current_question": "",
        "messages": [],
        "questions_asked": 0,
        "hard_ceiling": len(gaps) * _ENRICH_HARD_CEILING_PER_GAP,
        "questions_per_gap": {},
        "skipped_gaps": [],
        "full_gaps": gaps,
        "na_gaps": [],
    }

    lang = await get_ui_language(db)
    q_data = await question_generator_with_profile(state, profile_data, provider, lang=lang)
    first_question = q_data["question"]
    state["current_question"] = first_question
    state["messages"].append({"role": "assistant", "content": first_question})

    session = InterviewSession(
        job_analysis_id=None,
        profile_id=profile_record.id,
        mode=_ENRICH_MODE,
        status="active",
        state=state,
        questions_asked=0,
        hard_ceiling=state["hard_ceiling"],
        expires_at=datetime.now(timezone.utc) + timedelta(days=INTERVIEW_SESSION_TTL_DAYS),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    gap_items = _build_gap_items(gaps, 0, [], [], [])
    # Ensure the first item is explicitly "active"
    gap_items[0] = GapItem(id=gaps[0], label=gaps[0], status="active")

    return EnrichStartResponse(
        session_id=session.id,
        first_question=first_question,
        gaps=gap_items,
        estimated_questions=len(gaps),
    )


@router.post("/{session_id}/respond", response_model=EnrichRespondResponse)
async def respond_to_enrich(
    session_id: uuid.UUID,
    body: EnrichRespondRequest,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> EnrichRespondResponse:
    """Submit a user answer for the current gap question.

    Runs: reconcile_interview_turn (engine, single pass) → QuestionGenerator (next gap).
    The separate ResponseParser+reviewer+ProfileUpdater chain is replaced by the
    ADR-046 reconciler (no review step).
    """
    session = await _load_session(session_id, db)
    state: dict = dict(session.state)
    profile_record = await _load_profile(db)

    answer = body.answer.strip()
    if is_termination_signal(answer):
        session.status = "complete"
        await db.commit()
        all_gaps = state["critical_gaps"]
        gap_items = _build_gap_items(
            all_gaps,
            state["current_gap_index"],
            state["addressed_gaps"],
            state.get("na_gaps", []),
            state.get("skipped_gaps", []),
        )
        return EnrichRespondResponse(
            next_question=None,
            gaps=gap_items,
            done=True,
            profile_updated=False,
        )

    state["messages"].append({"role": "user", "content": answer})
    current_gap = state["critical_gaps"][state["current_gap_index"]]
    current_question = state["current_question"]

    # Reconcile answer via the ADR-046 engine (single pass — no separate review step)
    lang = await get_ui_language(db)
    # ADR-063 (#480 PR 2) — the bridge writes through `commit_ops`, which
    # flushes and leaves the transaction here. The write is now UNCONDITIONAL,
    # which subsumes #338: `addressed` is deliberately `bool(changes)` EXCLUDING
    # denials (#231, so a denial never reads as "this gap was resolved" to the
    # ledger/gap-advance logic below), and while it ALSO gated the write a
    # denial-only turn was reconciled, adjudicated and then discarded — losing
    # an ADR-059 receipt the LLM call had already paid for. #338 widened the
    # gate to `addressed or denial_recorded`; the committer removes the gate,
    # so a turn that produced neither still leaves a trail saying it happened.
    turn = await reconcile_interview_turn(
        db,
        profile_record=profile_record,
        gap=current_gap,
        question=current_question,
        answer=answer,
        provider=provider,
        session_id=str(session_id),
        lang=lang,
    )
    updated_profile_data = turn.profile_dict

    # Mark gap addressed if the reconciler applied at least one change.
    # Deliberately NOT `or denial_recorded` — see above: a denial is persisted
    # but must never advance the gap as though it had been answered.
    if turn.addressed:
        state["addressed_gaps"].append(current_gap)

    state["questions_asked"] = state.get("questions_asked", 0) + 1
    session.questions_asked = state["questions_asked"]
    session.state = state

    # US185 — an unresolved ambiguity becomes a targeted confirmation the user
    # answers next; the reconciler never guesses entity identity. Surface it
    # instead of advancing to the next gap question.
    if turn.pending_confirmations:
        confirmation = turn.pending_confirmations[0]
        state["current_question"] = confirmation.question
        session.state = state
        await db.commit()
        gap_items = _build_gap_items(
            state["critical_gaps"],
            state["current_gap_index"],
            state["addressed_gaps"],
            state.get("na_gaps", []),
            state.get("skipped_gaps", []),
        )
        return EnrichRespondResponse(
            next_question=confirmation.question,
            gaps=gap_items,
            done=False,
            profile_updated=turn.addressed,
            pending_confirmations=[
                ConfirmationPrompt(
                    question=c.question, options=list(c.options), context=dict(c.context)
                )
                for c in turn.pending_confirmations
            ],
        )

    next_question, done = await _next_question_or_done(
        session, updated_profile_data, provider, db
    )
    # Re-read state after helper has updated session.state
    state = dict(session.state)
    await db.commit()

    all_gaps = state["critical_gaps"]
    gap_items = _build_gap_items(
        all_gaps,
        state["current_gap_index"],
        state["addressed_gaps"],
        state.get("na_gaps", []),
        state.get("skipped_gaps", []),
    )
    return EnrichRespondResponse(
        next_question=next_question,
        gaps=gap_items,
        done=done,
        profile_updated=turn.addressed,
    )


@router.post("/{session_id}/skip", response_model=EnrichActionResponse)
async def skip_gap(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> EnrichActionResponse:
    """Skip the current gap and advance to the next one."""
    session = await _load_session(session_id, db)
    state: dict = dict(session.state)
    current_gap = state["critical_gaps"][state["current_gap_index"]]
    skipped: list[str] = state.get("skipped_gaps", [])
    skipped.append(current_gap)
    state["skipped_gaps"] = skipped
    session.state = state

    profile_record = await _load_profile(db)
    next_question, done = await _next_question_or_done(
        session, profile_record.profile_json or {}, provider, db
    )
    # Re-read state after helper has updated session.state
    state = dict(session.state)
    await db.commit()

    all_gaps = state["critical_gaps"]
    gap_items = _build_gap_items(
        all_gaps,
        state["current_gap_index"],
        state["addressed_gaps"],
        state.get("na_gaps", []),
        state["skipped_gaps"],
    )
    return EnrichActionResponse(next_question=next_question, gaps=gap_items, done=done)


@router.post("/{session_id}/na", response_model=EnrichActionResponse)
async def mark_gap_na(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    provider: LLMProvider = Depends(_get_provider),
    _auth: AuthProvider = Depends(get_auth_provider),
) -> EnrichActionResponse:
    """Mark the current gap as not applicable (N/A).

    Persists N/A to both the session state and profile _meta.na_fields so that
    future profile scans exclude this field permanently.
    """
    session = await _load_session(session_id, db)
    state: dict = dict(session.state)
    current_gap = state["critical_gaps"][state["current_gap_index"]]

    # Persist N/A to session state
    na_gaps: list[str] = state.get("na_gaps", [])
    na_gaps.append(current_gap)
    state["na_gaps"] = na_gaps
    session.state = state

    # Persist N/A to profile _meta so future scans exclude this field
    profile_record = await _load_profile(db)
    profile_data: dict = dict(profile_record.profile_json or {})
    meta: dict = dict(profile_data.get("_meta") or {})
    existing_na: list[str] = list(meta.get("na_fields", []))
    if current_gap not in existing_na:
        existing_na.append(current_gap)
    meta["na_fields"] = existing_na
    profile_data["_meta"] = meta
    profile_record.profile_json = profile_data
    await db.flush()

    next_question, done = await _next_question_or_done(
        session, profile_data, provider, db
    )
    # Re-read state after helper has updated session.state
    state = dict(session.state)
    await db.commit()

    all_gaps = state["critical_gaps"]
    gap_items = _build_gap_items(
        all_gaps,
        state["current_gap_index"],
        state["addressed_gaps"],
        state["na_gaps"],
        state.get("skipped_gaps", []),
    )
    return EnrichActionResponse(next_question=next_question, gaps=gap_items, done=done)
