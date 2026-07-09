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
from urllib.parse import parse_qsl, urlencode, urlsplit

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
    DuplicateOfHint,
    PatchApplicationRequest,
    StaleCVGained,
    StaleCVInfo,
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
    await _enrich_stale_cv(items, db)
    return ApplicationListResponse(items=items, total=len(items))


async def get_application(application_id: uuid.UUID, db: AsyncSession) -> ApplicationResponse:
    app = await _get_or_404(application_id, db)
    data = ApplicationResponse.model_validate(app)
    await _enrich_submitted_cv_meta([data], db)
    await _enrich_stale_cv([data], db)
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

    # Stale-CV nudge dismissal (E039/US221): stamp-only — the indicator re-arms
    # by itself when a NEWER enrichment lands, so there is no un-dismiss.
    if request.dismiss_stale_cv:
        app.stale_cv_dismissed_at = datetime.now(timezone.utc)

    _touch(app)
    await db.commit()
    await db.refresh(app)
    data = ApplicationResponse.model_validate(app)
    await _enrich_submitted_cv_meta([data], db)
    await _enrich_stale_cv([data], db)
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


async def find_duplicate_application(
    user_id: uuid.UUID,
    *,
    job_analysis_id: uuid.UUID,
    source_url: str | None,
    raw_text: str,
    db: AsyncSession,
) -> DuplicateOfHint | None:
    """Check a freshly analyzed JD against the USER'S pipeline (E039/US220).

    Journey Branch F: senior roles get reposted across boards — recognise the
    job Emma already has instead of letting a phantom application appear.
    Conservative first cut (journey OQ #10 owns any fuzzy threshold later):

      1. "job"        — the analyzed JD resolved to a job_analysis row the user
                        already has an application for (analyze_jd's global
                        URL/text-hash dedup collapses exact reposts upstream)
      2. "source_url" — normalized URL equality against the sibling job's URL
                        or the application's dossier source_url (text-tab capture)
      3. "text"       — whitespace/case-normalized JD-text equality

    Per-user boundary (epic 4.1 🔒): the query starts from `applications`
    (user-scoped) and reaches job_analyses only through the user's own rows —
    the shared/global job_analyses table is never scanned across users.
    """
    result = await db.execute(
        select(Application, JobAnalysis)
        .join(JobAnalysis, Application.job_analysis_id == JobAnalysis.id)
        .where(
            Application.user_id == user_id,
            Application.deleted_at.is_(None),
        )
        .order_by(Application.updated_at.desc())
    )
    rows = result.all()
    if not rows:
        return None

    norm_url = _normalize_source_url(source_url)
    norm_text = _normalize_jd_text(raw_text)

    for app, sibling_job in rows:
        if app.job_analysis_id == job_analysis_id:
            matched_on = "job"
        elif norm_url is not None and norm_url in (
            _normalize_source_url(sibling_job.source_url),
            _normalize_source_url(app.source_url),
        ):
            matched_on = "source_url"
        elif norm_text and norm_text == _normalize_jd_text(sibling_job.raw_text):
            matched_on = "text"
        else:
            continue
        return DuplicateOfHint(
            application_id=app.id,
            job_analysis_id=app.job_analysis_id,
            company_name=app.company_name,
            role_title=app.role_title,
            analyzed_at=app.created_at,
            matched_on=matched_on,
        )
    return None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

# Query params that identify a click, not a job — stripped before URL comparison.
_TRACKING_PARAMS = {"gclid", "fbclid", "ref", "source", "src", "cid"}


def _normalize_source_url(url: str | None) -> str | None:
    """Conservative URL identity: scheme, www., trailing slash, fragment and
    tracking params are noise; the rest of the query stays (board job ids often
    live there)."""
    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    if not host:
        return url.strip().lower().rstrip("/")
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
        ]
    )
    base = f"{host}{parts.path.rstrip('/')}"
    return f"{base}?{query}" if query else base


def _normalize_jd_text(text: str | None) -> str:
    """Near-exact text identity: case and whitespace runs are repost noise."""
    if not text:
        return ""
    return " ".join(text.lower().split())


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


# No re-tailor nudge once the pipeline has ended — there is nothing left to send.
_STALE_CV_TERMINAL_STATUSES = {UserStatus.rejected.value, UserStatus.hired.value}


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes and legacy trail entries may lack an
    offset — treat both as UTC so comparisons never mix aware and naive."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _parse_trail_timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        try:
            return _as_utc(datetime.fromisoformat(value))
        except ValueError:
            return None
    return None


async def _enrich_stale_cv(items: list[ApplicationResponse], db: AsyncSession) -> None:
    """Fill the stale_cv read model (E039/US221, journey Branch H).

    An application is stale when its newest READY generated CV predates the
    newest Master-Profile enrichment record — the profile grew after tailoring.
    The `gained` delta aggregates the enrichment changes newer than that CV so
    the nudge can explain WHAT grew. Dismissal (stale_cv_dismissed_at) mutes
    the hint until an even newer enrichment lands. Batched for the dashboard:
    one profile read + one CV query for the whole page.
    """
    candidates = [
        i for i in items if i.user_status not in _STALE_CV_TERMINAL_STATUSES
    ]
    if not candidates:
        return

    profile_result = await db.execute(
        select(MasterProfile.profile_json)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile_json = profile_result.scalar_one_or_none()
    if not profile_json:
        return
    trail = (profile_json.get("metadata") or {}).get("enrichment_history") or []
    records: list[tuple[datetime, list[dict]]] = []
    for rec in trail:
        ts = _parse_trail_timestamp(rec.get("timestamp"))
        if ts is not None:
            records.append((ts, rec.get("changes") or []))
    if not records:
        return
    enriched_at = max(ts for ts, _ in records)

    job_ids = {i.job_analysis_id for i in candidates}
    cv_result = await db.execute(
        select(
            GeneratedCV.id,
            GeneratedCV.job_analysis_id,
            GeneratedCV.created_at,
            GeneratedCV.template,
        )
        .where(
            GeneratedCV.job_analysis_id.in_(job_ids),
            GeneratedCV.status == "ready",
            GeneratedCV.deleted_at.is_(None),
        )
        .order_by(GeneratedCV.created_at.desc())
    )
    latest_by_job: dict[uuid.UUID, tuple] = {}
    for row in cv_result:
        latest_by_job.setdefault(row.job_analysis_id, row)  # first = newest

    for item in candidates:
        latest = latest_by_job.get(item.job_analysis_id)
        if latest is None:
            continue
        cv_created_at = _as_utc(latest.created_at)
        if enriched_at <= cv_created_at:
            continue
        dismissed_at = _as_utc(item.stale_cv_dismissed_at)
        if dismissed_at is not None and enriched_at <= dismissed_at:
            continue

        section_counts: dict[str, int] = {}
        for ts, changes in records:
            if ts <= cv_created_at:
                continue
            for change in changes:
                section = (change or {}).get("section")
                if section:
                    section_counts[section] = section_counts.get(section, 0) + 1
        item.stale_cv = StaleCVInfo(
            latest_cv_id=latest.id,
            latest_cv_created_at=cv_created_at,
            latest_cv_template=latest.template,
            profile_enriched_at=enriched_at,
            gained=[
                StaleCVGained(section=s, count=c)
                for s, c in sorted(section_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
        )


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
