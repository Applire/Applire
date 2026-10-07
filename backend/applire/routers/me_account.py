# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``DELETE /api/me/account`` — a person deletes their own account (ADR-091 cl. 25,
RD-3 / founder ruling D-3; US326).

Session only (``require_session_user`` — an ``api`` bearer cannot delete the
account it belongs to). Re-authentication first (cl. 23): a person with a
password re-enters it (wrong → 403 ``invalid_credentials``, not 401, so the shell
does not treat it as a lost session); a person without one must hold a verified
fresh-OIDC grant for ``account.delete`` (W3, package 1d) — until then 403
``reauth_required`` (fail closed). Refused with 409 ``last_admin`` for the last
active admin. Then ``erasure.erase(db, user_id, "account")`` + tombstone + audit
(``services/admin/users.delete_account``) and the cookie is cleared.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_session_user
from applire.db.session import get_db
from applire.models.user import User
from applire.schemas.me import AccountDeleteRequest
from applire.services.admin import identity_seams as seams
from applire.services.admin import users as accounts

router = APIRouter(prefix="/api/me", tags=["me"])


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                         detail={"error_code": code, "message": message})


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_account(
    request: Request,
    body: AccountDeleteRequest = Body(default_factory=AccountDeleteRequest),
    me: User = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if me.password_hash is not None:
        if not body.password or not await seams.verify_password(body.password, me.password_hash):
            raise _forbidden("invalid_credentials", "The password is not correct.")
    elif not await seams.consume_reauth_grant(
        db, request=request, user=me, action="account.delete", target_id=me.id
    ):
        raise _forbidden("reauth_required", "Confirm with your sign-in provider first.")

    try:
        await accounts.delete_account(db, actor=me, user_id=me.id, by="self")
    except accounts.AccountError as exc:
        raise exc.to_http() from exc

    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    seams.clear_session_cookie(response)
    return response
