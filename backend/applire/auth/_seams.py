# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guarded imports of sibling Strawberry W1 packages — the ONE place they live.

Package 1a codes against two interfaces other packages fill in parallel:

* 1c's bearer helpers in ``applire.auth.tokens``
  (``request_bearer_user(request, db, scope="api") -> User | None``);
* 1b's audit writer ``applire.services.audit.record(db, *, actor_id, action,
  target_type, target_id, details)`` (no IP, RD-11).

On a branch where either is absent, this module degrades **fail-closed** for
auth (no bearer is ever valid) and **no-op** for audit, so 1a's branch is green
alone. The main session removes this module at integration and imports the
real functions directly (WORK-PACKAGES "Wave 1 dispatch").
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.user import User

logger = logging.getLogger("applire.auth")


async def bearer_user(request: Request, db: AsyncSession, scope: str = "api") -> User | None:
    """1c's ``request_bearer_user``; ``None`` (= invalid bearer, 401) when absent."""
    try:
        from applire.auth.tokens import request_bearer_user  # type: ignore[attr-defined]
    except ImportError:
        logger.debug("auth.tokens.request_bearer_user absent — every bearer is invalid")
        return None
    try:
        return await request_bearer_user(request, db, scope=scope)
    except NotImplementedError:
        return None


async def audit(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None,
    details: dict[str, Any] | None = None,
) -> None:
    """1b's ``services.audit.record``; a debug-logged no-op when absent."""
    try:
        from applire.services.audit import record  # type: ignore[import-not-found]
    except ImportError:
        logger.debug("services.audit absent — audit %s not written", action)
        return
    await record(
        db,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
    )
