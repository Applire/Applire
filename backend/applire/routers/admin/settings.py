# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/api/admin/settings`` — runtime instance settings (ADR-093; #710 #726 #738).

Reads: ``require_admin``. Writes: ``require_admin_session`` (no bearer — a leaked
admin token must not reroute every user's documents, ADR-093 cl. 7).

The ``PUT`` body is read as raw JSON and validated by the service, NOT by a
pydantic model: FastAPI's default 422 echoes the failing ``input`` — for a dict
that is every value in it, i.e. the secrets (adversarial pass 2026-10-07). Every
refusal here names the key only.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from applire import config as _config
from applire.auth.deps import require_admin, require_admin_session
from applire.db.session import get_db
from applire.models.user import User
from applire.schemas.admin import InstanceSettingsResponse
from applire.services import instance_settings as svc

router = APIRouter(prefix="/api/admin/settings", tags=["admin"])


def _http(exc: svc.SettingsError) -> HTTPException:
    detail: dict[str, Any] = {"error_code": exc.code, "message": exc.message}
    detail.update({k: v for k, v in exc.extra.items() if k in ("key", "provider", "reason")})
    return HTTPException(status_code=exc.status, detail=detail)


async def _response_after_write(db: AsyncSession) -> InstanceSettingsResponse:
    """Re-read and re-pin: the request was pinned to the PRE-write snapshot."""
    await svc.after_commit()
    from applire.services.ops import probes

    probes.reset_provider_cache()
    token = _config.pin_overlay()
    try:
        return InstanceSettingsResponse(**await svc.build_response(db))
    finally:
        _config.unpin_overlay(token)


@router.get("", response_model=InstanceSettingsResponse)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> InstanceSettingsResponse:
    return InstanceSettingsResponse(**await svc.build_response(db))


@router.put("", response_model=InstanceSettingsResponse)
async def put_settings(
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin_session),
) -> InstanceSettingsResponse:
    try:
        body = json.loads(await request.body() or b"null")
    except (ValueError, UnicodeDecodeError):
        body = None
    changes = body.get("changes") if isinstance(body, dict) else None
    if (
        not isinstance(changes, dict)
        or not changes
        or len(changes) > 20
        or set(body) != {"changes"}
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "invalid_setting_value",
                "message": "Body must be {\"changes\": {KEY: value}} with 1-20 keys.",
            },
        )
    try:
        await svc.apply_changes(db, actor_id=admin.id, changes=changes)
        await db.commit()
    except svc.SettingsError as exc:
        await db.rollback()
        raise _http(exc) from None
    return await _response_after_write(db)


@router.delete("/{key}", response_model=InstanceSettingsResponse)
async def reset_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin_session),
) -> InstanceSettingsResponse:
    try:
        await svc.reset(db, actor_id=admin.id, key=key)
        await db.commit()
    except svc.SettingsError as exc:
        await db.rollback()
        raise _http(exc) from None
    return await _response_after_write(db)
