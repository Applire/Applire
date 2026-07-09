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

"""Application service — Iteration 17

Manages the Application entity lifecycle:
  create     — add job to pipeline, optional immediate workflow start
  list       — user's full pipeline, filterable by status
  get        — single application detail
  patch      — update user-managed fields (rejects workflow_status writes)
  delete     — soft-delete application + attached FlowSession
  start      — create FlowSession for a tracking application (deferred activation)
  sync_status — called by Flow Orchestrator after each advance_flow() to keep
                workflow_status consistent (write-time sync, not read-time)
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.application import (
    Application,
    STEP_TO_WORKFLOW_STATUS,
    UserStatus,
    WorkflowStatus,
    _APPLICATION_TTL_DAYS,
)
from applire.models.cover_letter import GeneratedCoverLetter
from applire.models.cv import GeneratedCV
from applire.models.flow import FlowSession
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.schemas.application import (
    ApplicationListResponse,
    ApplicationResponse,
    CreateApplicationRequest,
    PatchApplicationRequest,
)
from applire.schemas.application_mark_hired import MarkHiredResponse
from applire.schemas.profile import MasterProfileData

# Import flow helpers — these are pure functions with no import of application.py.
# The orchestrator imports sync_workflow_status lazily (inside advance_flow body)
# to avoid a circular import at module level.
from applire.services.flow.orchestrator import _compute_actions, _resolve_user_type


class ConflictError(Exception):
    """Raised when an operation violates a uniqueness or state constraint."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def create_application(
    user_id: uuid.UUID,
    request: CreateApplicationRequest,
    db: AsyncSession,
) -> ApplicationResponse:
    """Add a job to the user's pipeline (idempotent get-or-create).

    Denormalizes company_name / role_title from JobAnalysis if not supplied.
    If start_workflow=True, creates a FlowSession atomically in the same
    transaction (same code path as POST /api/applications/{id}/start).

    One Application per (user_id, job_analysis_id) is enforced at the DB level
    (uq_application_user_job, which deliberately ignores deleted_at). Rather than
    leak that constraint as a hard 409, a repeat submission for the same
    (user, job) reuses the existing Application — reactivating it if it was
    soft-deleted. This makes the natural retry after a failed build resume
    cleanly instead of dead-ending the user.
    """
    job = await db.get(JobAnalysis, request.job_analysis_id)
    if job is None:
        raise LookupError(f"JobAnalysis {request.job_analysis_id} not found")

    # Idempotent reuse: an Application for this (user, job) may already exist —
    # including a soft-deleted one, which still occupies the unique slot.
    existing_result = await db.execute(
        select(Application).where(
            Application.user_id == user_id,
            Application.job_analysis_id == request.job_analysis_id,
        )
    )
    app = existing_result.scalar_one_or_none()

    if app is not None:
        if app.deleted_at is not None:
            app.deleted_at = None  # reactivate a previously removed application
        _touch(app)
    else:
        app = Application(
            user_id=user_id,
            job_analysis_id=request.job_analysis_id,
            company_name=request.company_name or job.company_name,
            role_title=request.role_title or job.role_title,
            notes=request.notes,
            deadline=request.deadline,
            source_url=request.source_url or job.source_url,
        )
        db.add(app)
        await db.flush()  # get app.id before potential workflow creation

    # _start_workflow is itself idempotent: it reuses the existing FlowSession
    # for this (user, job) if one is already present.
    if request.start_workflow:
        await _start_workflow(app, user_id, db)

    await db.commit()
    await db.refresh(app)
    return ApplicationResponse.model_validate(app)


async def list_applications(
    user_id: uuid.UUID,
    db: AsyncSession,
    workflow_status: WorkflowStatus | None = None,
    user_status: UserStatus | None = None,
    q: str | None = None,
) -> ApplicationListResponse:
    stmt = (
        select(Application)
        .where(
            Application.user_id == user_id,
            Application.deleted_at.is_(None),
        )
        .order_by(Application.updated_at.desc())
    )
    if workflow_status is not None:
        stmt = stmt.where(Application.workflow_status == workflow_status.value)
    if user_status is not None:
        stmt = stmt.where(Application.user_status == user_status.value)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            (Application.role_title.ilike(like))
            | (Application.company_name.ilike(like))
            | (Application.notes.ilike(like))
        )

    result = await db.execute(stmt)
    apps = result.scalars().all()

    # Batch-load FlowSessions to avoid N+1 — Application has no ORM relationship,
    # only the FK column flow_session_id.
    flow_ids = [app.flow_session_id for app in apps if app.flow_session_id is not None]
    if flow_ids:
        flow_result = await db.execute(
            select(FlowSession).where(FlowSession.id.in_(flow_ids))
        )
        flow_map: dict[uuid.UUID, FlowSession] = {
            f.id: f for f in flow_result.scalars().all()
        }
    else:
        flow_map = {}

    items = []
    for app in apps:
        data = ApplicationResponse.model_validate(app)
        if app.flow_session_id is not None:
            flow = flow_map.get(app.flow_session_id)
            if flow is not None:
                data.flow_current_step = flow.current_step
        items.append(data)
    await _enrich_submitted_cv_meta(items, db)
    return ApplicationListResponse(items=items, total=len(items))


