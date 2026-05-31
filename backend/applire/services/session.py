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

"""
Session service — orchestrates the interview state machine across REST calls.

Turn 1  (POST /api/session):
    resolve mode (auto-detect or override) → check for existing active session (idempotent)
    → [lazy gap analysis for MODE A] → GapDetector → QuestionGenerator → persist state

Turn N  (POST /api/session/{id}/message):
    load state → done-signal check (pre-LLM) → hard-ceiling check
    → ResponseParser → ProfileUpdater → persist profile
    → if gaps remain AND under ceiling: QuestionGenerator → next question
    → else: mark complete with reason

GET /api/session/{id}:
    load session → return SessionStateResponse for agent recovery / pause-resume
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import (
    INTERVIEW_HARD_CEILING_GUIDED,
    INTERVIEW_HARD_CEILING_TARGETED,
    INTERVIEW_MAX_QUESTIONS_PER_GAP,
    INTERVIEW_SESSION_TTL_DAYS as _SESSION_TTL_DAYS,
    INTERVIEW_TARGET_MIN_GUIDED,
    INTERVIEW_TARGET_MIN_TARGETED,
    MODE_B_COMPLETENESS_THRESHOLD,
)
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.models.session import InterviewSession
from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import MasterProfileData
from applire.schemas.session import (
    ConflictSummary,
    InterviewState,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionMessageResponse,
    SessionStateResponse,
)
from applire.services.gap import analyze_gaps
from applire.services.interview.signals import is_termination_signal
from applire.services.interview_graph import (
    gap_detector,
    gap_detector_mode_b,
    profile_updater,
    question_generator_with_profile,
    response_parser,
)


# ---------------------------------------------------------------------------
# POST /api/session
# ---------------------------------------------------------------------------


async def create_session(
    request: SessionCreateRequest,
    db: AsyncSession,
    provider: LLMProvider,
) -> SessionCreateResponse:
    job_id = request.job_id

    # Resolve job analysis
    job_result = await db.execute(
        select(JobAnalysis).where(
            JobAnalysis.id == job_id,
            JobAnalysis.deleted_at.is_(None),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise LookupError(f"Job analysis {job_id} not found")

    # Resolve latest profile (may be None for MODE B)
    profile_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile_record = profile_result.scalar_one_or_none()

    # --- Mode resolution ---
    if request.mode is not None:
        resolved_mode = request.mode
    else:
        resolved_mode = _auto_detect_mode(profile_record)

    # --- Micro-session: target_gap scopes to a single gap (Gap-Click mode, 19.9) ---
    if request.target_gap and resolved_mode == "targeted":
        return await _create_micro_session(job_id, job, profile_record, request.target_gap, db, provider)

    # --- Idempotency: return existing active session if one exists for this job ---
    existing = await _get_active_session(job_id, db)
    if existing is not None:
        state: InterviewState = dict(existing.state)
        gaps_total = len(state.get("critical_gaps", []))
        gaps_remaining = gaps_total - state.get("current_gap_index", 0)
        estimated = _estimated_questions(existing.mode)
        current_q = state.get("current_question", "")
        current_choices = state.get("current_choices")
        return SessionCreateResponse(
            session_id=existing.id,
            mode=existing.mode,
            first_question=current_q,
            question=current_q,
            estimated_questions=estimated,
            gaps_total=gaps_total,
            gaps_remaining=gaps_remaining,
            choices=current_choices,
            resumed=True,
        )

    # --- MODE A: Targeted Gap-Fill ---
    if resolved_mode == "targeted":
        return await _create_targeted_session(job_id, job, profile_record, db, provider)

    # --- MODE B: Guided Build ---
    return await _create_guided_session(job_id, job, profile_record, db, provider)


async def _create_targeted_session(
    job_id: uuid.UUID,
    job: JobAnalysis,
    profile_record: MasterProfile | None,
    db: AsyncSession,
    provider: LLMProvider,
) -> SessionCreateResponse:
    if profile_record is None:
        raise LookupError(
            "No profile found — upload a CV first, or use mode='guided' to build from scratch"
        )

    # Lazy gap analysis
    gap_result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(GapAnalysis.created_at.desc())
        .limit(1)
    )
    gap_analysis = gap_result.scalar_one_or_none()
    if gap_analysis is None:
        gap_response = await analyze_gaps(job_id, db, provider)
        ga_result2 = await db.execute(
            select(GapAnalysis).where(GapAnalysis.id == gap_response.id)
        )
        gap_analysis = ga_result2.scalar_one()

    cluster_ids, cluster_categories, clusters_by_id = gap_detector(gap_analysis)

    if not cluster_ids:
        state: InterviewState = _build_state(
            mode="targeted",
            job_id=job_id,
            gap_analysis_id=gap_analysis.id,
            profile_id=profile_record.id,
            critical_gaps=[],
            gap_categories={},
            gap_clusters_by_id={},
            current_question="",
            hard_ceiling=INTERVIEW_HARD_CEILING_TARGETED,
        )
        record = _make_session_record(
            job_id=job_id,
            gap_analysis_id=gap_analysis.id,
            profile_id=profile_record.id,
            mode="targeted",
            status="complete",
            state=state,
            hard_ceiling=INTERVIEW_HARD_CEILING_TARGETED,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        no_gaps_msg = "No critical gaps identified — your profile is a strong match!"
        return SessionCreateResponse(
            session_id=record.id,
            mode="targeted",
            first_question=no_gaps_msg,
            question=no_gaps_msg,
            estimated_questions=0,
            gaps_total=0,
            gaps_remaining=0,
        )

    first_cluster_id = cluster_ids[0]
    first_category = cluster_categories.get(first_cluster_id)
    state = _build_state(
        mode="targeted",
        job_id=job_id,
        gap_analysis_id=gap_analysis.id,
        profile_id=profile_record.id,
        critical_gaps=cluster_ids,
        gap_categories=cluster_categories,
        gap_clusters_by_id=clusters_by_id,
        current_question="",
        hard_ceiling=INTERVIEW_HARD_CEILING_TARGETED,
    )
    q_data = await question_generator_with_profile(
        state, profile_record.profile_json, provider, gap_category=first_category
    )
    first_question = q_data["question"]
    first_choices = q_data["choices"]
    state["current_question"] = first_question
    state["current_choices"] = first_choices
    state["messages"].append({"role": "assistant", "content": first_question})
    state["questions_asked"] = 1

    record = _make_session_record(
        job_id=job_id,
        gap_analysis_id=gap_analysis.id,
        profile_id=profile_record.id,
        mode="targeted",
        status="active",
        state=state,
        hard_ceiling=INTERVIEW_HARD_CEILING_TARGETED,
        questions_asked=1,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return SessionCreateResponse(
        session_id=record.id,
        mode="targeted",
        first_question=first_question,
        question=first_question,
        estimated_questions=_estimated_questions("targeted"),
        gaps_total=len(cluster_ids),
        gaps_remaining=len(cluster_ids),
        choices=first_choices,
    )


async def _create_guided_session(
    job_id: uuid.UUID,
    job: JobAnalysis,
    profile_record: MasterProfile | None,
    db: AsyncSession,
    provider: LLMProvider,
) -> SessionCreateResponse:
    # MODE B can start without a profile — create an empty stub if needed
    if profile_record is None:
        stub = MasterProfile(profile_json={})
        db.add(stub)
        await db.flush()
        profile_record = stub

    sections = gap_detector_mode_b(job)
    job_context = {
        "role_title": job.role_title or "",
        "seniority_level": job.seniority_level or "",
    }

    state: InterviewState = _build_state(
        mode="guided",
        job_id=job_id,
        gap_analysis_id=None,
        profile_id=profile_record.id,
        critical_gaps=sections,
        gap_categories={},
        gap_clusters_by_id={},
        current_question="",
        hard_ceiling=INTERVIEW_HARD_CEILING_GUIDED,
    )
    q_data = await question_generator_with_profile(
        state,
        profile_record.profile_json,
        provider,
        gap_category=None,
        job_context=job_context,
    )
    first_question = q_data["question"]
    state["current_question"] = first_question
    state["current_choices"] = None
    state["messages"].append({"role": "assistant", "content": first_question})
    state["questions_asked"] = 1

    record = _make_session_record(
        job_id=job_id,
        gap_analysis_id=None,
        profile_id=profile_record.id,
        mode="guided",
        status="active",
        state=state,
        hard_ceiling=INTERVIEW_HARD_CEILING_GUIDED,
        questions_asked=1,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return SessionCreateResponse(
        session_id=record.id,
        mode="guided",
        first_question=first_question,
        question=first_question,
        estimated_questions=_estimated_questions("guided"),
        gaps_total=len(sections),
        gaps_remaining=len(sections),
    )


async def _create_micro_session(
    job_id: uuid.UUID,
    job: JobAnalysis,
    profile_record: MasterProfile | None,
    target_cluster_id: str,
    db: AsyncSession,
    provider: LLMProvider,
) -> SessionCreateResponse:
    """Create a 1-question micro-session scoped to a single cluster (Gap-Click mode)."""
    if profile_record is None:
        raise LookupError(
            "No profile found — upload a CV first before using Gap-Click mode"
        )

    # Load latest gap analysis to find the cluster
    gap_result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(GapAnalysis.created_at.desc())
        .limit(1)
    )
    gap_analysis = gap_result.scalar_one_or_none()

    cluster: dict = {
        "id": target_cluster_id,
        "label": target_cluster_id,
        "gaps": [],
        "jd_skills": [],
        "jd_context": "",
    }
    gap_category: str | None = None
    if gap_analysis is not None:
        clusters_raw: list[dict] = list(gap_analysis.gap_clusters or [])
        for c in clusters_raw:
            if c.get("id") == target_cluster_id:
                cluster = c
                gap_category = c.get("category")
                break

    _MICRO_CEILING = 1
    state: InterviewState = _build_state(
        mode="targeted",
        job_id=job_id,
        gap_analysis_id=gap_analysis.id if gap_analysis else None,
        profile_id=profile_record.id,
        critical_gaps=[target_cluster_id],
        gap_categories={target_cluster_id: gap_category or "C"},
        gap_clusters_by_id={target_cluster_id: cluster},
        current_question="",
        hard_ceiling=_MICRO_CEILING,
    )
    q_data = await question_generator_with_profile(
        state, profile_record.profile_json, provider, gap_category=gap_category
    )
    first_question = q_data["question"]
    first_choices = q_data["choices"]
    state["current_question"] = first_question
    state["current_choices"] = first_choices
    state["messages"].append({"role": "assistant", "content": first_question})
    state["questions_asked"] = 1

    existing_active = await _get_active_session(job_id, db)
    if existing_active is not None:
        existing_active.status = "complete"
        existing_active.updated_at = datetime.now(timezone.utc)
        await db.flush()

    record = _make_session_record(
        job_id=job_id,
        gap_analysis_id=gap_analysis.id if gap_analysis else None,
        profile_id=profile_record.id,
        mode="targeted",
        status="active",
        state=state,
        hard_ceiling=_MICRO_CEILING,
        questions_asked=1,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return SessionCreateResponse(
        session_id=record.id,
        mode="targeted",
        first_question=first_question,
        question=first_question,
        estimated_questions=1,
        gaps_total=1,
        gaps_remaining=1,
        choices=first_choices,
    )


# ---------------------------------------------------------------------------
# POST /api/session/{session_id}/message
# ---------------------------------------------------------------------------


async def send_message(
    session_id: uuid.UUID,
    message: str,
    db: AsyncSession,
    provider: LLMProvider,
) -> SessionMessageResponse:
    # Load session
    session_result = await db.execute(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.deleted_at.is_(None),
        )
    )
    record = session_result.scalar_one_or_none()
    if record is None:
        raise LookupError(f"Session {session_id} not found")
    if record.status == "complete":
        raise ValueError("Session is already complete")

    state: InterviewState = dict(record.state)
    state["messages"].append({"role": "user", "content": message})

    # --- Done-signal check (pre-LLM, deterministic) ---
    if is_termination_signal(message):
        return await _complete_session(record, state, db, "user_ended")

    current_idx = state["current_gap_index"]
    current_gap = state["critical_gaps"][current_idx]
    current_question = state["current_question"]

    skipped_set = set(state.get("skipped_gaps", []))
    addressed_set = set(state.get("addressed_gaps", []))
    clusters_by_id = state.get("gap_clusters_by_id") or {}
    current_cluster = clusters_by_id.get(current_gap, {"label": current_gap})
    cluster_label = current_cluster.get("label", current_gap)

    # --- ResponseParser ---
    patch = await response_parser(
        cluster_label, current_question, message, provider
    )

    # --- ProfileUpdater ---
    profile_record = await _load_profile(state["profile_id"], db)
    updated_profile, merge_conflicts = profile_updater(profile_record.profile_json, patch)
    profile_record.profile_json = updated_profile
    profile_record.updated_at = datetime.now(timezone.utc)

    # Increment questions_asked
    questions_asked = state.get("questions_asked", 1) + 1
    state["questions_asked"] = questions_asked
    record.questions_asked = questions_asked

    # --- Hard ceiling check ---
    if questions_asked >= state["hard_ceiling"]:
        state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
        return await _complete_session(
            record, state, db, "max_questions_reached", profile_record
        )

    # --- Advance decision ---
    # "declined" advances like "full": the candidate has said they have no
    # experience for this gap, so drilling for a "more specific example" would
    # only ask about experience they don't have (bug 3 — interview over-drilling).
    gap_resolution = patch.get("gap_resolution", "none")
    questions_for_gap = state.get("questions_per_gap", {}).get(current_gap, 1)

    if gap_resolution in ("full", "declined") or questions_for_gap >= INTERVIEW_MAX_QUESTIONS_PER_GAP:
        # Advance to next gap
        state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
        skipped_set_updated = set(state.get("skipped_gaps", []))
        next_index = _next_valid_index(
            state["critical_gaps"], current_idx + 1, skipped_set_updated
        )
        state["current_gap_index"] = next_index
        gaps_remaining = _count_remaining(
            state["critical_gaps"], next_index, skipped_set_updated
        )

        # Gap exhaustion check
        if gaps_remaining <= 0:
            return await _complete_session(
                record, state, db, "gaps_resolved", profile_record
            )

        # Generate next question
        next_gap = state["critical_gaps"][next_index]
        next_category = (state.get("gap_categories") or {}).get(next_gap)
        job_context: dict | None = None
        if state.get("mode") == "guided":
            job_context = await _load_job_context(state["job_id"], db)

        next_q_data = await question_generator_with_profile(
            state,
            updated_profile,
            provider,
            gap_category=next_category,
            job_context=job_context,
        )
        next_question = next_q_data["question"]
        next_choices = next_q_data["choices"]
        state["current_question"] = next_question
        state["current_choices"] = next_choices
        state["messages"].append({"role": "assistant", "content": next_question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()

        return SessionMessageResponse(
            complete=False,
            question=next_question,
            gaps_remaining=gaps_remaining,
            pending_conflicts=merge_conflicts if merge_conflicts else None,
            choices=next_choices,
        )

    else:
        # Follow-up: stay on current gap
        qpg = dict(state.get("questions_per_gap", {}))
        qpg[current_gap] = questions_for_gap + 1
        state["questions_per_gap"] = qpg

        follow_up_hint = (
            patch.get("follow_up_hint")
            or f"ask for a more specific or concrete example related to {current_gap}"
        )
        gap_category = (state.get("gap_categories") or {}).get(current_gap)

        follow_up_data = await question_generator_with_profile(
            state,
            updated_profile,
            provider,
            gap_category=gap_category,
            follow_up_hint=follow_up_hint,
        )
        follow_up_question = follow_up_data["question"]
        state["current_question"] = follow_up_question
        state["current_choices"] = None
        state["messages"].append({"role": "assistant", "content": follow_up_question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()

        gaps_remaining = _count_remaining(
            state["critical_gaps"],
            current_idx,
            set(state.get("skipped_gaps", [])),
        )

        return SessionMessageResponse(
            complete=False,
            question=follow_up_question,
            gaps_remaining=gaps_remaining,
            pending_conflicts=merge_conflicts if merge_conflicts else None,
            choices=None,
        )


# ---------------------------------------------------------------------------
# GET /api/session/{session_id}
# ---------------------------------------------------------------------------


async def get_session_state(
    session_id: uuid.UUID,
    db: AsyncSession,
) -> SessionStateResponse:
    session_result = await db.execute(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.deleted_at.is_(None),
        )
    )
    record = session_result.scalar_one_or_none()
    if record is None:
        raise LookupError(f"Session {session_id} not found")

    state: InterviewState = dict(record.state)
    profile_record = await _load_profile(state["profile_id"], db)
    profile_data = MasterProfileData.model_validate(profile_record.profile_json)
    completeness = profile_data.calculate_completeness()

    current_question: str | None = None
    gaps_remaining = 0
    status_str: str = record.status

    if record.status == "active":
        current_question = state.get("current_question") or None
        idx = state.get("current_gap_index", 0)
        skipped = set(state.get("skipped_gaps", []))
        gaps_remaining = _count_remaining(
            state.get("critical_gaps", []),
            idx,
            skipped,
        )

    # Treat expired sessions (past expires_at) as "expired" status
    if (
        record.expires_at is not None
        and datetime.now(timezone.utc) > record.expires_at
        and record.status != "complete"
    ):
        status_str = "expired"

    return SessionStateResponse(
        session_id=record.id,
        job_id=record.job_analysis_id,
        mode=record.mode,
        status=status_str,
        questions_asked=record.questions_asked,
        hard_ceiling=record.hard_ceiling,
        current_question=current_question,
        gaps_remaining=gaps_remaining,
        completeness_score=completeness,
        created_at=record.created_at,
        updated_at=record.updated_at,
        expires_at=record.expires_at,
    )


# ---------------------------------------------------------------------------
# Completion helper
# ---------------------------------------------------------------------------


async def _complete_session(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    reason: str,
    profile_record: MasterProfile | None = None,
) -> SessionMessageResponse:
    record.state = state
    record.status = "complete"
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    completeness = 0.0
    if profile_record is not None:
        profile_data = MasterProfileData.model_validate(profile_record.profile_json)
        completeness = profile_data.calculate_completeness()

    addressed = state.get("addressed_gaps", [])
    all_gaps = state.get("critical_gaps", [])
    idx = state.get("current_gap_index", 0)
    skipped = set(state.get("skipped_gaps", []))
    addressed_set = set(addressed)
    unresolved = (
        [g for g in all_gaps[idx:] if g not in skipped and g not in addressed_set]
        if reason != "gaps_resolved"
        else []
    )

    return SessionMessageResponse(
        complete=True,
        reason=reason,
        questions_asked=state.get("questions_asked", record.questions_asked),
        gaps_resolved=len(addressed),
        gaps_unresolved=unresolved,
        completeness_score=completeness,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auto_detect_mode(profile_record: MasterProfile | None) -> str:
    if profile_record is None:
        return "guided"
    profile_data = MasterProfileData.model_validate(profile_record.profile_json)
    score = profile_data.calculate_completeness()
    return "targeted" if score >= MODE_B_COMPLETENESS_THRESHOLD else "guided"


def _estimated_questions(mode: str) -> int:
    if mode == "guided":
        return (INTERVIEW_TARGET_MIN_GUIDED + INTERVIEW_HARD_CEILING_GUIDED) // 2
    return (INTERVIEW_TARGET_MIN_TARGETED + INTERVIEW_HARD_CEILING_TARGETED) // 2


def _build_state(
    *,
    mode: str,
    job_id: uuid.UUID,
    gap_analysis_id: uuid.UUID | None,
    profile_id: uuid.UUID,
    critical_gaps: list[str],
    gap_categories: dict,
    gap_clusters_by_id: dict,
    current_question: str,
    hard_ceiling: int,
) -> InterviewState:
    return {
        "mode": mode,
        "job_id": str(job_id),
        "gap_analysis_id": str(gap_analysis_id) if gap_analysis_id else None,
        "profile_id": str(profile_id),
        "critical_gaps": critical_gaps,
        "gap_categories": gap_categories,
        "gap_clusters_by_id": gap_clusters_by_id,
        "addressed_gaps": [],
        "current_gap_index": 0,
        "current_question": current_question,
        "current_choices": None,
        "messages": [],
        "questions_asked": 0,
        "hard_ceiling": hard_ceiling,
        "questions_per_gap": {},
        "skipped_gaps": [],
        "full_gaps": [],
        "na_gaps": [],
    }


def _make_session_record(
    *,
    job_id: uuid.UUID,
    gap_analysis_id: uuid.UUID | None,
    profile_id: uuid.UUID,
    mode: str,
    status: str,
    state: InterviewState,
    hard_ceiling: int,
    questions_asked: int = 0,
) -> InterviewSession:
    now = datetime.now(timezone.utc)
    return InterviewSession(
        job_analysis_id=job_id,
        gap_analysis_id=gap_analysis_id,
        profile_id=profile_id,
        mode=mode,
        status=status,
        state=state,
        hard_ceiling=hard_ceiling,
        questions_asked=questions_asked,
        expires_at=now + timedelta(days=_SESSION_TTL_DAYS),
    )


async def _get_active_session(
    job_id: uuid.UUID, db: AsyncSession
) -> InterviewSession | None:
    result = await db.execute(
        select(InterviewSession).where(
            InterviewSession.job_analysis_id == job_id,
            InterviewSession.status == "active",
            InterviewSession.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def _load_profile(profile_id: str, db: AsyncSession) -> MasterProfile:
    result = await db.execute(
        select(MasterProfile).where(MasterProfile.id == uuid.UUID(profile_id))
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise LookupError(f"Profile {profile_id} not found")
    return record


async def _load_job_context(job_id: str, db: AsyncSession) -> dict:
    result = await db.execute(
        select(JobAnalysis).where(JobAnalysis.id == uuid.UUID(job_id))
    )
    job = result.scalar_one_or_none()
    if job is None:
        return {}
    return {
        "role_title": job.role_title or "",
        "seniority_level": job.seniority_level or "",
    }


def _next_valid_index(
    critical_gaps: list[str],
    from_index: int,
    skipped_gaps: set[str],
) -> int:
    """Return the first index >= from_index whose gap is not in skipped_gaps.

    Returns len(critical_gaps) if all remaining gaps are skipped (signals exhaustion).
    """
    idx = from_index
    while idx < len(critical_gaps) and critical_gaps[idx] in skipped_gaps:
        idx += 1
    return idx


def _count_remaining(
    critical_gaps: list[str],
    from_index: int,
    skipped_gaps: set[str],
) -> int:
    """Count non-skipped gaps from from_index onwards (inclusive)."""
    return sum(
        1 for g in critical_gaps[from_index:]
        if g not in skipped_gaps
    )
