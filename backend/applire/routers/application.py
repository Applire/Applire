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

"""Application router — Iteration 17

6 endpoints:
  GET  /api/applications            — list user's pipeline
  POST /api/applications            — add job to tracking
  GET  /api/applications/{id}       — detail with flow state
  PATCH /api/applications/{id}      — update user-managed fields
  DELETE /api/applications/{id}     — remove from pipeline (soft-delete cascade)
  POST /api/applications/{id}/start — create FlowSession (deferred activation)
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.db.session import get_db
from applire.models.application import UserStatus, WorkflowStatus
from applire.schemas.application import (
    ApplicationListResponse,
    ApplicationResponse,
    CreateApplicationRequest,
    PatchApplicationRequest,
)
from applire.schemas.application_mark_hired import MarkHiredResponse
from applire.services.application import (
    ConflictError,
    create_application,
    delete_application,
    get_application,
    list_applications,
    mark_application_hired,
    patch_application,
    start_application_workflow,
)

router = APIRouter(prefix="/api/applications", tags=["applications"])


@router.get("", response_model=ApplicationListResponse)
async def list_pipeline(
    request: Request,
    workflow_status: WorkflowStatus | None = Query(default=None),
    user_status: UserStatus | None = Query(default=None),
    q: str | None = Query(default=None, description="Search role_title, company_name, notes"),
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
) -> ApplicationListResponse:
    """List all applications for the current user, sorted updated_at DESC."""
    user = await auth.get_current_user(request)
    return await list_applications(user.id, db, workflow_status, user_status, q)


@router.post("", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
async def create(
    body: CreateApplicationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
) -> ApplicationResponse:
    """Add a job to the tracking pipeline.

    Provide start_workflow=true for atomic create-and-start (same code path as POST /{id}/start).
    Requires a pre-existing job_analysis_id — call POST /api/job/analyze first.
    """
    user = await auth.get_current_user(request)
    try:
        return await create_application(user.id, body, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/{application_id}", response_model=ApplicationResponse)
async def get(
    application_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
) -> ApplicationResponse:
    user = await auth.get_current_user(request)
    try:
        return await get_application(application_id, user.id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.patch("/{application_id}", response_model=ApplicationResponse)
async def patch(
    application_id: uuid.UUID,
    body: PatchApplicationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
) -> ApplicationResponse:
    """Update user-managed fields. workflow_status is system-managed and will be rejected (422)."""
    user = await auth.get_current_user(request)
    try:
        return await patch_application(application_id, user.id, body, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.delete("/{application_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    application_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
) -> None:
    """Soft-delete the application and cascade to attached FlowSession."""
    user = await auth.get_current_user(request)
    try:
        await delete_application(application_id, user.id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/{application_id}/start", response_model=ApplicationResponse)
async def start_workflow(
    application_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
) -> ApplicationResponse:
    """Create a FlowSession for a tracking application (deferred activation).

    Returns 409 if a workflow has already been started for this application.
    """
    user = await auth.get_current_user(request)
    try:
        return await start_application_workflow(application_id, user.id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/{application_id}/mark-hired", response_model=MarkHiredResponse)
async def mark_hired(
    application_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    auth: AuthProvider = Depends(get_auth_provider),
) -> MarkHiredResponse:
    user = await auth.get_current_user(request)
    try:
        return await mark_application_hired(application_id, user.id, db)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