async def get_application(application_id: uuid.UUID, db: AsyncSession) -> ApplicationResponse:
    app = await _get_or_404(application_id, db)
    data = ApplicationResponse.model_validate(app)
    await _enrich_submitted_cv_meta([data], db)
    return data


async def patch_application(
    application_id: uuid.UUID,
    request: PatchApplicationRequest,
    db: AsyncSession,
) -> ApplicationResponse:
    app = await _get_or_404(application_id, db)

    provided = request.model_dump(exclude_unset=True)

    # Never clearable: an explicit null is ignored, a value is applied.
    if request.user_status is not None:
        app.user_status = request.user_status.value
    if request.company_name is not None:
        app.company_name = request.company_name
    if request.role_title is not None:
        app.role_title = request.role_title

    # Clearable dossier fields: an explicit null in the body clears the value
    # (the UI's "remove deadline/note/source" action — E039/US217).
    for field in ("notes", "applied_at", "deadline", "source_url"):
        if field in provided:
            setattr(app, field, provided[field])

    # Submitted pins (E039/US219): value = pin, explicit null = unpin. A pin must
    # reference a live artifact generated for THIS application's job — otherwise
    # the "sent version" recall (Branch G) would show a document from another
    # application, and the retention exemption (ADR-005) would protect the wrong row.
    if "submitted_cv_id" in provided:
        if provided["submitted_cv_id"] is not None:
            await _validate_pin(
                GeneratedCV, provided["submitted_cv_id"], "submitted_cv_id", app, db
            )
        app.submitted_cv_id = provided["submitted_cv_id"]
    if "submitted_cover_letter_id" in provided:
        if provided["submitted_cover_letter_id"] is not None:
            await _validate_pin(
                GeneratedCoverLetter,
                provided["submitted_cover_letter_id"],
                "submitted_cover_letter_id",
                app,
                db,
            )
        app.submitted_cover_letter_id = provided["submitted_cover_letter_id"]

    _touch(app)
    await db.commit()
    await db.refresh(app)
    data = ApplicationResponse.model_validate(app)
    await _enrich_submitted_cv_meta([data], db)
    return data


async def delete_application(application_id: uuid.UUID, db: AsyncSession) -> None:
    """Soft-delete the application and its attached FlowSession (if any)."""
    app = await _get_or_404(application_id, db)
    now = datetime.now(timezone.utc)
    app.deleted_at = now

    if app.flow_session_id is not None:
        flow = await db.get(FlowSession, app.flow_session_id)
        if flow is not None:
            flow.deleted_at = now

    await db.commit()


