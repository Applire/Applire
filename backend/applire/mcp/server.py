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
Applire MCP Server — Iteration 8 — Agent Flow & Ingestion (ADR-010 amended 2026-05-31)

Exposes the full JD → profile → gap-fill → CV tailoring workflow as MCP tools
and resources so AI agents can drive the process autonomously.

Transport: stdio (Community Edition).  SSE is reserved for Cloud Edition.

Tools:
  analyze_jd        — analyse a job description text or URL
  get_profile       — retrieve the current MasterProfile
  update_profile    — patch a section of the MasterProfile
  analyze_gaps      — compare profile against a job
  run_interview     — start a gap-fill interview session
  send_message      — advance an active interview session
  generate_cv       — generate a tailored CV
  get_cv_status     — poll CV generation status and retrieve download URLs
  get_cv_ats_report — get the persisted ATS audit report for a generated CV
  generate_cover_letter       — generate a cover letter for a job (#170)
  get_cover_letter_status     — poll cover letter generation status (#170)
  get_cover_letter_ats_report — ATS audit report for a generated cover letter (#170)
  start_flow        — create or resume a flow session (US109)
  advance_flow      — advance a flow to the next step (US109)
  get_flow_state    — get current flow session state (US109)
  import_cv         — seed or extend the Master Profile from a PDF or CV text
  add_role          — add a new work-experience role to the Master Profile
  create_application — create a new job application record
  update_application — update user-managed fields (status, notes, deadline, source_url, submitted pins, stale-CV dismiss)
  list_applications  — list all job applications for the current user
  get_application    — retrieve a single job application by ID

Resources:
  profile://current       — current MasterProfile JSON
  job://{job_id}          — JobAnalysis JSON
  cv://{cv_id}            — GeneratedCV metadata JSON
  flow://{flow_id}        — FlowStateResponse JSON
"""

import base64
import binascii
import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from applire.config import settings
from applire.mcp.deps import get_db
from applire.mcp.errors import internal, invalid_input, not_found
from applire.models.application import UserStatus
from applire.models.cv import GeneratedCV
from applire.models.job import JobAnalysis
from applire.models.user import User
from applire.norms import DEFAULT_REGION, REGION_NORMS
from applire.providers import get_provider
from applire.schemas.application import (
    ApplicationListResponse,
    ApplicationResponse,
    CreateApplicationRequest,
    PatchApplicationRequest,
)
from applire.schemas.cover_letter import CoverLetterGenerateRequest
from applire.schemas.cv import GeneratedCVResponse
from applire.schemas.job import JobAnalysisResponse
from applire.schemas.flow import AdvanceFlowRequest, CreateFlowRequest
from applire.schemas.profile_roles import AddRoleRequest, CloseRoleEntry
from applire.services.profile.role_add import add_role_to_profile, AddRoleValidationError
from applire.services.scraper import ScraperError, scrape_job_url
from applire.services import application as app_svc
from applire.services import cover_letter as cover_letter_svc
from applire.services import cv as cv_svc
from applire.services import gap as gap_svc
from applire.services import job as job_svc
from applire.services import profile as profile_svc
from applire.services import session as session_svc
from applire.services.flow import orchestrator as flow_svc
from applire.services.flow.orchestrator import ArtifactRequiredError, InvalidTransitionError

MAX_CV_BYTES = 10 * 1024 * 1024  # 10 MB pre-encode cap (ADR-010 amendment)

logger = logging.getLogger(__name__)

mcp = FastMCP("Applire")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def warn_if_base_url_unset() -> None:
    """Warn at server startup when APPLIRE_BASE_URL was never set (#168).

    Every MCP tool that returns an artifact link (generate_cv, get_cv_status,
    generate_cover_letter, ...) builds html_url/pdf_url from
    ``settings.applire_base_url``, which defaults to
    ``http://localhost:8001``. Behind a reverse proxy (nginx, Caddy, ...) that
    default silently points agents at an unreachable URL instead of the
    externally-reachable one.

    pydantic-settings can't distinguish "the deployer set it to the default
    value" from "the deployer never set it" — so this checks os.environ
    directly rather than settings.applire_base_url.
    """
    if "APPLIRE_BASE_URL" not in os.environ:
        logger.warning(
            "APPLIRE_BASE_URL is not set — MCP artifact URLs (html_url/pdf_url) "
            "will default to %s, which will be wrong for any non-default "
            "deployment. Set APPLIRE_BASE_URL to the externally-reachable "
            "scheme://host:port of your reverse proxy (see .env.example).",
            settings.applire_base_url,
        )


def _parse_uuid(value: str, param: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise invalid_input(f"{param} must be a valid UUID, got: {value!r}")


def _profile_summary(profile_response) -> dict:
    """Non-sensitive extraction summary for agents — never the raw profile."""
    data = profile_response.model_dump(mode="json")
    profile = data.get("profile") or {}
    stats = data.get("stats") or {}
    return {
        "profile_id": data.get("id"),
        "positions": stats.get("positions"),
        "skills_count": len(profile.get("skills") or []),
        "completeness": data.get("completeness"),
        "merge_conflicts": len(data.get("merge_conflicts") or []),
    }


async def _current_user_id(db) -> uuid.UUID:
    """Resolve the single local user (Community single-user mode)."""
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if user is None:
        raise not_found("No user found — import a CV first via import_cv")
    return user.id


# ---------------------------------------------------------------------------
# Tools (7.2 – 7.8)
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Seed or extend the Master Profile from a CV. Primary: file_base64 = a "
        "base64-encoded PDF (<=10 MB). Call once per CV to merge multiple documents. "
        "Fallback: text = already-extracted CV text. Returns an extraction summary "
        "(never the raw profile). Oversize files: use REST POST /api/profile/upload."
    )
)
async def import_cv(
    file_base64: str | None = None,
    filename: str | None = None,
    text: str | None = None,
) -> dict:
    # `filename` is reserved (arc42 §5.3.6a) for a future format hint; ignored for now.
    provider = get_provider()
    if file_base64:
        try:
            raw = base64.b64decode(file_base64, validate=True)
        except (binascii.Error, ValueError):
            raise invalid_input("file_base64 is not valid base64")
        if len(raw) > MAX_CV_BYTES:
            raise invalid_input(
                "CV exceeds 10 MB after decoding — "
                "upload large files via REST POST /api/profile/upload instead."
            )
        async with get_db() as db:
            try:
                result = await profile_svc.import_from_pdf(raw, db, provider)
            except ValueError as exc:
                raise invalid_input(str(exc))
            except Exception as exc:
                raise internal(str(exc))
    elif text and text.strip():
        async with get_db() as db:
            try:
                result = await profile_svc.import_from_text(text.strip(), db, provider)
            except ValueError as exc:
                raise invalid_input(str(exc))
            except Exception as exc:
                raise internal(str(exc))
    else:
        raise invalid_input("Provide either file_base64 (base64 PDF) or text")
    return _profile_summary(result)


@mcp.tool(
    description=(
        "Analyse a job description and return a structured JobAnalysis. "
        "Provide exactly one of: text (the JD body) or url (scraped server-side). "
        "If the JD matches a job already in the user's application pipeline "
        "(repost recognition), the response carries a duplicate_of hint with the "
        "existing application_id — offer to open that application instead of "
        "creating a new one; never block on it."
    )
)
async def analyze_jd(text: str | None = None, url: str | None = None) -> dict:
    if not text and not url:
        raise invalid_input("Provide either text or url")
    if text and url:
        raise invalid_input("Provide only one of text or url")
    provider = get_provider()
    source_url = None
    if url:
        try:
            jd_text = await scrape_job_url(url)
        except ScraperError as exc:
            raise invalid_input(f"Could not scrape {url}: {exc}")
        source_url = url
    else:
        jd_text = text.strip()
        if not jd_text:
            raise invalid_input("text must not be empty")
    async with get_db() as db:
        try:
            result = await job_svc.analyze_jd(jd_text, db, provider, source_url=source_url)
        except Exception as exc:
            raise internal(str(exc))
        # Branch F (E039/US220): repost hint against the user's own pipeline.
        # Best-effort — no user yet (fresh install) or any lookup failure just
        # skips the hint; the analysis itself must never fail because of it.
        try:
            uid = await _current_user_id(db)
            result.duplicate_of = await app_svc.find_duplicate_application(
                uid,
                job_analysis_id=result.id,
                source_url=source_url,
                raw_text=jd_text,
                db=db,
            )
        except Exception:
            pass
    return result.model_dump(mode="json")


@mcp.tool(description="Return the current MasterProfile.")
async def get_profile() -> dict:
    async with get_db() as db:
        result = await profile_svc.get_profile(db)
    if result is None:
        raise not_found("No profile found — import a CV first via POST /api/profile/import")
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Update a section of the MasterProfile. "
        f"section must be one of: {', '.join(sorted(profile_svc._VALID_SECTIONS))}. "
        "data is a dict for object-shaped sections (e.g. personal_info) or a list "
        "for list-shaped sections (e.g. skills, work_experience)."
    )
)
async def update_profile(section: str, data: dict | list) -> dict:
    async with get_db() as db:
        try:
            result = await profile_svc.patch_profile_section(section, data, db)
        except ValueError as exc:
            raise invalid_input(str(exc))
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(description="Analyse gaps between the current profile and the specified job.")
async def analyze_gaps(job_id: str) -> dict:
    jid = _parse_uuid(job_id, "job_id")
    provider = get_provider()
    async with get_db() as db:
        try:
            result = await gap_svc.analyze_gaps(jid, db, provider)
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Start a gap-fill interview session for the given job. "
        "Requires a gap analysis to exist (call analyze_gaps first). "
        "Returns session_id and the first question."
    )
)
async def run_interview(job_id: str) -> dict:
    jid = _parse_uuid(job_id, "job_id")
    provider = get_provider()
    async with get_db() as db:
        try:
            from applire.schemas.session import SessionCreateRequest as _SCR
            result = await session_svc.create_session(_SCR(job_id=jid), db, provider)
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Send a message in an active interview session. "
        "Returns the next question, or {complete: true} when the session is finished. "
        "If 'pending_confirmations' is present, the system is unsure whether a fact "
        "matches an existing profile entry (e.g. two role titles for one job) and is "
        "asking you to confirm — reply by sending one of the listed 'options' as the "
        "next message; never assume the answer."
    )
)
async def send_message(session_id: str, message: str) -> dict:
    sid = _parse_uuid(session_id, "session_id")
    if not message.strip():
        raise invalid_input("message must not be empty")
    provider = get_provider()
    async with get_db() as db:
        try:
            result = await session_svc.send_message(sid, message.strip(), db, provider)
        except LookupError as exc:
            raise not_found(str(exc))
        except ValueError as exc:
            raise invalid_input(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Generate a tailored CV for the given job. "
        "Returns cv_id, html_url, and pdf_url. "
        "The URLs point to the FastAPI backend (APPLIRE_BASE_URL). "
        "Optional target_pages pins the CV to a specific page count for this "
        f"generation only ({DEFAULT_REGION} norm: "
        f"{REGION_NORMS[DEFAULT_REGION].cv_standard_pages} pages standard, "
        f"{REGION_NORMS[DEFAULT_REGION].cv_max_pages} max); omit it to use "
        "the user's default setting, then the region standard."
    )
)
async def generate_cv(job_id: str, target_pages: int | None = None) -> dict:
    jid = _parse_uuid(job_id, "job_id")
    if target_pages is not None and target_pages < 1:
        raise invalid_input("target_pages must be >= 1")
    provider = get_provider()
    async with get_db() as db:
        try:
            result = await cv_svc.generate_cv(
                jid,
                db,
                provider,
                base_url=settings.applire_base_url,
                target_pages=target_pages,
            )
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Poll the status of a CV generation. "
        "Returns {cv_id, status, html_url?, pdf_url?, expires_at?}. "
        "status: 'pending' | 'generating' | 'ready' | 'failed'."
    )
)
async def get_cv_status(cv_id: str) -> dict:
    cid = _parse_uuid(cv_id, "cv_id")
    async with get_db() as db:
        try:
            result = await cv_svc.get_cv_status(cid, db, settings.applire_base_url)
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Get the persisted ATS audit report for a generated CV (ADR-039). "
        "Returns {document_id, status, report}; report is null while generation/audit "
        "is pending or unavailable. report = {checks: [{id, status, details?}], "
        "keywords: {present, missing}} — named checks, no aggregate score."
    )
)
async def get_cv_ats_report(cv_id: str) -> dict:
    cid = _parse_uuid(cv_id, "cv_id")
    async with get_db() as db:
        try:
            result = await cv_svc.get_cv_ats_report(cid, db)
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Generate a cover letter for the given job. Requires an existing flow "
        "session for the job (call start_flow first) — raises not_found if none "
        "exists. Returns cover_letter_id, status, html_url, and pdf_url. "
        "The URLs point to the FastAPI backend (APPLIRE_BASE_URL). Editing a "
        "generated cover letter's sections is UI-only — there is no MCP tool for it."
    )
)
async def generate_cover_letter(job_id: str) -> dict:
    jid = _parse_uuid(job_id, "job_id")
    provider = get_provider()
    async with get_db() as db:
        try:
            result = await cover_letter_svc.generate_cover_letter(
                CoverLetterGenerateRequest(job_id=jid),
                db,
                provider,
                base_url=settings.applire_base_url,
            )
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Poll the status of a cover letter generation. "
        "Returns {cover_letter_id, status, html_url?, pdf_url?, expires_at?}. "
        "status: 'pending' | 'generating' | 'ready' | 'failed'."
    )
)
async def get_cover_letter_status(cover_letter_id: str) -> dict:
    cid = _parse_uuid(cover_letter_id, "cover_letter_id")
    async with get_db() as db:
        try:
            result = await cover_letter_svc.get_cover_letter_status(
                cid, db, settings.applire_base_url
            )
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Get the persisted ATS audit report for a generated cover letter (ADR-039). "
        "Returns {document_id, status, report}; report is null while generation/audit "
        "is pending or unavailable. report = {checks: [{id, status, details?}], "
        "keywords: {present, missing}} — named checks, no aggregate score."
    )
)
async def get_cover_letter_ats_report(cover_letter_id: str) -> dict:
    cid = _parse_uuid(cover_letter_id, "cover_letter_id")
    async with get_db() as db:
        try:
            result = await cover_letter_svc.get_cover_letter_ats_report(cid, db)
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Create or resume a flow session. Pass job_id to bind the flow to a job "
        "(idempotent per user+job); omit it for a CV-only flow. Returns flow_id + state."
    )
)
async def start_flow(job_id: str | None = None) -> dict:
    jid = _parse_uuid(job_id, "job_id") if job_id else None
    async with get_db() as db:
        # _current_user_id raises McpError directly; keep it outside the try so a
        # missing user stays -32001 NotFound rather than being remapped to -32603.
        uid = await _current_user_id(db)
        try:
            result = await flow_svc.create_flow(
                CreateFlowRequest(job_id=jid), uid, db, settings.applire_base_url
            )
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Advance a flow to the next step. Steps that produce an artifact require "
        "artifact_id (gap_analysis→gap_analysis_id, interview→interview_session_id, "
        "complete→generated_cv_id). flow_id is the stable handle for session recovery."
    )
)
async def advance_flow(flow_id: str, step: str, artifact_id: str | None = None) -> dict:
    fid = _parse_uuid(flow_id, "flow_id")
    aid = _parse_uuid(artifact_id, "artifact_id") if artifact_id else None
    async with get_db() as db:
        try:
            result = await flow_svc.advance_flow(
                fid, AdvanceFlowRequest(step=step, artifact_id=aid), db, settings.applire_base_url
            )
        except (InvalidTransitionError, ArtifactRequiredError) as exc:
            raise invalid_input(str(exc))
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(description="Get the current state of a flow session, including available actions.")
async def get_flow_state(flow_id: str) -> dict:
    fid = _parse_uuid(flow_id, "flow_id")
    async with get_db() as db:
        try:
            result = await flow_svc.get_flow_state(fid, db, settings.applire_base_url)
        except LookupError as exc:
            raise not_found(str(exc))
    return result.model_dump(mode="json")


# Valid user_status values, derived from the enum so tool descriptions and
# error messages can never go stale again (the old literal lacked 'hired').
_USER_STATUS_VALUES = ", ".join(m.value for m in UserStatus)


def _parse_user_status(raw: str, field: str) -> UserStatus:
    try:
        return UserStatus(raw)
    except ValueError:
        raise invalid_input(
            f"Invalid {field}: {raw!r}. Must be one of: {_USER_STATUS_VALUES}."
        )


@mcp.tool(
    description=(
        "List the user's application pipeline. "
        f"Optional status_filter: {_USER_STATUS_VALUES}."
    )
)
async def list_applications(status_filter: str | None = None) -> list[dict]:
    user_status = None
    if status_filter:
        user_status = _parse_user_status(status_filter, "status_filter")
    # Retrieve the single user from the DB (MCP runs in single-user context).
    async with get_db() as db:
        user_result = await db.execute(select(User).limit(1))
        user = user_result.scalar_one_or_none()
        if user is None:
            raise not_found("No user found — create a user first")
        try:
            result = await app_svc.list_applications(
                user_id=user.id,
                db=db,
                workflow_status=None,
                user_status=user_status,
            )
        except Exception as exc:
            raise internal(str(exc))
    return [item.model_dump(mode="json") for item in result.items]


@mcp.tool(
    description=(
        "Get details for a specific application by ID. A non-null stale_cv "
        "field means the Master Profile grew after the newest CV was tailored "
        "(stale_cv.gained lists what changed per section) — offer to re-tailor "
        "via generate_cv for the same job, or mute the hint with "
        "update_application(dismiss_stale_cv=true). Never regenerate without "
        "asking; a pinned submitted version is never replaced."
    )
)
async def get_application(application_id: str) -> dict:
    aid = _parse_uuid(application_id, "application_id")
    async with get_db() as db:
        uid = await _current_user_id(db)
        try:
            result = await app_svc.get_application(aid, uid, db)
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Log an application to the user's pipeline. job_id is the JobAnalysis id; "
        "company_name/role_title default from the job when omitted. "
        "source_url records where the posting was found (defaults from the job "
        "when it was analyzed from a URL). "
        "start_workflow=true atomically creates the flow session."
    )
)
async def create_application(
    job_id: str,
    start_workflow: bool = False,
    company_name: str | None = None,
    role_title: str | None = None,
    deadline: str | None = None,
    source_url: str | None = None,
) -> dict:
    jid = _parse_uuid(job_id, "job_id")
    dl = None
    if deadline:
        try:
            dl = datetime.fromisoformat(deadline)
        except ValueError:
            raise invalid_input("deadline must be ISO 8601 (e.g. 2026-07-01T00:00:00)")
    req = CreateApplicationRequest(
        job_analysis_id=jid,
        start_workflow=start_workflow,
        company_name=company_name,
        role_title=role_title,
        deadline=dl,
        source_url=source_url,
    )
    async with get_db() as db:
        uid = await _current_user_id(db)
        try:
            result = await app_svc.create_application(uid, req, db)
        except app_svc.ConflictError as exc:
            raise invalid_input(str(exc))
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Update user-managed fields on an application (MCP mirror of "
        "PATCH /api/applications/{id}). Omitted fields are left unchanged. "
        f"user_status must be one of: {_USER_STATUS_VALUES}. "
        "deadline is ISO 8601. source_url records where the posting was found. "
        "submitted_cv_id / submitted_cover_letter_id pin the exact generated "
        "document that was sent to the employer (must belong to this "
        "application's job); pinned documents are kept while the application "
        "is active. dismiss_stale_cv=true mutes an application's stale-CV "
        "re-tailor hint (the stale_cv field on get/list responses) until the "
        "profile grows again."
    )
)
async def update_application(
    application_id: str,
    user_status: str | None = None,
    company_name: str | None = None,
    role_title: str | None = None,
    notes: str | None = None,
    deadline: str | None = None,
    source_url: str | None = None,
    submitted_cv_id: str | None = None,
    submitted_cover_letter_id: str | None = None,
    dismiss_stale_cv: bool | None = None,
) -> dict:
    aid = _parse_uuid(application_id, "application_id")
    # Build the request from provided fields only, so PatchApplicationRequest's
    # model_fields_set semantics stay honest (E039: omitted ≠ explicit null).
    fields: dict = {}
    if user_status is not None:
        fields["user_status"] = _parse_user_status(user_status, "user_status")
    if company_name is not None:
        fields["company_name"] = company_name
    if role_title is not None:
        fields["role_title"] = role_title
    if notes is not None:
        fields["notes"] = notes
    if deadline is not None:
        try:
            fields["deadline"] = datetime.fromisoformat(deadline)
        except ValueError:
            raise invalid_input("deadline must be ISO 8601 (e.g. 2026-07-01T00:00:00)")
    if source_url is not None:
        fields["source_url"] = source_url
    if submitted_cv_id is not None:
        fields["submitted_cv_id"] = _parse_uuid(submitted_cv_id, "submitted_cv_id")
    if submitted_cover_letter_id is not None:
        fields["submitted_cover_letter_id"] = _parse_uuid(
            submitted_cover_letter_id, "submitted_cover_letter_id"
        )
    if dismiss_stale_cv is not None:
        fields["dismiss_stale_cv"] = dismiss_stale_cv
    if not fields:
        raise invalid_input(
            "At least one field must be provided (user_status, company_name, "
            "role_title, notes, deadline, source_url, submitted_cv_id, "
            "submitted_cover_letter_id, dismiss_stale_cv)."
        )
    req = PatchApplicationRequest(**fields)
    async with get_db() as db:
        uid = await _current_user_id(db)
        try:
            result = await app_svc.patch_application(aid, uid, req, db)
        except LookupError as exc:
            raise not_found(str(exc))
        except ValueError as exc:
            raise invalid_input(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


@mcp.tool(
    description=(
        "Add a new ongoing role to the Master Profile (post-hire update). "
        "close_role_ids lists prior open roles to close; each is closed the day "
        "before start_date. Dates are YYYY-MM-DD."
    )
)
async def add_role(
    title: str,
    company: str,
    start_date: str,
    location: str | None = None,
    industry: str | None = None,
    close_role_ids: list[str] | None = None,
) -> dict:
    try:
        start = date.fromisoformat(start_date)
    except ValueError:
        raise invalid_input("start_date must be YYYY-MM-DD")
    close_end = (start - timedelta(days=1)).isoformat()
    close_roles = [CloseRoleEntry(role_id=rid, end_date=close_end) for rid in (close_role_ids or [])]
    req = AddRoleRequest(
        title=title, company=company, start_date=start_date,
        location=location, industry=industry, close_roles=close_roles, source="manual",
    )
    async with get_db() as db:
        try:
            result = await add_role_to_profile(req, db)
        except AddRoleValidationError as exc:
            raise invalid_input(str(exc))
        except LookupError as exc:
            raise not_found(str(exc))
        except Exception as exc:
            raise internal(str(exc))
    return result.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Resources (7.9 – 7.11)
# ---------------------------------------------------------------------------


@mcp.resource(
    "profile://current",
    mime_type="application/json",
    description="Current MasterProfile JSON.",
)
async def resource_profile() -> str:
    async with get_db() as db:
        result = await profile_svc.get_profile(db)
    if result is None:
        raise not_found("No profile found")
    return json.dumps(result.model_dump(mode="json"))


@mcp.resource(
    "job://{job_id}",
    mime_type="application/json",
    description="JobAnalysis JSON for the given job_id.",
)
async def resource_job(job_id: str) -> str:
    jid = _parse_uuid(job_id, "job_id")
    async with get_db() as db:
        result = await db.execute(
            select(JobAnalysis).where(
                JobAnalysis.id == jid,
                JobAnalysis.deleted_at.is_(None),
            )
        )
        record = result.scalar_one_or_none()
    if record is None:
        raise not_found(f"Job analysis {job_id} not found")
    return json.dumps(JobAnalysisResponse.model_validate(record).model_dump(mode="json"))


@mcp.resource(
    "flow://{flow_id}",
    mime_type="application/json",
    description="FlowStateResponse JSON for the given flow_id.",
)
async def resource_flow(flow_id: str) -> str:
    fid = _parse_uuid(flow_id, "flow_id")
    async with get_db() as db:
        try:
            result = await flow_svc.get_flow_state(fid, db, settings.applire_base_url)
        except LookupError as exc:
            raise not_found(str(exc))
    return json.dumps(result.model_dump(mode="json"))


@mcp.resource(
    "cv://{cv_id}",
    mime_type="application/json",
    description="GeneratedCV metadata JSON for the given cv_id.",
)
async def resource_cv(cv_id: str) -> str:
    cid = _parse_uuid(cv_id, "cv_id")
    async with get_db() as db:
        result = await db.execute(
            select(GeneratedCV).where(
                GeneratedCV.id == cid,
                GeneratedCV.deleted_at.is_(None),
            )
        )
        record = result.scalar_one_or_none()
    if record is None:
        raise not_found(f"Generated CV {cv_id} not found")
    return json.dumps(GeneratedCVResponse.model_validate(record).model_dump(mode="json"))
