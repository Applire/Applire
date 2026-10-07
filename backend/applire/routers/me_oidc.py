# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``POST /api/me/oidc/link`` and ``DELETE /api/me/oidc`` (ADR-091 cl. 6, 23; MD-7; US323).

Both session only, 404 while ``OIDC_ISSUER`` is empty.

* **Link** — an existing account (one with a password) links its IdP identity
  from a signed-in session: the state cookie carries ``intent=link`` and the
  session user's ``uid``; the callback links only when the session finishing the
  flow is that same user (R3-2iii). Audited ``oidc.linked``.
* **Unlink** (ruling on CONTRACT-CHANGE 1d-1) — refused with 409
  ``last_credential`` when the account has no password (nobody locks themselves
  out); otherwise it needs a fresh, verified ``oidc.unlink`` grant for this
  session (else 403 ``reauth_required``). Clears the binding; audited
  ``oidc.unlinked``. Unlinking an account that has no binding is a no-op 204.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import oidc
from applire.auth.deps import require_session_user
from applire.auth.reauth import consume_grant
from applire.db.session import get_db
from applire.models.user import User
from applire.routers.auth_oidc import require_oidc_enabled
from applire.routers.me_reauth import idp_unreachable
from applire.schemas.me import AuthorizeRedirectResponse

router = APIRouter(prefix="/api/me", tags=["me"])


@router.post("/oidc/link", response_model=AuthorizeRedirectResponse)
async def oidc_link(
    response: Response,
    me: User = Depends(require_session_user),
) -> AuthorizeRedirectResponse:
    require_oidc_enabled()
    try:
        url, flow = await oidc.begin("link", next_path="/settings", uid=me.id)
    except oidc.OidcError as exc:
        raise idp_unreachable() from exc
    oidc.set_state_cookie(response, flow)
    response.headers["Cache-Control"] = "no-store"
    return AuthorizeRedirectResponse(authorize_url=url)


@router.delete("/oidc", status_code=status.HTTP_204_NO_CONTENT)
async def oidc_unlink(
    request: Request,
    me: User = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    require_oidc_enabled()
    if me.oidc_subject is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if me.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "last_credential",
                    "message": "This sign-in is the only way into this account."},
        )
    if not await consume_grant(db, request=request, user=me, action="oidc.unlink", target_id=me.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "reauth_required",
                    "message": "Confirm with your sign-in provider first."},
        )
    await db.execute(
        update(User)
        .where(User.id == me.id)
        .values(oidc_issuer=None, oidc_subject=None)
        .execution_options(synchronize_session=False)
    )
    from applire.services.audit import record

    await record(db, actor_id=me.id, action="oidc.unlinked", target_type="user",
                 target_id=me.id, details={})
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
