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

# Steps that RECORD an artifact_id when advanced into — field name on FlowSession.
#
# #676 line 35 (edge UAT 2026-09-18): `cv_generation` used to be absent here, so
# `advance_flow(step="cv_generation", artifact_id=<cv_id>)` — the call the guide's
# own "steps that produce artifacts need the matching artifact_id" asks for, and
# the call the CV page makes (`frontend/.../flow/[flowId]/cv/page.tsx`) — was
# accepted and SILENTLY dropped: the flow answered `current_step: cv_generation`
# with `cv_summary: null` while `flow_sessions.generated_cv_id` stayed NULL until
# the agent happened to re-pass the same id at `complete`. The REST door's own
# docstring (`routers/flow.py`) has named cv_generation an artifact-producing step
# the whole time. The id is now recorded at the step that PRODUCES it; `complete`
# keeps accepting it unchanged (back-compat: every existing caller still works,
# and re-passing the same id at `complete` is the idempotent same-value write).
_ARTIFACT_FIELD: dict[str, str] = {
    "gap_analysis":   "gap_analysis_id",
    "interview":      "interview_session_id",
    "cv_generation":  "generated_cv_id",
    "complete":       "generated_cv_id",
}

# The subset of _ARTIFACT_FIELD whose id the caller MUST supply (422 / -32602
# without it). RECORDING and REQUIRING are two different facts and were one map
# until #676 line 35: `cv_generation` is entered in order to GENERATE the CV, so
# at transition time the artifact does not exist yet — the gaps page and the
# interview page both advance into it with no id, and the interview-completion
# hook (`advance_flow_on_interview_complete`) does too. Requiring it there would
# turn the fix into a breaking change for all three callers; the id is written
# whenever it IS supplied, which is exactly what the dropped call needed.
_ARTIFACT_REQUIRED: frozenset[str] = frozenset(
    {"gap_analysis", "interview", "complete"}
)

# Same steps — ORM model the artifact_id must resolve to. #676 line 1 (was #581):
# the FK used to be written straight from request.artifact_id with no lookup, so a
# wrong-referent id passed db.commit() through to a raw IntegrityError neither
# door caught (bare 500). _check_artifact_exists uses this map to turn that into a
# typed ArtifactNotFoundError before the write.
_ARTIFACT_MODEL: dict[str, type] = {
    "gap_analysis":   GapAnalysis,
    "interview":      InterviewSession,
    "cv_generation":  GeneratedCV,
    "complete":       GeneratedCV,
}


def _artifact_field_sentence() -> str:
    """The step → field mapping as one sentence, derived from the map itself.

    #676 line 35 / #673 line 46: the notice below and AGENT_GUIDE.md both have to
    state which id goes where, and a hand-written copy of this mapping is exactly
    the drift #603 found between GUIDE_VERSION and the guide's own revision line.
    Pinned by `test_notice_names_every_recording_step`.
    """
    pairs = ", ".join(f"{step} → {field}" for step, field in _ARTIFACT_FIELD.items())
    return (
        f"artifact_id is recorded at these steps only: {pairs}. "
        "The cover letter is not a flow step — generate_cover_letter links it "
        "to the flow itself."
    )


def unrecordable_artifact_notice(step: str) -> str:
    """The response notice for an artifact_id passed at a step that records none.

    #676 line 35 / ruling D-4: an id the flow cannot record is answered with an
    explicit notice, never a silent drop and never an error — an error would
    break agents that follow today's tool description, and the silent drop is the
    defect being fixed. The notice rides `FlowStateResponse.notices`, so the
    transition itself still succeeds.
    """
    return (
        f"artifact_id was not recorded: step '{step}' produces no artifact. "
        + _artifact_field_sentence()
    )


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


class ArtifactNotFoundError(Exception):
    """artifact_id was provided but no matching row exists for this step's model.

    Raised instead of letting the FK write reach db.commit(), which on a real
    (FK-enforcing) database raises a raw IntegrityError that neither door catches.
    """

    def __init__(self, step: str, artifact_id: uuid.UUID) -> None:
        self.step = step
        self.artifact_id = artifact_id
        super().__init__(
            f"Advancing to '{step}' references artifact_id {artifact_id}, "
            f"but no matching record exists."
        )


