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

"""Flow Orchestrator — Iteration 15

Manages the end-to-end user journey from JD intake to CV download.

State lives in flow_sessions (server-side, per ADR 004 Stateful Backend principle).
The step graph is a validated linear DAG — no illegal jumps.

Write path (advance_flow): caller passes artifact_id explicitly; FK written atomically
with the step transition to prevent race conditions with stale sibling flows.

Read path (get_flow_state): eager-loads child summaries via the FKs already set on
the flow record — deterministic, no discovery queries needed.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import MODE_B_COMPLETENESS_THRESHOLD
from applire.models.cover_letter import GeneratedCoverLetter
from applire.models.cv import GeneratedCV
from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.models.session import InterviewSession
# Imported at call-site to avoid circular imports (application service imports
# _resolve_user_type / _compute_actions from this module).
# from applire.services.application import sync_workflow_status
from applire.schemas.flow import (
    AdvanceFlowRequest,
    CoverLetterSummary,
    CreateFlowRequest,
    CreateFlowResponse,
    CVSummary,
    FlowStateResponse,
    GapAnalysisSummary,
    InterviewSummary,
    JobAnalysisSummary,
)
from applire.schemas.profile import MasterProfileData

# ---------------------------------------------------------------------------
# Step graph
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[str, list[str]] = {
    # "interview" target: no-CV guided onboarding (US156 / FMEA JF-M-2.6, ADR-016
    # amended) — a user with a job but no CV skips cv_import + gap_analysis and the
    # guided interview builds the Master Profile from scratch.
    "jd_analysis":   ["cv_import", "gap_analysis", "interview"],
    "cv_import":     ["gap_analysis"],
    "gap_analysis":  ["interview", "cv_generation"],   # returning users may skip interview
    "interview":     ["cv_generation"],
    "cv_generation": ["complete"],
    "complete":      [],
}

# Steps that require an artifact_id when advanced into — field name on FlowSession
_ARTIFACT_FIELD: dict[str, str] = {
    "gap_analysis":   "gap_analysis_id",
    "interview":      "interview_session_id",
    # generated_cv_id is recorded when the CV page advances to complete
    "complete":       "generated_cv_id",
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    def __init__(self, current: str, target: str, allowed: list[str]) -> None:
        self.current = current
        self.target = target
        self.allowed = allowed
        super().__init__(
            f"Cannot transition from '{current}' to '{target}'. Allowed: {allowed}"
        )


class ArtifactRequiredError(Exception):
    def __init__(self, step: str, field: str) -> None:
        self.step = step
        self.field = field
        super().__init__(
            f"Advancing to '{step}' requires artifact_id ({field}) but none was provided."
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_flow(
    request: CreateFlowRequest,
    user_id: uuid.UUID,
    db: AsyncSession,
    base_url: str = "http://localhost:8001",
) -> CreateFlowResponse:
    """Create or resume a flow session.

    job_id is optional — omit it for a CV-only upload with no linked job.
    Idempotent when job_id is provided: returns the existing flow for (user_id, job_id).
    """
    job = None
    if request.job_id is not None:
        job = await db.get(JobAnalysis, request.job_id)
        if job is None:
            raise LookupError(f"Job {request.job_id} not found")

        existing = await _get_existing_flow(user_id, request.job_id, db)
        if existing is not None:
            return CreateFlowResponse(
                flow_id=existing.id,
                user_type=existing.user_type,
                current_step=existing.current_step,
                available_actions=existing.available_actions,
                job_summary=JobAnalysisSummary(job_id=job.id, role_title=job.role_title),
            )

    user_type = await _resolve_user_type(db)
    available_actions = _compute_actions(
        "jd_analysis", user_type, await _has_open_gate(db)
    )

    flow = FlowSession(
        user_id=user_id,
        job_id=request.job_id,
        current_step="jd_analysis",
        user_type=user_type,
        available_actions=available_actions,
    )
    db.add(flow)
    try:
        await db.commit()
    except IntegrityError:
        # Race: another request created the flow for the same (user_id, job_id)
        await db.rollback()
        if request.job_id is None:
            raise
        existing = await _get_existing_flow(user_id, request.job_id, db)
        if existing is None:
            raise
        flow = existing

    await db.refresh(flow)
    job_summary = (
        JobAnalysisSummary(job_id=job.id, role_title=job.role_title) if job else None
    )
    return CreateFlowResponse(
        flow_id=flow.id,
        user_type=flow.user_type,
        current_step=flow.current_step,
        available_actions=flow.available_actions,
        job_summary=job_summary,
    )


async def get_flow_state(
    flow_id: uuid.UUID,
    db: AsyncSession,
    base_url: str = "http://localhost:8001",
) -> FlowStateResponse:
    """Return the full flow state with eagerly-loaded child resource summaries."""
    flow = await db.get(FlowSession, flow_id)
    if flow is None:
        raise LookupError(f"Flow {flow_id} not found")
    return await _build_state_response(flow, db, base_url)


async def advance_flow(
    flow_id: uuid.UUID,
    request: AdvanceFlowRequest,
    db: AsyncSession,
    base_url: str = "http://localhost:8001",
) -> FlowStateResponse:
    """Validate and apply a step transition, writing artifact FK atomically."""
    flow = await db.get(FlowSession, flow_id)
    if flow is None:
        raise LookupError(f"Flow {flow_id} not found")

    target = request.step

    # Idempotent re-advance: already on the target step. Treat as a no-op rather
    # than raising InvalidTransitionError (which the router maps to HTTP 409).
    # This absorbs benign double-submits (e.g. photo-skip firing twice) and lets
    # a re-generated artifact (e.g. a new CV) refresh the recorded FK.
    if target == flow.current_step:
        if target in _ARTIFACT_FIELD and request.artifact_id is not None:
            setattr(flow, _ARTIFACT_FIELD[target], request.artifact_id)
            flow.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(flow)
        return await _build_state_response(flow, db, base_url)

    allowed = VALID_TRANSITIONS.get(flow.current_step, [])
    if target not in allowed:
        raise InvalidTransitionError(
            current=flow.current_step, target=target, allowed=allowed
        )

    if target in _ARTIFACT_FIELD:
        field = _ARTIFACT_FIELD[target]
        if request.artifact_id is None:
            raise ArtifactRequiredError(step=target, field=field)
        setattr(flow, field, request.artifact_id)

    flow.current_step = target
    flow.available_actions = _compute_actions(
        target, flow.user_type, await _has_open_gate(db)
    )
    flow.updated_at = datetime.now(timezone.utc)
    if target == "complete":
        flow.completed_at = datetime.now(timezone.utc)

    # Write-time status sync: keep Application.workflow_status consistent.
    #
    # WHY lazy import: applire.services.application imports _resolve_user_type and
    # _compute_actions from this module at the top level. If we also import
    # sync_workflow_status at the top level here, Python sees a circular dependency
    # (flow.orchestrator ↔ services.application) and raises ImportError.
    # Importing inside the function body breaks the cycle because by the time this
    # line executes, both modules are fully loaded.
    #
    # DO NOT move this import to the top of the file — it will reintroduce the cycle.
    # Long-term fix: extract sync into a lightweight callback/event that both sides
    # can reference without importing each other. Deferred — current scale doesn't
    # warrant the abstraction.
    if flow.application_id is not None:
        from applire.services.application import sync_workflow_status
        await sync_workflow_status(flow.application_id, target, db)

    await db.commit()
    await db.refresh(flow)
    return await _build_state_response(flow, db, base_url)


async def repoint_flow_gap_analysis(
    job_id: uuid.UUID | None,
    gap_analysis_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """Repoint the owning flow's gap_analysis_id FK to the latest gap analysis.

    The CV/flow read path (_build_state_response) reports match_score + gaps via
    flow.gap_analysis_id, but that FK is only written when advance_flow transitions
    INTO the gap_analysis step. Every later recompute (/gaps/refresh, interview
    completion, gap-click) creates a NEW gap_analyses row through analyze_gaps()
    but, without this, leaves the flow pinned to the stale pre-interview analysis
    (UAT 2026-06-26: CV page showed 40% and re-listed answered gaps after the
    interview reached 90%).

    Scope: a flow is uniquely (user_id, job_id) — for a given job_id there is at
    most one non-deleted owning flow in single-user Community mode, so resolving by
    job_id cannot move a different job's FK. Null-safe: no job_id or no owning flow
    is a no-op. Only the FK is touched — current_step and the step machine are left
    untouched (this is NOT a transition).
    """
    if job_id is None:
        return
    result = await db.execute(
        select(FlowSession).where(
            FlowSession.job_id == job_id,
            FlowSession.deleted_at.is_(None),
        )
    )
    flow = result.scalar_one_or_none()
    if flow is None:
        return
    if flow.gap_analysis_id == gap_analysis_id:
        return
    flow.gap_analysis_id = gap_analysis_id
    flow.updated_at = datetime.now(timezone.utc)
    await db.commit()


async def advance_flow_on_interview_complete(
    interview_session_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """Advance the owning flow off the 'interview' step when its interview completes.

    Called when an InterviewSession reaches status='complete' (user ended, gaps
    resolved). Without this the flow's current_step stays at 'interview' and
    resuming from the dashboard re-opens the interview with a fresh session
    (issue #68). No-op when no flow owns the session (e.g. Mode C profile-enrich)
    or the flow is not on the interview step. advance_flow is idempotent, so a
    later 'Generate CV' re-advance to cv_generation is harmless.
    """
    result = await db.execute(
        select(FlowSession).where(
            FlowSession.interview_session_id == interview_session_id
        )
    )
    flow = result.scalar_one_or_none()
    if flow is None or flow.current_step != "interview":
        return
    await advance_flow(
        flow.id,
        AdvanceFlowRequest(step="cv_generation"),
        db,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _get_existing_flow(
    user_id: uuid.UUID, job_id: uuid.UUID, db: AsyncSession
) -> FlowSession | None:
    result = await db.execute(
        select(FlowSession).where(
            FlowSession.user_id == user_id,
            FlowSession.job_id == job_id,
        )
    )
    return result.scalar_one_or_none()


async def _resolve_user_type(db: AsyncSession) -> str:
    """Return 'returning' if profile completeness >= MODE_B_COMPLETENESS_THRESHOLD."""
    result = await db.execute(select(MasterProfile).limit(1))
    profile_record = result.scalar_one_or_none()
    if profile_record is None:
        return "new"
    try:
        profile_data = MasterProfileData.model_validate(profile_record.profile_json)
        score = profile_data.calculate_completeness()
    except Exception:
        return "new"
    return "returning" if score >= MODE_B_COMPLETENESS_THRESHOLD else "new"


async def _has_open_gate(db: AsyncSession) -> bool:
    """True if a deferred Tier-1 integrity gate is parked (US167 / US163).

    While one is open a returning user must not be allowed to skip the interview
    — that interview is where the gate is confirmed (ADR-041 amended).
    """
    from applire.services.profile import list_open_gates  # lazy: avoid import cycle

    return bool(await list_open_gates(db))


def _compute_actions(
    step: str, user_type: str, has_open_gate: bool = False
) -> dict[str, str]:
    """Return available_actions dict for a given (step, user_type).

    ``has_open_gate`` withdraws the returning-user skip-to-generation shortcut so
    a parked integrity gate is always confirmed in the interview first (US163).
    """
    if step == "jd_analysis":
        return {"next": "gap_analysis"} if user_type == "returning" else {"next": "cv_import"}
    if step == "cv_import":
        return {"next": "gap_analysis"}
    if step == "gap_analysis":
        if user_type == "returning" and not has_open_gate:
            return {"next": "cv_generation"}
        if user_type == "returning":
            return {"next": "interview"}
        return {"next": "interview", "skip": "cv_generation"}
    if step == "interview":
        return {"next": "cv_generation"}
    if step == "cv_generation":
        return {"next": "complete"}
    return {}


async def _build_state_response(
    flow: FlowSession,
    db: AsyncSession,
    base_url: str,
) -> FlowStateResponse:
    # Job summary
    job_summary: JobAnalysisSummary | None = None
    job = await db.get(JobAnalysis, flow.job_id)
    if job:
        job_summary = JobAnalysisSummary(job_id=job.id, role_title=job.role_title)

    # Profile completeness
    profile_completeness: float | None = None
    result = await db.execute(select(MasterProfile).limit(1))
    profile_record = result.scalar_one_or_none()
    if profile_record:
        try:
            profile_data = MasterProfileData.model_validate(profile_record.profile_json)
            profile_completeness = profile_data.calculate_completeness()
        except Exception:
            pass

    # Gap summary — via FK set by advance_flow
    gap_summary: GapAnalysisSummary | None = None
    if flow.gap_analysis_id:
        gap = await db.get(GapAnalysis, flow.gap_analysis_id)
        if gap:
            gap_summary = GapAnalysisSummary(
                gap_analysis_id=gap.id,
                match_score=gap.match_score,
                critical_gaps_count=len(gap.critical_gaps),
                category_c_count=len(gap.category_c),
            )

    # Interview summary — via FK set by advance_flow
    interview_summary: InterviewSummary | None = None
    if flow.interview_session_id:
        session = await db.get(InterviewSession, flow.interview_session_id)
        if session:
            interview_summary = InterviewSummary(
                session_id=session.id,
                mode=session.mode,
                status=session.status,
                questions_asked=session.questions_asked,
                hard_ceiling=session.hard_ceiling,
            )

    # CV summary — via FK set by advance_flow
    cv_summary: CVSummary | None = None
    if flow.generated_cv_id:
        cv = await db.get(GeneratedCV, flow.generated_cv_id)
        if cv:
            cv_summary = CVSummary(
                cv_id=cv.id,
                pdf_url=f"{base_url}/api/cv/{cv.id}/pdf",
                expires_at=cv.expires_at,
            )

    # Cover letter summary — via FK set when cover letter is generated
    cover_letter_summary: CoverLetterSummary | None = None
    if flow.generated_cover_letter_id is not None:
        cl_result = await db.execute(
            select(GeneratedCoverLetter).where(
                GeneratedCoverLetter.id == flow.generated_cover_letter_id
            )
        )
        cl = cl_result.scalar_one_or_none()
        if cl is not None:
            cover_letter_summary = CoverLetterSummary(
                cover_letter_id=cl.id,
                status=cl.status,
                template=cl.template,
                expires_at=cl.expires_at,
            )

    return FlowStateResponse(
        flow_id=flow.id,
        job_id=flow.job_id,
        application_id=flow.application_id,
        user_type=flow.user_type,
        current_step=flow.current_step,
        available_actions=flow.available_actions,
        job_summary=job_summary,
        profile_completeness=profile_completeness,
        gap_summary=gap_summary,
        interview_summary=interview_summary,
        cv_summary=cv_summary,
        cover_letter_summary=cover_letter_summary,
        created_at=flow.created_at,
        updated_at=flow.updated_at,
    )
