# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Profile Health actions (#764) — ``POST /api/profile/health/recompute-span``.

A separate router for the same reason ``routers/signature.py`` is one:
``routers/profile.py`` is already the largest door in the app, and the Health
hub's one-click repair is a self-contained act with one service behind it
(``services/profile/span_provenance.recompute_span``). The door is an adapter
(ADR-066): it resolves the caller, maps the service's refusals to status
codes, and owns nothing else. ``GET /api/profile/health`` stays where it is.

Two segments under ``/health``, so it cannot collide with
``PATCH /api/profile/{section}``.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_user
from applire.db.session import get_db
from applire.models.user import User
from applire.schemas.profile import MasterProfileResponse
from applire.services.profile.commit import StaleEditError, VaultWriteRevertedError
from applire.services.profile.span_provenance import SpanNotLaunderedError, recompute_span

router = APIRouter(prefix="/api/profile/health", tags=["profile"])


class RecomputeSpanRequest(BaseModel):
    skill_id: str


@router.post("/recompute-span", response_model=MasterProfileResponse)
async def recompute_span_endpoint(
    body: RecomputeSpanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MasterProfileResponse:
    """Re-derive one laundered skill span from the role dates (#764).

    409 when the skill is not (or no longer) flagged — the Health item was
    stale; the client refreshes the hub instead of retrying.
    """
    try:
        return await recompute_span(
            db, body.skill_id, user_id=getattr(current_user, "id", None)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except (SpanNotLaunderedError, StaleEditError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This skill duration no longer needs recalculating.",
        )
    except VaultWriteRevertedError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The profile could not be saved; nothing was changed.",
        )