async def _check_artifact_exists(
    step: str, artifact_id: uuid.UUID, db: AsyncSession
) -> None:
    """Look the artifact_id up in its step's model before it is written to the FK.

    #676 line 1 (was #581): a plain PK lookup, not a query — cheap, and it runs
    on the same session/transaction as the write that follows, so there is no
    TOCTOU window between the check and the setattr.

    Adversarial pass, 2026-09-19 (#676 line 1 residual): a bare ``db.get()``
    finds a SOFT-DELETED row too — every one of these models carries
    ``deleted_at``, and every other fetch-by-id in this codebase
    (``services/gap.py``, ``services/application.py``, …) filters
    ``deleted_at.is_(None)``. This lookup did not, so a deleted CV/gap-
    analysis/interview-session id was recorded into the flow's FK exactly
    like a live one — same class of silent wrong-referent write #676 line 1
    already closed for an id from another table. A ``SELECT … WHERE id = :id
    AND deleted_at IS NULL`` on the same session keeps the no-TOCTOU property.
    """
    model = _ARTIFACT_MODEL[step]
    row = await db.scalar(
        select(model).where(model.id == artifact_id, model.deleted_at.is_(None))
    )
    if row is None:
        raise ArtifactNotFoundError(step=step, artifact_id=artifact_id)


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

    # #676 line 35 — an artifact_id the flow has nowhere to put is REPORTED, on
    # both branches below (the drop the edge UAT hit was on the idempotent one:
    # the CV page re-advances to the step it is already on).
    notices: list[str] = []
    if request.artifact_id is not None and target not in _ARTIFACT_FIELD:
        notices.append(unrecordable_artifact_notice(target))

    # Idempotent re-advance: already on the target step. Treat as a no-op rather
    # than raising InvalidTransitionError (which the router maps to HTTP 409).
    # This absorbs benign double-submits (e.g. photo-skip firing twice) and lets
    # a re-generated artifact (e.g. a new CV) refresh the recorded FK.
    if target == flow.current_step:
        if target in _ARTIFACT_FIELD and request.artifact_id is not None:
            await _check_artifact_exists(target, request.artifact_id, db)
            setattr(flow, _ARTIFACT_FIELD[target], request.artifact_id)
            flow.updated_at = datetime.now(timezone.utc)
            await db.commit()
            await db.refresh(flow)
        return await _build_state_response(flow, db, base_url, notices=notices)

    allowed = VALID_TRANSITIONS.get(flow.current_step, [])
    if target not in allowed:
        raise InvalidTransitionError(
            current=flow.current_step, target=target, allowed=allowed
        )

    if target in _ARTIFACT_FIELD:
        field = _ARTIFACT_FIELD[target]
        if request.artifact_id is None:
            # #676 line 35 — only the REQUIRED subset refuses. cv_generation is
            # entered to produce the CV, so "no id yet" is its normal case.
            if target in _ARTIFACT_REQUIRED:
                raise ArtifactRequiredError(step=target, field=field)
        else:
            await _check_artifact_exists(target, request.artifact_id, db)
            setattr(flow, field, request.artifact_id)

    flow.current_step = target
    flow.available_actions = _compute_actions(
        target,
        flow.user_type,
        await _has_open_gate(db),
        has_gaps=(
            await _gap_items_present(db, flow.gap_analysis_id)
            if target == "gap_analysis"
            else None
        ),
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
    return await _build_state_response(flow, db, base_url, notices=notices)


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
    step: str,
    user_type: str,
    has_open_gate: bool = False,
    has_gaps: bool | None = None,
) -> dict[str, str]:
    """Return available_actions dict for a given step.

    At ``gap_analysis`` the interview offer is GAP-driven, not user-type-driven
    (ADR-016 amended 2026-07-13, Spaghettieis UAT): gaps detected → the user
    gets the mitigation option regardless of new/returning; a clean sweep goes
    straight to CV generation. ``has_gaps=None`` (analysis not resolvable)
    keeps the offer — never silently remove the mitigation path.

    ``has_open_gate`` forces the interview so a parked Tier-1 integrity gate is
    always confirmed there first (US163, ADR-041) — even on a clean sweep.
    """
    if step == "jd_analysis":
        return {"next": "gap_analysis"} if user_type == "returning" else {"next": "cv_import"}
    if step == "cv_import":
        return {"next": "gap_analysis"}
    if step == "gap_analysis":
        if has_open_gate:
            return {"next": "interview"}
        if has_gaps is False:
            return {"next": "cv_generation"}
        return {"next": "interview", "skip": "cv_generation"}
    if step == "interview":
        return {"next": "cv_generation"}
    if step == "cv_generation":
        return {"next": "complete"}
    return {}


async def _gap_items_present(
    db: AsyncSession, gap_analysis_id: uuid.UUID | None
) -> bool | None:
    """Whether the analysis found anything to address (partials OR gaps).

    None when the artifact can't be resolved — the caller keeps the safe
    default (interview offered).
    """
    if gap_analysis_id is None:
        return None
    gap = await db.get(GapAnalysis, gap_analysis_id)
    if gap is None:
        return None
    return bool(gap.category_b or []) or bool(gap.category_c or [])


async def _build_state_response(
    flow: FlowSession,
    db: AsyncSession,
    base_url: str,
    notices: list[str] | None = None,
) -> FlowStateResponse:
    """Build the flow's state DTO.

    ``notices`` is advance-only (#676 line 35): a READ of the state never has
    anything to say about a call that was not made, so `get_flow_state` leaves
    the list empty rather than echoing a stale notice off the record.
    """
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
        notices=list(notices or []),
    )
