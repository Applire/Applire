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

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
from applire.models.user_settings import UserSettings
from applire.services.color_detection import _CE_STUB_USER_ID
from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import MasterProfileData
from applire.schemas.session import (
    ConfirmationPrompt,
    ConflictSummary,
    InterviewState,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionMessageResponse,
    SessionStateResponse,
)
from applire.services.gap import analyze_gaps, has_clustering_input
from applire.services.interview.signals import is_termination_signal
from applire.services.keyword_ledger import upgrade_ledger_for_concepts
from applire.services.interview_graph import (
    build_confirmation_clusters,
    build_conflict_clusters,
    build_gate_clusters,
    gap_detector,
    gap_detector_mode_b,
    interpret_conflict_answer,
    interpret_gate_answer,
    is_confirmation_cluster,
    is_conflict_cluster,
    is_gate_cluster,
    question_generator_with_profile,
)
from applire.services.profile.reconcile.interview_bridge import (
    reconcile_interview_turn,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# UI language resolver
# ---------------------------------------------------------------------------


async def get_ui_language(db: AsyncSession) -> str:
    """Resolve the user's UI language for conversational LLM output (ADR-038).

    Reads the CE stub user's settings; returns 'en' when no row exists.
    Single seam for the future multi-user (OIDC) lookup.
    """
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == _CE_STUB_USER_ID)
    )
    row = result.scalar_one_or_none()
    return (row.ui_language if row else None) or "en"


# ---------------------------------------------------------------------------
# US163 — deferred integrity gate injection (ADR-041 amended)
# ---------------------------------------------------------------------------


def _account_name(profile_record: MasterProfile | None) -> str | None:
    if profile_record is None:
        return None
    return ((profile_record.profile_json or {}).get("personal_info") or {}).get("name")


async def _pending_gate_clusters(
    db: AsyncSession, lang: str, account_name: str | None
) -> tuple[list[str], dict, dict]:
    """Build gate-first pseudo-clusters for every open parked gate (US167).

    Returns the GapDetector-shaped ``(ids, categories, clusters_by_id)`` so the
    caller can prepend the gate ids ahead of the JD gaps, mandatory and
    job-irrelevant. Empty when nothing is parked — the no-gate path is unchanged.
    """
    from applire.services.profile import list_open_gates  # lazy: avoid import cycle

    records = await list_open_gates(db)
    if not records:
        return [], {}, {}
    inputs = [
        {
            "upload_id": r.id,
            "gate": r.gate_status,
            "account_name": account_name,
            "cv_name": ((r.staged_extraction or {}).get("personal_info") or {}).get("name"),
        }
        for r in records
    ]
    return build_gate_clusters(inputs, lang)


def _gate_entry(state: InterviewState, cluster_id: str) -> dict | None:
    """The stored gate descriptor for a critical-gaps entry, or None if not a gate."""
    if not is_gate_cluster(cluster_id):
        return None
    return (state.get("gate_clusters") or {}).get(cluster_id)


async def _resolve_gate(
    db: AsyncSession, upload_id: str, action: str, provider: LLMProvider
) -> None:
    """Apply the user's gate decision, tolerating an already-resolved upload.

    The merge reconciles the parked extraction into the master profile via the
    ADR-046 engine, so the interview's provider is threaded through (rather than
    the global factory) — that keeps the merge on the same LLM the session uses
    and makes the path injectable in tests.
    """
    from applire.services.profile import (  # lazy: avoid import cycle
        StagedExtractionAlreadyResolved,
        StagedExtractionNotFound,
        resolve_staged_extraction,
    )

    try:
        await resolve_staged_extraction(
            db, uuid.UUID(upload_id), action=action, provider=provider
        )
    except (StagedExtractionAlreadyResolved, StagedExtractionNotFound):
        # Idempotent on resume / TTL eviction — the gate is no longer open, so
        # there is nothing to apply; advancing past it is the correct behaviour.
        pass


async def _ask_or_complete_at(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    next_index: int,
    lang: str,
) -> SessionMessageResponse:
    """Position the session at ``next_index`` and emit its question (gate-aware)
    or complete when no gaps remain. Used after a gate is resolved."""
    skipped = set(state.get("skipped_gaps", []))
    next_index = _next_valid_index(state["critical_gaps"], next_index, skipped)
    state["current_gap_index"] = next_index
    gaps_remaining = _count_remaining(state["critical_gaps"], next_index, skipped)

    profile_record = await _load_profile(state["profile_id"], db)
    if gaps_remaining <= 0:
        return await _complete_session(record, state, db, "gaps_resolved", profile_record)

    next_gap = state["critical_gaps"][next_index]
    gate_entry = _gate_entry(state, next_gap)
    conflict_entry = _conflict_entry(state, next_gap)
    confirmation_entry = _confirmation_entry(state, next_gap)
    if gate_entry is not None:
        next_question = gate_entry["question"]
        next_choices = gate_entry["choices"]
    elif conflict_entry is not None:
        next_question = conflict_entry["question"]
        next_choices = conflict_entry["choices"]
    elif confirmation_entry is not None:
        next_question = confirmation_entry["question"]
        next_choices = confirmation_entry["choices"]
    else:
        next_category = (state.get("gap_categories") or {}).get(next_gap)
        job_context = (
            await _load_job_context(state["job_id"], db)
            if state.get("mode") == "guided"
            else None
        )
        q_data = await question_generator_with_profile(
            state, profile_record.profile_json, provider,
            gap_category=next_category, job_context=job_context, lang=lang,
        )
        next_question = q_data["question"]
        next_choices = q_data["choices"]

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
        choices=next_choices,
    )