async def start_application_workflow(
    application_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> ApplicationResponse:
    """Create a FlowSession for a tracking application (deferred activation path).

    Returns HTTP 409 semantics via ConflictError if workflow already started.
    """
    app = await _get_or_404(application_id, db)

    if app.flow_session_id is not None:
        # User-facing message — no internal identifiers (router surfaces str(exc)
        # verbatim as the API `detail`).
        raise ConflictError(
            "This application's workflow has already been started."
        )

    await _start_workflow(app, user_id, db)
    _touch(app)
    await db.commit()
    await db.refresh(app)
    return ApplicationResponse.model_validate(app)


async def sync_workflow_status(
    application_id: uuid.UUID,
    new_step: str,
    db: AsyncSession,
) -> None:
    """Called by advance_flow() after a successful step transition.

    Maps the FlowSession step to a WorkflowStatus and updates the Application.
    The caller (orchestrator) is responsible for the db.commit().
    """
    new_ws = STEP_TO_WORKFLOW_STATUS.get(new_step, WorkflowStatus.analyzing)
    now = datetime.now(timezone.utc)
    await db.execute(
        update(Application)
        .where(Application.id == application_id, Application.deleted_at.is_(None))
        .values(
            workflow_status=new_ws.value,
            updated_at=now,
            expires_at=now + timedelta(days=_APPLICATION_TTL_DAYS),
        )
    )


async def mark_application_hired(
    application_id: uuid.UUID,
    db: AsyncSession,
) -> MarkHiredResponse:
    """Idempotent: marks the application as hired and returns the redirect URL."""
    result = await db.execute(
        select(Application).where(
            Application.id == application_id,
            Application.deleted_at.is_(None),
        )
    )
    app_row = result.scalar_one_or_none()
    if app_row is None:
        raise LookupError(f"Application {application_id} not found")

    app_row.user_status = UserStatus.hired.value
    if app_row.applied_at is None:
        app_row.applied_at = datetime.now(timezone.utc)
    await db.commit()

    return MarkHiredResponse(
        application_id=str(app_row.id),
        user_status=app_row.user_status,
        redirect_url=(
            f"/profile/upload?action=add-role"
            f"&source=application&application_id={app_row.id}"
        ),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _validate_pin(
    model: type,
    artifact_id: uuid.UUID,
    field: str,
    app: Application,
    db: AsyncSession,
) -> None:
    """A submitted pin must reference an existing, non-deleted artifact generated
    for the application's job (E039/US219)."""
    artifact = await db.get(model, artifact_id)
    if artifact is None or artifact.deleted_at is not None:
        raise ValueError(f"{field} does not reference an existing generated document.")
    if artifact.job_analysis_id != app.job_analysis_id:
        raise ValueError(
            f"{field} must reference a document generated for this application's job."
        )


async def _enrich_submitted_cv_meta(
    items: list[ApplicationResponse], db: AsyncSession
) -> None:
    """Fill submitted_cv_created_at for pinned items (E039/US219 read model).

    One batched query for the whole page — the dashboard list must not go N+1.
    The timestamp is the sent badge's "version" identity; ordinals would
    renumber whenever retention purges an older unpinned CV.
    """
    pinned_ids = {i.submitted_cv_id for i in items if i.submitted_cv_id is not None}
    if not pinned_ids:
        return
    result = await db.execute(
        select(GeneratedCV.id, GeneratedCV.created_at).where(
            GeneratedCV.id.in_(pinned_ids)
        )
    )
    created_map = {row.id: row.created_at for row in result}
    for item in items:
        if item.submitted_cv_id is not None:
            item.submitted_cv_created_at = created_map.get(item.submitted_cv_id)


async def _get_or_404(application_id: uuid.UUID, db: AsyncSession) -> Application:
    result = await db.execute(
        select(Application).where(
            Application.id == application_id,
            Application.deleted_at.is_(None),
        )
    )
    app = result.scalar_one_or_none()
    if app is None:
        raise LookupError(f"Application {application_id} not found")
    return app


async def _start_workflow(
    app: Application,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    """Shared logic for create(start_workflow=True) and start_application_workflow().

    Creates a FlowSession (or reactivates a soft-deleted one), links it to the
    Application, and sets workflow_status to 'analyzing'.
    The caller is responsible for db.commit().

    uq_flow_session_user_job enforces one session per (user_id, job_id).  If a
    session already exists (e.g. from a previously deleted application for the
    same job), we reuse it rather than attempting a duplicate INSERT.
    """
    # Check for any existing session for this (user_id, job_id) — including
    # soft-deleted ones, which still occupy the unique constraint slot.
    existing_result = await db.execute(
        select(FlowSession).where(
            FlowSession.user_id == user_id,
            FlowSession.job_id == app.job_analysis_id,
        )
    )
    existing = existing_result.scalar_one_or_none()

    if existing is not None:
        # Reactivate and relink the existing session.
        existing.deleted_at = None
        existing.application_id = app.id
        existing.updated_at = datetime.now(timezone.utc)
        flow = existing
    else:
        user_type = await _resolve_user_type(db)
        available_actions = _compute_actions("jd_analysis", user_type)
        flow = FlowSession(
            user_id=user_id,
            job_id=app.job_analysis_id,
            current_step="jd_analysis",
            user_type=user_type,
            available_actions=available_actions,
            application_id=app.id,
        )
        db.add(flow)
        await db.flush()  # populate flow.id

    app.flow_session_id = flow.id
    app.workflow_status = WorkflowStatus.analyzing.value


def _touch(app: Application) -> None:
    """Reset the GDPR inactivity timer on any update."""
    now = datetime.now(timezone.utc)
    app.updated_at = now
    app.expires_at = now + timedelta(days=_APPLICATION_TTL_DAYS)
