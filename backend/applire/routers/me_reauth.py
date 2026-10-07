# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``POST /api/me/reauth/start`` — begin a fresh IdP login for a destructive action
(ADR-091 cl. 23; MD-7; US323).

Session only. Creates a 5-minute ``reauth_grants`` row bound to (user, this
session, action, target) and answers with the IdP URL (``prompt=login&max_age=0``);
the state cookie carries the grant id. Refused (403 ``forbidden``) when the target
is not the person's own account or the account has no OIDC binding. 404 while
``OIDC_ISSUER`` is empty.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import oidc
from applire.auth.deps import require_session_user
from applire.auth.reauth import create_grant, current_session_id
from applire.db.session import get_db
from applire.models.user import User
from applire.routers.auth_oidc import require_oidc_enabled
from applire.schemas.me import AuthorizeRedirectResponse, ReauthStartRequest

router = APIRouter(prefix="/api/me", tags=["me"])


def _forbidden(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                         detail={"error_code": "forbidden", "message": message})


def idp_unreachable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error_code": "oidc_failed", "message": "The sign-in provider could not be reached."},
    )


@router.post("/reauth/start", response_model=AuthorizeRedirectResponse)
async def reauth_start(
    body: ReauthStartRequest,
    request: Request,
    response: Response,
    me: User = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> AuthorizeRedirectResponse:
    require_oidc_enabled()
    if body.target_id != me.id:
        raise _forbidden("You can only confirm actions on your own account.")
    if me.oidc_subject is None:
        raise _forbidden("This account has no sign-in provider to confirm with.")
    session_id = current_session_id(request)
    if session_id is None:
        raise _forbidden("Confirming needs a signed-in session.")
    grant = await create_grant(db, user=me, session_id=session_id,
                               action=body.action, target_id=body.target_id)
    try:
        url, flow = await oidc.begin("reauth", uid=me.id, grant_id=grant.id)
    except oidc.OidcError as exc:
        await db.rollback()
        raise idp_unreachable() from exc
    await db.commit()
    oidc.set_state_cookie(response, flow)
    response.headers["Cache-Control"] = "no-store"
    return AuthorizeRedirectResponse(authorize_url=url)