async def _handle_gate_answer(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    current_idx: int,
    gate_entry: dict,
    message: str,
) -> SessionMessageResponse:
    """Resolve a deferred Tier-1 gate from the user's answer (US163).

    A clear yes/no merges or discards the parked upload and advances; an unclear
    answer re-asks the same blocking question (safe default: never auto-merge).
    """
    decision = interpret_gate_answer(message)

    if decision == "unclear":
        question = gate_entry["question"]
        choices = gate_entry["choices"]
        state["current_question"] = question
        state["current_choices"] = choices
        state["messages"].append({"role": "assistant", "content": question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()
        gaps_remaining = _count_remaining(
            state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
        )
        return SessionMessageResponse(
            complete=False, question=question, gaps_remaining=gaps_remaining, choices=choices
        )

    action = "merge" if decision == "merge" else "discard"
    await _resolve_gate(db, gate_entry["upload_id"], action, provider)

    current_gap = state["critical_gaps"][current_idx]
    state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
    state["questions_asked"] = state.get("questions_asked", 0) + 1
    record.questions_asked = state["questions_asked"]

    lang = await get_ui_language(db)
    return await _ask_or_complete_at(record, state, db, provider, current_idx + 1, lang)


# ---------------------------------------------------------------------------
# US165 — standalone profile-review interview (no JD): conflict resolution
# ---------------------------------------------------------------------------


def _conflict_entry(state: InterviewState, cluster_id: str) -> dict | None:
    """The stored conflict descriptor for a critical-gaps entry, or None."""
    if not is_conflict_cluster(cluster_id):
        return None
    return (state.get("conflict_clusters") or {}).get(cluster_id)


def _confirmation_entry(state: InterviewState, cluster_id: str) -> dict | None:
    """The stored confirmation descriptor for a critical-gaps entry, or None.

    E037 PQ #4 — an N-option import ambiguity surfaced in the profile-review
    interview (question + per-option buttons), distinct from a 2-value conflict.
    """
    if not is_confirmation_cluster(cluster_id):
        return None
    return (state.get("confirmation_clusters") or {}).get(cluster_id)


async def _resolve_confirmation_safely(
    db: AsyncSession, confirmation_id: str, chosen_option: str
) -> None:
    """Record the user's confirmation choice, tolerating an already-resolved one."""
    from applire.services.profile import resolve_confirmation  # lazy: avoid cycle

    try:
        await resolve_confirmation(confirmation_id, chosen_option, db)
    except LookupError:
        # Idempotent on resume: the confirmation is already gone from pending, so
        # advancing past it is correct.
        pass


async def _handle_confirmation_answer(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    current_idx: int,
    confirmation_entry: dict,
    message: str,
) -> SessionMessageResponse:
    """Resolve a pending import-time confirmation from the user's choice (E037 PQ #4).

    Deterministic (no LLM): the user picks one of the engine's options. Any
    non-empty answer records the choice and advances; an empty answer re-asks the
    same question + options (never guess an identity judgement)."""
    options = confirmation_entry.get("options") or confirmation_entry.get("choices") or []
    chosen = (message or "").strip()

    if not chosen:
        question = confirmation_entry["question"]
        state["current_question"] = question
        state["current_choices"] = options
        state["messages"].append({"role": "assistant", "content": question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()
        gaps_remaining = _count_remaining(
            state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
        )
        return SessionMessageResponse(
            complete=False, question=question, gaps_remaining=gaps_remaining, choices=options
        )

    await _resolve_confirmation_safely(db, confirmation_entry["confirmation_id"], chosen)

    current_gap = state["critical_gaps"][current_idx]
    state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
    state["questions_asked"] = state.get("questions_asked", 0) + 1
    record.questions_asked = state["questions_asked"]

    lang = await get_ui_language(db)
    return await _ask_or_complete_at(record, state, db, provider, current_idx + 1, lang)


def _skill_confirmation_decision(chosen: str) -> str:
    """Map the user's picked option to a skill-dedupe resolution (#187).

    Robust to the two skill-confirmation shapes (single-token containment and
    multi-atom overlap) by matching the answer text, not an option index:
    ``"distinct"`` keeps the incoming as its own skill, ``"merge"`` folds it into
    the existing one, ``"keep"`` discards the incoming (keep the existing skills).
    An unrecognised non-empty answer defaults to ``"distinct"`` — never silently
    drop the user's skill."""
    c = (chosen or "").strip().lower()
    if "separate" in c:
        return "distinct"
    if "keep" in c and "existing" in c:
        return "keep"
    if "merge" in c:
        return "merge"
    return "distinct"


def _apply_interview_confirmation(
    profile_json: dict, options: list[str], context: dict, chosen: str
) -> dict | None:
    """Apply a resolved interview-turn skill confirmation to the profile (#187).

    Returns the updated profile JSONB dict, or ``None`` when there is nothing to
    apply deterministically (a non-skill confirmation, or "keep the existing
    skills"). The caller advances the interview either way, which is what closes
    the loop. Skill dedupe is the reported vector: the ADR-046 applier is reused
    with a ``user_confirmed`` flag that BYPASSES the stateless containment guard.
    """
    incoming = context.get("incoming_skill")
    if not incoming:
        # Not a skill confirmation (entity near-dupe etc.) — advancing is enough
        # to break the loop; entity-merge resolution is out of #187's scope.
        return None

    decision = _skill_confirmation_decision(chosen)
    if decision == "keep":
        return None  # discard the incoming — the existing skills stand unchanged

    from applire.services.profile.reconcile.apply import _apply_upsert_skill
    from applire.services.profile.reconcile.ops import UpsertSkill

    profile = MasterProfileData.model_validate(profile_json)
    op = UpsertSkill(
        name=incoming,
        category=context.get("category"),
        proficiency=context.get("proficiency"),
        evidence=list(context.get("evidence_refs") or []),
    )

    def resolve(handle):
        for entry in (
            *profile.work_experience,
            *profile.projects,
            *profile.volunteer_activities,
        ):
            if getattr(entry, "id", None) == handle:
                return entry
        return None

    changes: list = []
    pending: list = []
    _apply_upsert_skill(
        op, profile, resolve, changes, pending, user_confirmed=decision
    )
    return profile.model_dump(mode="json")


async def _handle_interview_confirmation_answer(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    current_idx: int,
    pending_conf: dict,
    message: str,
    lang: str,
) -> SessionMessageResponse:
    """Resolve a reconciler-emitted interview-turn confirmation (#187).

    Deterministic (no LLM re-run): the user's choice is applied via the carried
    context, then the interview advances. An empty answer re-asks the same
    question + options (never guess)."""
    options = pending_conf.get("options") or []
    context = pending_conf.get("context") or {}
    chosen = (message or "").strip()

    if not chosen:
        question = pending_conf.get("question", "")
        state["current_question"] = question
        state["current_choices"] = options
        state["messages"].append({"role": "assistant", "content": question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()
        gaps_remaining = _count_remaining(
            state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
        )
        return SessionMessageResponse(
            complete=False, question=question, gaps_remaining=gaps_remaining,
            choices=options,
        )

    profile_record = await _load_profile(state["profile_id"], db)
    applied = _apply_interview_confirmation(
        profile_record.profile_json, options, context, chosen
    )
    if applied is not None:
        profile_record.profile_json = applied
        profile_record.updated_at = datetime.now(timezone.utc)

    # Consume the pending confirmation AND the one-shot flag — the loop is closed.
    state.pop("pending_interview_confirmation", None)
    state.pop("resolving_confirmation", None)

    current_gap = state["critical_gaps"][current_idx]
    if current_gap not in state.get("addressed_gaps", []):
        state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
    state["questions_asked"] = state.get("questions_asked", 0) + 1
    record.questions_asked = state["questions_asked"]

    return await _ask_or_complete_at(record, state, db, provider, current_idx + 1, lang)


async def _resolve_conflict_safely(
    db: AsyncSession, conflict_id: str, resolution: str
) -> None:
    """Apply the user's correction, tolerating an already-resolved conflict."""
    from applire.services.profile import resolve_conflict  # lazy: avoid import cycle

    try:
        await resolve_conflict(conflict_id, resolution, None, db)
    except LookupError:
        # Idempotent on resume: the conflict is already gone from pending, so
        # advancing past it is correct.
        pass


async def _handle_conflict_answer(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    provider: LLMProvider,
    current_idx: int,
    conflict_entry: dict,
    message: str,
) -> SessionMessageResponse:
    """Resolve a pending Tier-2 conflict from the user's answer (US165).

    A clear keep/use choice writes through the ADR-013 merge (manual_edit
    EnrichmentRecord) and advances; an ambiguous answer re-asks the same
    question (safe default: never guess a factual correction)."""
    decision = interpret_conflict_answer(
        message, conflict_entry["existing_value"], conflict_entry["incoming_value"]
    )

    if decision == "unclear":
        question = conflict_entry["question"]
        choices = conflict_entry["choices"]
        state["current_question"] = question
        state["current_choices"] = choices
        state["messages"].append({"role": "assistant", "content": question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()
        gaps_remaining = _count_remaining(
            state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
        )
        return SessionMessageResponse(
            complete=False, question=question, gaps_remaining=gaps_remaining, choices=choices
        )

    resolution = "existing" if decision == "existing" else "incoming"
    await _resolve_conflict_safely(db, conflict_entry["conflict_id"], resolution)

    current_gap = state["critical_gaps"][current_idx]
    state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
    state["questions_asked"] = state.get("questions_asked", 0) + 1
    record.questions_asked = state["questions_asked"]

    lang = await get_ui_language(db)
    return await _ask_or_complete_at(record, state, db, provider, current_idx + 1, lang)


def _to_confirmation_prompts(confirmations) -> list[ConfirmationPrompt]:
    """Map engine RequestConfirmation ops to the API confirmation DTO (US185)."""
    return [
        ConfirmationPrompt(
            question=c.question, options=list(c.options), context=dict(c.context)
        )
        for c in confirmations
    ]


async def _ask_confirmation(
    record: InterviewSession,
    state: InterviewState,
    db: AsyncSession,
    turn,
    current_gap: str,
    current_idx: int,
) -> SessionMessageResponse:
    """Surface a reconciler ambiguity as a targeted confirmation question (US185).

    The underlying answer was already applied (the ambiguity is a refinement, not
    the gap going unmet), so the current gap is marked addressed and a one-shot
    ``resolving_confirmation`` flag makes the *next* turn advance rather than
    re-ask. The engine resolves the entity identity from the user's answer — the
    system asks, it never guesses (mirrors the US163/US165 confirm principle).

    #187 — the confirmation op (question, options, context) is persisted in state
    so the NEXT turn resolves it DETERMINISTICALLY (no LLM re-run), mirroring the
    import-time ``_handle_confirmation_answer`` mechanism. Without this the
    stateless reconciler re-emits the identical confirmation every turn and the
    interview loops forever."""
    confirmation = turn.pending_confirmations[0]
    if current_gap not in state.get("addressed_gaps", []):
        state["addressed_gaps"] = state.get("addressed_gaps", []) + [current_gap]
    state["resolving_confirmation"] = True
    state["pending_interview_confirmation"] = {
        "question": confirmation.question,
        "options": list(confirmation.options),
        "context": dict(confirmation.context),
    }
    state["current_question"] = confirmation.question
    state["current_choices"] = list(confirmation.options)
    state["messages"].append({"role": "assistant", "content": confirmation.question})
    record.state = state
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()
    gaps_remaining = _count_remaining(
        state["critical_gaps"], current_idx, set(state.get("skipped_gaps", []))
    )
    return SessionMessageResponse(
        complete=False,
        question=confirmation.question,
        gaps_remaining=gaps_remaining,
        choices=list(confirmation.options),
        pending_confirmations=_to_confirmation_prompts(turn.pending_confirmations),
        pending_conflicts=turn.conflict_summaries or None,
    )


async def _get_active_profile_review_session(
    db: AsyncSession,
) -> InterviewSession | None:
    """The active standalone profile-review session, if one is in flight.

    A profile-review session is recorded as ``mode='guided'`` with no
    ``job_analysis_id``. Real guided (JD) sessions always carry a job, and the
    Mode-C enrichment sessions are ``mode='profile_enrich'``, so
    "guided + job IS NULL" identifies a profile review uniquely (resume-safe,
    ADR-004)."""
    result = await db.execute(
        select(InterviewSession)
        .where(
            InterviewSession.job_analysis_id.is_(None),
            InterviewSession.mode == "guided",
            InterviewSession.status == "active",
            InterviewSession.deleted_at.is_(None),
        )
        .order_by(InterviewSession.created_at.desc())
    )
    return result.scalars().first()


async def _open_conflicts(profile_record: MasterProfile) -> list[dict]:
    """Unresolved ADR-013 conflicts on the profile, shaped for the cluster builder."""
    profile_data = MasterProfileData.model_validate(profile_record.profile_json)
    if profile_data.metadata is None:
        return []
    return [
        {
            "conflict_id": c.conflict_id,
            "section": c.section,
            "field": c.field,
            "existing_value": c.existing_value,
            "incoming_value": c.incoming_value,
        }
        for c in profile_data.metadata.pending_conflicts
        if not c.resolved
    ]


async def _open_confirmations(profile_record: MasterProfile) -> list[dict]:
    """Unresolved import-time confirmations (E037 PQ #4), shaped for the cluster
    builder. Each is an N-option ambiguity the reconciler could not auto-resolve."""
    profile_data = MasterProfileData.model_validate(profile_record.profile_json)
    if profile_data.metadata is None:
        return []
    return [
        {
            "confirmation_id": c.confirmation_id,
            "question": c.question,
            "options": list(c.options),
        }
        for c in profile_data.metadata.pending_confirmations
        if not c.resolved
    ]


async def create_profile_review_session(
    db: AsyncSession,
    provider: LLMProvider,
    lang: str | None = None,
) -> SessionCreateResponse:
    """Launch the standalone profile-review interview (US165).

    No ``job_id``: the session walks the user's open Tier-2 conflicts and resolves
    each in place through the ADR-013 merge. Resume-safe — a second call returns
    the in-flight session rather than starting a new one.
    """
    profile_result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile_record = profile_result.scalar_one_or_none()
    if profile_record is None:
        raise LookupError("No profile found — upload a CV first")

    if lang is None:
        lang = await get_ui_language(db)

    existing = await _get_active_profile_review_session(db)
    if existing is not None:
        return _resumed_response(existing)

    conflicts = await _open_conflicts(profile_record)
    conflict_ids, conflict_categories, conflict_by_id = build_conflict_clusters(
        conflicts, lang
    )
    # E037 PQ #4 — import-time ambiguities surface alongside conflicts, as their
    # own N-option question + per-option buttons (not a garbled 2-value conflict).
    confirmations = await _open_confirmations(profile_record)
    confirm_ids, confirm_categories, confirm_by_id = build_confirmation_clusters(
        confirmations, lang
    )

    review_ids = conflict_ids + confirm_ids
    review_categories = {**conflict_categories, **confirm_categories}
    review_by_id = {**conflict_by_id, **confirm_by_id}

    state: InterviewState = _build_state(
        mode="guided",
        job_id=None,
        gap_analysis_id=None,
        profile_id=profile_record.id,
        critical_gaps=review_ids,
        gap_categories=review_categories,
        gap_clusters_by_id=review_by_id,
        current_question="",
        hard_ceiling=INTERVIEW_HARD_CEILING_GUIDED,
    )
    state["entry"] = "profile_review"
    state["conflict_clusters"] = conflict_by_id
    state["confirmation_clusters"] = confirm_by_id

    # Nothing flagged — record a complete session and tell the user they're clear.
    if not review_ids:
        record = _make_session_record(
            job_id=None,
            gap_analysis_id=None,
            profile_id=profile_record.id,
            mode="guided",
            status="complete",
            state=state,
            hard_ceiling=INTERVIEW_HARD_CEILING_GUIDED,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        all_clear = "No open issues to review — your Master Profile is in good shape!"
        return SessionCreateResponse(
            session_id=record.id,
            mode="guided",
            first_question=all_clear,
            question=all_clear,
            estimated_questions=0,
            gaps_total=0,
            gaps_remaining=0,
        )

    first_entry = review_by_id[review_ids[0]]
    first_question = first_entry["question"]
    first_choices = first_entry["choices"]
    state["current_question"] = first_question
    state["current_choices"] = first_choices
    state["messages"].append({"role": "assistant", "content": first_question})
    state["questions_asked"] = 1

    record = _make_session_record(
        job_id=None,
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
        gaps_total=len(review_ids),
        gaps_remaining=len(review_ids),
        choices=first_choices,
    )


# ---------------------------------------------------------------------------
# POST /api/session
# ---------------------------------------------------------------------------


async def gap_cluster_ids(job_id: uuid.UUID, db: AsyncSession) -> list[str]:
    """Return the gap-cluster ids from the job's latest gap analysis.

    Used by the agent channel (`resolve_gap`) to validate a caller-supplied
    ``gap_id`` with exact membership before opening a targeted micro-session —
    the same fail-fast discipline `submit_claims` applies to ledger concepts,
    so an agent gets the valid ids back instead of a silently generic session.
    Returns ``[]`` when no analysis exists yet.
    """
    result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(GapAnalysis.created_at.desc())
        .limit(1)
    )
    gap_analysis = result.scalar_one_or_none()
    if gap_analysis is None:
        return []
    return [c.get("id") for c in (gap_analysis.gap_clusters or []) if c.get("id")]


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

    # Resolve UI language once per request (ADR-038)
    lang = await get_ui_language(db)

    # --- Micro-session: target_gap scopes to a single gap (Gap-Click mode, 19.9) ---
    if request.target_gap and resolved_mode == "targeted":
        return await _create_micro_session(job_id, job, profile_record, request.target_gap, db, provider, lang)

    # --- Idempotency: return existing active session if one exists for this job ---
    existing = await _get_active_session(job_id, db)
    if existing is not None:
        return _resumed_response(existing)

    try:
        # --- MODE A: Targeted Gap-Fill ---
        if resolved_mode == "targeted":
            return await _create_targeted_session(job_id, job, profile_record, db, provider, lang)

        # --- MODE B: Guided Build ---
        return await _create_guided_session(job_id, job, profile_record, db, provider, lang)
    except IntegrityError:
        # Lost a create race: a concurrent request (e.g. React StrictMode
        # double-fire) committed its session after our idempotency check but
        # before our insert.  The unique active-per-job index rejected ours —
        # return the winner instead of surfacing a 500.
        await db.rollback()
        winner = await _get_active_session(job_id, db)
        if winner is None:
            raise
        return _resumed_response(winner)


def _resumed_response(existing: InterviewSession) -> SessionCreateResponse:
    state: InterviewState = dict(existing.state)
    gaps_total = len(state.get("critical_gaps", []))
    gaps_remaining = gaps_total - state.get("current_gap_index", 0)
    estimated = _estimated_questions(existing.mode)
    current_q = state.get("current_question", "")
    current_choices = state.get("current_choices")
    # `resumed` must reflect genuine in-progress work, not mere session
    # pre-existence (issue #44).  The onboarding overlay pre-creates the guided
    # session before routing to /interview, so the interview page's own
    # idempotent create call always lands here — but the user has answered
    # nothing yet.  A freshly created session sits at questions_asked == 1 (first
    # question generated, zero answers); only once the user has answered at least
    # one question (questions_asked > 1) is there somewhere to "continue from".
    answered = (state.get("questions_asked", existing.questions_asked) or 0) > 1
    return SessionCreateResponse(
        session_id=existing.id,
        mode=existing.mode,
        first_question=current_q,
        question=current_q,
        estimated_questions=estimated,
        gaps_total=gaps_total,
        gaps_remaining=gaps_remaining,
        choices=current_choices,
        resumed=answered,
    )


async def _create_targeted_session(
    job_id: uuid.UUID,
    job: JobAnalysis,
    profile_record: MasterProfile | None,
    db: AsyncSession,
    provider: LLMProvider,
    lang: str = "en",
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

    # US163: prepend any open deferred Tier-1 gate ahead of the JD gaps —
    # mandatory and job-irrelevant.
    gate_ids, gate_categories, gate_by_id = await _pending_gate_clusters(
        db, lang, _account_name(profile_record)
    )
    critical_gaps = gate_ids + cluster_ids
    gap_categories = {**cluster_categories, **gate_categories}
    gap_clusters_by_id = {**clusters_by_id, **gate_by_id}

    if not critical_gaps:
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
        # #166: "strong match" is only honest when there really are NO critical
        # gaps. has_clustering_input() mirrors cluster_gaps()'s OWN augmented
        # input (persisted category_c PLUS keyword-only honest gaps, #166
        # Important-1) — checking raw category_c alone missed the case where
        # category_c is empty but keyword-only honest gaps were non-empty, so
        # clustering had real input yet still produced nothing askable. When
        # that shared predicate says there was something to cluster, clustering
        # silently failed (JSON-object-mode parse loss, or the keyword-only path)
        # — telling the candidate they're a strong match is a dangerous lie.
        # Emit an honest fallback instead.
        if has_clustering_input(gap_analysis):
            logger.warning(
                "targeted session %s: clustering had input (category_c=%d) but no "
                "askable clusters/gates — clustering likely failed; emitting honest "
                "fallback",
                record.id,
                len(gap_analysis.category_c or []),
            )
            no_gaps_msg = (
                "The gap interview is currently unavailable — "
                "you can proceed to CV generation."
            )
        else:
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

    state = _build_state(
        mode="targeted",
        job_id=job_id,
        gap_analysis_id=gap_analysis.id,
        profile_id=profile_record.id,
        critical_gaps=critical_gaps,
        gap_categories=gap_categories,
        gap_clusters_by_id=gap_clusters_by_id,
        current_question="",
        hard_ceiling=INTERVIEW_HARD_CEILING_TARGETED,
    )
    state["gate_clusters"] = gate_by_id

    first_cluster_id = critical_gaps[0]
    gate_entry = gate_by_id.get(first_cluster_id)
    if gate_entry is not None:
        first_question = gate_entry["question"]
        first_choices = gate_entry["choices"]
    else:
        first_category = gap_categories.get(first_cluster_id)
        q_data = await question_generator_with_profile(
            state, profile_record.profile_json, provider,
            gap_category=first_category, lang=lang,
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
        gaps_total=len(critical_gaps),
        gaps_remaining=len(critical_gaps),
        choices=first_choices,
    )


async def _create_guided_session(
    job_id: uuid.UUID,
    job: JobAnalysis,
    profile_record: MasterProfile | None,
    db: AsyncSession,
    provider: LLMProvider,
    lang: str = "en",
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

    # US163: an open deferred gate blocks even a from-scratch guided build.
    gate_ids, gate_categories, gate_by_id = await _pending_gate_clusters(
        db, lang, _account_name(profile_record)
    )
    critical_gaps = gate_ids + sections

    state: InterviewState = _build_state(
        mode="guided",
        job_id=job_id,
        gap_analysis_id=None,
        profile_id=profile_record.id,
        critical_gaps=critical_gaps,
        gap_categories=gate_categories,
        gap_clusters_by_id=gate_by_id,
        current_question="",
        hard_ceiling=INTERVIEW_HARD_CEILING_GUIDED,
    )
    state["gate_clusters"] = gate_by_id

    first_cluster_id = critical_gaps[0]
    gate_entry = gate_by_id.get(first_cluster_id)
    if gate_entry is not None:
        first_question = gate_entry["question"]
        first_choices = gate_entry["choices"]
    else:
        q_data = await question_generator_with_profile(
            state,
            profile_record.profile_json,
            provider,
            gap_category=None,
            job_context=job_context,
            lang=lang,
        )
        first_question = q_data["question"]
        first_choices = None
    state["current_question"] = first_question
    state["current_choices"] = first_choices
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
        gaps_total=len(critical_gaps),
        gaps_remaining=len(critical_gaps),
        choices=first_choices,
    )


async def _create_micro_session(
    job_id: uuid.UUID,
    job: JobAnalysis,
    profile_record: MasterProfile | None,
    target_cluster_id: str,
    db: AsyncSession,
    provider: LLMProvider,
    lang: str = "en",
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
        state, profile_record.profile_json, provider, gap_category=gap_category, lang=lang
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


async def _upgrade_ledger_for_addressed_gap(
    state: InterviewState,
    current_gap: str,
    answer: str,
    db: AsyncSession,
) -> None:
    """When an interview turn ADDRESSED a gap, upgrade the matching keyword_ledger
    entries on the persisted GapAnalysis row IN PLACE (#188) — no LLM re-run.

    The ledger is built ONCE during gap analysis and persisted on
    ``GapAnalysis.keyword_ledger``; BOTH the CV and cover-letter generators read
    that same row. A strength the candidate has now CONFIRMED in the interview
    otherwise stays classed as an honest gap, so the cover letter hedges it as a
    growth area — contradicting the CV. Flipping the matched entry to claimable
    (with the answer text as evidence) fixes both documents from the one row.

    This is the single backend seam that covers ALL interview channels — the full
    ``/interview`` flow, the inline micro-session, and the MCP ``run_interview`` —
    since every one of them funnels through ``send_message``.

    Conservative (truthfulness): only the CURRENT cluster's concepts are eligible,
    and only honest-gap entries that normalize-match them are touched. NO-OP (never
    fabricate) when the session has no ledger (guided / Mode B — ``gap_analysis_id``
    is None), the cluster has no concepts, the row has no ledger, or nothing matches.
    """
    gap_analysis_id = state.get("gap_analysis_id")
    if not gap_analysis_id:
        return  # guided / Mode B sessions have no ledger — never fabricate one

    cluster = (state.get("gap_clusters_by_id") or {}).get(current_gap)
    if not cluster:
        return
    concepts = [c for c in (cluster.get("gaps") or []) if c]
    if not concepts:
        return

    result = await db.execute(
        select(GapAnalysis).where(GapAnalysis.id == uuid.UUID(str(gap_analysis_id)))
    )
    gap = result.scalar_one_or_none()
    if gap is None or not gap.keyword_ledger:
        return

    new_ledger, changed = upgrade_ledger_for_concepts(
        gap.keyword_ledger, concepts, answer
    )
    if changed:
        # JSONB tracking gotcha: keyword_ledger is a plain _JSON column, NOT a
        # MutableList — mutating a dict inside the list in place would not be
        # flagged dirty. Reassign the WHOLE attribute (a freshly built list) so
        # SQLAlchemy persists it. Entry values are JSON-native (str/bool/float/
        # list), so no model_dump(mode="json") coercion is needed here.
        gap.keyword_ledger = new_ledger


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

    # Resolve UI language once for this turn (ADR-038)
    lang = await get_ui_language(db)

    state: InterviewState = dict(record.state)
    state["messages"].append({"role": "user", "content": message})

    # --- Done-signal check (pre-LLM, deterministic) ---
    if is_termination_signal(message):
        return await _complete_session(record, state, db, "user_ended")

    current_idx = state["current_gap_index"]
    current_gap = state["critical_gaps"][current_idx]
    current_question = state["current_question"]

    # --- US163: a deferred Tier-1 gate is resolved deterministically (no LLM),
    # never run through the gap response parser / profile updater. ---
    gate_entry = _gate_entry(state, current_gap)
    if gate_entry is not None:
        return await _handle_gate_answer(
            record, state, db, provider, current_idx, gate_entry, message
        )

    # --- US165: a pending Tier-2 conflict is resolved deterministically (no LLM),
    # through the ADR-013 merge — never the gap response parser / profile updater. ---
    conflict_entry = _conflict_entry(state, current_gap)
    if conflict_entry is not None:
        return await _handle_conflict_answer(
            record, state, db, provider, current_idx, conflict_entry, message
        )

    # --- E037 PQ #4: a pending import-time confirmation (N-option ambiguity) is
    # resolved deterministically (no LLM) — the user picks one of the engine's
    # options; never run through the gap response parser / profile updater. ---
    confirmation_entry = _confirmation_entry(state, current_gap)
    if confirmation_entry is not None:
        return await _handle_confirmation_answer(
            record, state, db, provider, current_idx, confirmation_entry, message
        )

    # --- #187: a reconciler-emitted interview-turn confirmation (skill dedupe,
    # entity near-dupe) surfaced on a PREVIOUS turn is resolved deterministically
    # here — NEVER re-run through the reconciler, whose stateless guard would
    # re-emit the identical confirmation and loop forever. Mirrors the
    # deterministic import-time confirmation path above. ---
    pending_conf = state.get("pending_interview_confirmation")
    if pending_conf is not None:
        return await _handle_interview_confirmation_answer(
            record, state, db, provider, current_idx, pending_conf, message, lang
        )

    skipped_set = set(state.get("skipped_gaps", []))
    addressed_set = set(state.get("addressed_gaps", []))
    clusters_by_id = state.get("gap_clusters_by_id") or {}
    current_cluster = clusters_by_id.get(current_gap, {"label": current_gap})
    cluster_label = current_cluster.get("label", current_gap)

    # --- Reconcile this answer into the profile (US182a / ADR-046) ---
    profile_record = await _load_profile(state["profile_id"], db)
    turn = await reconcile_interview_turn(
        profile_dict=profile_record.profile_json,
        gap=cluster_label,
        question=current_question,
        answer=message,
        provider=provider,
        session_id=str(record.id),
        lang=lang,
    )
    profile_record.profile_json = turn.profile_dict
    profile_record.updated_at = datetime.now(timezone.utc)
    conflict_summaries = turn.conflict_summaries
    # The reconciled profile feeds the next/follow-up question generator below.
    updated_profile = turn.profile_dict

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

    # #187 — consume the one-shot resolving flag BEFORE the re-ask check below.
    # The primary resolution path is the deterministic handler above; this flag is
    # the ordering backstop that guarantees a re-emitted identical confirmation can
    # never re-loop (the flag was previously popped AFTER the re-ask, so the
    # advance logic was unreachable whenever the reconciler re-emitted a
    # confirmation — the loop).
    resolving_confirmation = state.pop("resolving_confirmation", False)

    # --- US185: an unresolved ambiguity becomes a targeted confirmation question.
    # The reconciler never guesses entity identity (synonym role, project-vs-
    # position, DE<->EN employer); it asks. Surface that before advancing —
    # unless this turn is itself resolving a prior confirmation (#187). ---
    if turn.pending_confirmations and not resolving_confirmation:
        return await _ask_confirmation(record, state, db, turn, current_gap, current_idx)

    # --- Advance decision ---
    # Deterministic gap-progress (US182a): a profile mutation means the answer
    # addressed the gap. "declined" is already handled upstream by
    # is_termination_signal, so an answer that changes nothing -> follow up once.
    addressed = turn.addressed
    questions_for_gap = state.get("questions_per_gap", {}).get(current_gap, 1)

    # --- #188: a turn that ADDRESSED the current gap deterministically upgrades
    # the matching keyword_ledger entry on the persisted GapAnalysis row IN PLACE,
    # so a confirmed strength stops reading as an honest gap in the CV and cover
    # letter (both read that one row). `addressed` is exactly `bool(applied.changes)`
    # (interview_bridge), so this fires only when the reconciler actually touched a
    # field — never on a no-op turn. A no-op for guided/Mode-B (no ledger) and for
    # clusters whose concepts don't normalize-match any ledger entry. Runs before
    # the advance/complete branch so the same turn's single commit persists it. ---
    if addressed:
        await _upgrade_ledger_for_addressed_gap(state, current_gap, message, db)

    if addressed or resolving_confirmation or questions_for_gap >= INTERVIEW_MAX_QUESTIONS_PER_GAP:
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
            lang=lang,
        )
        next_question = next_q_data["question"]
        next_choices = next_q_data["choices"]
        state["current_question"] = next_question
        state["current_choices"] = next_choices
        state["messages"].append({"role": "assistant", "content": next_question})
        record.state = state
        record.updated_at = datetime.now(timezone.utc)
        # ONE commit per turn (#179): the reconciler's profile write (~line 1164)
        # and this transcript write share the turn's transaction. Never commit
        # between them — an early commit would persist the profile while a failed
        # question call rolls the transcript back (a real partial commit).
        await db.commit()

        return SessionMessageResponse(
            complete=False,
            question=next_question,
            gaps_remaining=gaps_remaining,
            pending_conflicts=conflict_summaries if conflict_summaries else None,
            choices=next_choices,
        )

    else:
        # Follow-up: stay on current gap
        qpg = dict(state.get("questions_per_gap", {}))
        qpg[current_gap] = questions_for_gap + 1
        state["questions_per_gap"] = qpg

        follow_up_hint = f"ask for a more specific or concrete example related to {current_gap}"
        gap_category = (state.get("gap_categories") or {}).get(current_gap)

        follow_up_data = await question_generator_with_profile(
            state,
            updated_profile,
            provider,
            gap_category=gap_category,
            follow_up_hint=follow_up_hint,
            lang=lang,
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
            pending_conflicts=conflict_summaries if conflict_summaries else None,
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

    # Treat expired sessions (past expires_at) as "expired" status. Coerce a
    # naive timestamp (some backends/drivers drop tzinfo) to UTC before comparing.
    expires_at = record.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        expires_at is not None
        and datetime.now(timezone.utc) > expires_at
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

    # Issue #68: completing the interview must move the flow off the 'interview'
    # step, else resuming from the dashboard re-opens it with a fresh session.
    # Lazy import to avoid a session<->flow import cycle (mirrors the lazy-import
    # pattern in flow.orchestrator.advance_flow). Best-effort: the interview is
    # already committed complete above; advancing the flow is a recoverable
    # convenience (the Generate-CV button re-advances idempotently), so a failure
    # here must not break the completion response.
    from applire.services.flow.orchestrator import advance_flow_on_interview_complete
    try:
        await advance_flow_on_interview_complete(record.id, db)
    except Exception:
        logger.warning(
            "Flow advance after interview completion failed for session %s; "
            "flow left on 'interview' step (recoverable via Generate CV)",
            record.id,
            exc_info=True,
        )

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
    job_id: uuid.UUID | None,
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
        "job_id": str(job_id) if job_id else None,
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
    # Newest-first + first(): pre-migration databases may still hold
    # duplicate active sessions; never raise MultipleResultsFound here.
    result = await db.execute(
        select(InterviewSession)
        .where(
            InterviewSession.job_analysis_id == job_id,
            InterviewSession.status == "active",
            InterviewSession.deleted_at.is_(None),
        )
        .order_by(InterviewSession.created_at.desc())
    )
    return result.scalars().first()


async def _load_profile(profile_id: str, db: AsyncSession) -> MasterProfile:
    result = await db.execute(
        select(MasterProfile).where(MasterProfile.id == uuid.UUID(profile_id))
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise LookupError(f"Profile {profile_id} not found")
    return record


async def _load_job_context(job_id: str | None, db: AsyncSession) -> dict:
    if not job_id:
        return {}
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
