# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin views of the instance (#694, S-11, B2-2; ADR-086 amended 2026-10-07).

``GET /api/admin/audit`` · ``GET /api/admin/usage`` · ``GET /api/admin/dashboard`` ·
``GET /api/admin/notices`` — all ``require_admin``, all metadata only (S-4, RD-6).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_admin
from applire.db.session import get_db
from applire.models.user import User
from applire.schemas.admin import (
    AdminDashboardResponse,
    AdminNoticesResponse,
    AdminUsageResponse,
    AuditPageResponse,
)
from applire.services.admin import audit_view, dashboard, usage

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/audit", response_model=AuditPageResponse)
async def audit_log(
    action: list[str] = Query(default_factory=list),
    actor_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=audit_view.DEFAULT_LIMIT, ge=1, le=audit_view.MAX_LIMIT),
    cursor: str | None = Query(default=None, max_length=200),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> AuditPageResponse:
    try:
        page = await audit_view.page(
            db,
            actions=action,
            actor_id=actor_id,
            target_user_id=target_user_id,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
    except audit_view.InvalidCursor:
        raise HTTPException(
            status_code=422,
            detail={"error_code": "invalid_cursor", "message": "Invalid cursor."},
        ) from None
    return AuditPageResponse(**page)


@router.get("/usage", response_model=AdminUsageResponse)
async def usage_view(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> AdminUsageResponse:
    return AdminUsageResponse(**await usage.usage_report(db, days=days))


@router.get("/dashboard", response_model=AdminDashboardResponse)
async def dashboard_view(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> AdminDashboardResponse:
    return AdminDashboardResponse(**await dashboard.dashboard(db))


@router.get("/notices", response_model=AdminNoticesResponse)
async def notices_view(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> AdminNoticesResponse:
    items = await dashboard.notices_cheap(db)
    return AdminNoticesResponse(count=len(items), items=items)
