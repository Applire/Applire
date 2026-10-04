# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``GET /api/auth/oidc/start`` and ``GET /api/auth/oidc/callback`` (ADR-091 cl. 6, 23; US323).

Both are public (allowlist, cl. 20) and 404 while ``OIDC_ISSUER`` is empty. The
callback serves three intents carried in the signed state cookie:

* ``login`` — a bound ``(issuer, sub)`` signs in (a disabled account →
  ``/login?error=account_disabled``, W0B-3); an unknown identity binds only to a
  pending invited account (MD-7) or → ``/login?error=oidc_no_account``.
* ``link`` — the CURRENT session's user must equal the ``uid`` signed into the
  state (R3-2iii) → ``/settings?oidc=linked``.
* ``reauth`` — the current session must be the grant's and the identity the
  account's own → ``/settings?reauth=<action>`` (CONTRACT-CHANGE 1d-1).

Every failure is a redirect, never a body: ``/login?error=oidc_failed`` for
``login``, ``/settings?oidc=failed`` for ``link``/``reauth``. The reason is
logged (no claim values).
"""

from __future__ import annotations

import logging
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import oidc
from applire.auth.reauth import verify_grant
from applire.auth.sessions import SESSION_COOKIE, issue_session, resolve_session
from applire.db.session import get_db
from applire.models.auth import ReauthGrant
from applire.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth/oidc", tags=["auth"])

LOGIN_FAILED = "/login?error=oidc_failed"
SETTINGS_FAILED = "/settings?oidc=failed"


def require_oidc_enabled() -> None:
    if not oidc.oidc_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


def _redirect(url: str) -> RedirectResponse:
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    oidc.clear_state_cookie(response)
    response.headers["Cache-Control"] = "no-store"
    return response


def _failed(intent: str, reason: str) -> RedirectResponse:
    logger.warning("OIDC callback refused (intent=%s): %s", intent, reason)
    return _redirect(LOGIN_FAILED if intent == "login" else SETTINGS_FAILED)


@router.get("/start")
async def oidc_start(next: str | None = Query(default=None)) -> RedirectResponse:  # noqa: A002
    require_oidc_enabled()
    try:
        url, flow = await oidc.begin("login", next_path=oidc.safe_next(next))
    except oidc.OidcError as exc:
        return _failed("login", exc.reason)
    response = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    response.headers["Cache-Control"] = "no-store"
    oidc.set_state_cookie(response, flow)
    return response


@router.get("/callback")
async def oidc_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    require_oidc_enabled()
    try:
        flow = oidc.decode_state(request.cookies.get(oidc.STATE_COOKIE), state)
    except oidc.OidcError as exc:
        return _failed("login", exc.reason)
    try:
        oidc.claim_state_once(flow)
        if error:
            raise oidc.OidcError("IdP returned an error")
        claims = await oidc.exchange_code(code, flow)
    except oidc.OidcError as exc:
        return _failed(flow.intent, exc.reason)

    if flow.intent == "login":
        return await _finish_login(db, claims, flow)
    return await _finish_signed_in(request, db, claims, flow)


async def _finish_login(db: AsyncSession, claims: oidc.Claims, flow: oidc.FlowState) -> RedirectResponse:
    user = await oidc.find_bound_user(db, claims)
    if user is None:
        bound_id = await oidc.bind_pending_invite(db, claims)
        if bound_id is None:
            logger.info("OIDC sign-in: unknown identity without a pending invitation")
            return _redirect("/login?error=oidc_no_account")
        user = await db.get(User, bound_id, populate_existing=True)
    elif user.deleted_at is not None:
        return _redirect("/login?error=oidc_no_account")
    elif user.disabled_at is not None:
        return _redirect("/login?error=account_disabled")
    response = _redirect(oidc.safe_next(flow.next))
    await issue_session(db, user, response)
    await db.commit()
    return response


async def _finish_signed_in(
    request: Request, db: AsyncSession, claims: oidc.Claims, flow: oidc.FlowState
) -> RedirectResponse:
    resolved = await resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if resolved is None:
        return _failed(flow.intent, "no live session")
    session, user = resolved
    if flow.uid is None or str(user.id) != flow.uid:
        return _failed(flow.intent, "session user differs from the flow's uid")

    if flow.intent == "link":
        if not await oidc.link_to_user(db, user.id, claims):
            return _failed("link", "identity bound elsewhere or account already linked")
        await db.commit()
        return _redirect("/settings?oidc=linked")

    try:
        grant_id = uuid.UUID(flow.grant_id or "")
    except ValueError:
        return _failed("reauth", "no grant in the state")
    ok = await verify_grant(
        db, grant_id=grant_id, user=user, session_id=session.id,
        issuer=claims.issuer, subject=claims.subject, auth_time=claims.auth_time,
    )
    if not ok:
        await db.rollback()
        return _failed("reauth", "grant not verifiable (identity, session, auth_time or expiry)")
    grant = await db.get(ReauthGrant, grant_id)
    action = grant.action if grant is not None else ""
    await db.commit()
    return _redirect("/settings?reauth=" + quote(action, safe="."))
