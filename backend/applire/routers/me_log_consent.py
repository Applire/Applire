# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The person's consent to content logs (ADR-091 amended 2026-11-18).

``GET`` reads the state and the text values, ``POST`` agrees to today's text,
``DELETE`` withdraws. Session only: consent is a person's act, never an API
token's (ADR-091 cl. 17). At LOG_LEVEL above DEBUG every door answers
``required: false`` and ``POST``/``DELETE`` change nothing (404 ``not_required``
would make a stale tab fail loudly for no reason; the state is what it is).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_session_user
from applire.db.session import get_db
from applire.models.user import User
from applire.schemas.auth import LogConsentRequest, LogConsentState
from applire.services import audit, log_consent

router = APIRouter(prefix="/api/me", tags=["me"])


async def _fresh(db: AsyncSession, user: User) -> User:
    return await db.get(User, user.id, populate_existing=True) or user


@router.get("/log-consent", response_model=LogConsentState)
async def read_log_consent(
    user: User = Depends(require_session_user), db: AsyncSession = Depends(get_db)
) -> LogConsentState:
    return LogConsentState(**log_consent.summary(await _fresh(db, user)))


@router.post("/log-consent", response_model=LogConsentState)
async def agree_log_consent(
    body: LogConsentRequest,
    user: User = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> LogConsentState:
    row = await _fresh(db, user)
    if log_consent.state(row) == "not_required":
        return LogConsentState(**log_consent.summary(row))
    try:
        log_consent.record_consent(row, body.version)
    except log_consent.ConsentVersionStale:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "log_consent_version_stale",
                "message": "The consent text changed while you were reading it. Read it again.",
            },
        )
    await audit.record(db, actor_id=row.id, action="log_consent.given", target_type="user",
                       target_id=row.id, details={"version": body.version})
    await db.commit()
    return LogConsentState(**log_consent.summary(row))


@router.delete("/log-consent", response_model=LogConsentState)
async def withdraw_log_consent(
    user: User = Depends(require_session_user), db: AsyncSession = Depends(get_db)
) -> LogConsentState:
    row = await _fresh(db, user)
    if log_consent.state(row) in ("not_required", "withdrawn"):
        return LogConsentState(**log_consent.summary(row))
    log_consent.withdraw(row)
    await audit.record(db, actor_id=row.id, action="log_consent.withdrawn", target_type="user",
                       target_id=row.id, details={})
    await db.commit()
    return LogConsentState(**log_consent.summary(row))


__all__ = ["router"]
