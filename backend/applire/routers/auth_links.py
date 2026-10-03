# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Forgot password, link inspect and link redeem (ADR-091 cl. 22–23; US325).

All three are on the closed unauthenticated allowlist (cl. 20). The link token
arrives only in the POST body (the page read it from its URL fragment) — never in
a path or query — and every response carries ``Referrer-Policy: no-referrer``.

* ``POST /api/auth/forgot`` answers **202 with no body, always**. The lookup, the
  per-account throttle (3 reset links / hour) and the mail run in a background
  task with their own DB session, so a known and an unknown email take the same
  time and give the same answer. SMTP off → nothing happens at all.
* ``POST /api/auth/links/inspect`` → ``{purpose, email, state}`` for the set-password
  page; 404 ``link_invalid`` for an unknown token (no state to show).
* ``POST /api/auth/links/redeem`` sets the password and signs the person in (204 +
  cookie). The password policy is checked **before** the link is consumed (a typo
  must not burn the link); consumption is one atomic ``UPDATE … RETURNING``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.db.session import AsyncSessionLocal, get_db
from applire.models.user import User
from applire.schemas.auth import (
    ForgotRequest,
    LinkInspectRequest,
    LinkInspectResponse,
    LinkRedeemRequest,
)
from applire.services import audit, mail
from applire.services.admin import identity_seams as seams
from applire.services.admin import links
from applire.services.admin import users as accounts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: Public unsafe routes take 1a's origin check (ADR-091 cl. 12; inventory-tested).
require_origin = seams.require_origin_dependency()

FORGOT_LIMIT_PER_HOUR = 3
NO_REFERRER = {"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}


def _err(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code,
                         detail={"error_code": code, "message": message}, headers=NO_REFERRER)


LINK_INVALID = (404, "link_invalid", "This link is not valid.")
LINK_USED = (409, "link_used", "This link was already used or replaced by a newer one.")
LINK_EXPIRED = (410, "link_expired", "This link has expired.")


# --- forgot ---------------------------------------------------------------------

async def _send_forgot_mail(email: str, origin: str, session_factory=None) -> None:
    """Background: look up, throttle, issue, mail. Never raises, never logs the email."""
    factory = session_factory or AsyncSessionLocal
    try:
        async with factory() as db:
            user = await accounts.find_by_email(db, email)
            if user is None or user.password_hash is None or accounts.user_status(user) != "active":
                return
            if await links.recent_reset_count(db, user.id, timedelta(hours=1)) >= FORGOT_LIMIT_PER_HOUR:
                logger.info("forgot-password: per-account limit reached; no mail sent")
                return
            issued = await accounts.issue_reset_link(db, actor=None, user_id=user.id,
                                                     via="forgot", mailed=True)
            lang = await accounts.ui_language_of(db, user.id)
            await mail.send_link_mail(
                "reset", to=user.email, lang=lang,
                link=links.build_link_url(origin, "reset", issued.raw_token),
                expires_at=issued.expires_at, instance_url=origin,
            )
    except Exception as exc:  # noqa: BLE001 — a background task must not crash silently-loud
        logger.error("forgot-password background task failed: %s", type(exc).__name__)


@router.post("/forgot", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_origin)])
async def forgot(body: ForgotRequest, request: Request, background: BackgroundTasks) -> Response:
    if mail.smtp_enabled():
        background.add_task(_send_forgot_mail, body.email, links.request_origin(request))
    return Response(status_code=status.HTTP_202_ACCEPTED, headers=NO_REFERRER)


# --- inspect --------------------------------------------------------------------

@router.post("/links/inspect", response_model=LinkInspectResponse,
             dependencies=[Depends(require_origin)])
async def inspect(body: LinkInspectRequest, response: Response,
                  db: AsyncSession = Depends(get_db)) -> LinkInspectResponse:
    found = await links.inspect_link(db, body.token)
    if found is None:
        raise _err(*LINK_INVALID)
    response.headers.update(NO_REFERRER)
    return LinkInspectResponse(purpose=found.link.purpose, email=found.user.email, state=found.state)


# --- redeem ---------------------------------------------------------------------

@router.post("/links/redeem", status_code=status.HTTP_204_NO_CONTENT,
             dependencies=[Depends(require_origin)])
async def redeem(body: LinkRedeemRequest, db: AsyncSession = Depends(get_db)) -> Response:
    found = await links.inspect_link(db, body.token)
    if found is None:
        raise _err(*LINK_INVALID)
    if found.state == "used":
        raise _err(*LINK_USED)
    if found.state == "expired":
        raise _err(*LINK_EXPIRED)
    try:
        seams.check_password_policy(body.password, found.user.email)
    except ValueError as exc:
        raise _err(422, "password_policy",
                   "Use 12 to 256 characters, and not your email address.") from exc

    password_hash = await seams.hash_password(body.password)

    consumed = await links.consume_link(db, body.token)
    if consumed is None:  # lost the race, or expired between inspect and now
        await db.rollback()
        again = await links.inspect_link(db, body.token)
        if again is not None and again.state == "expired":
            raise _err(*LINK_EXPIRED)
        raise _err(*LINK_USED)
    link_id, user_id, purpose = consumed

    user = await db.get(User, user_id, populate_existing=True)
    if user is None or user.deleted_at is not None or user.disabled_at is not None:
        await db.rollback()
        raise _err(*LINK_INVALID)

    await seams.revoke_user_sessions(db, user.id)  # "signed out on your other devices"
    await links.revoke_open_links(db, user.id)  # an invite and a reset never both stay live
    user.password_hash = password_hash
    if purpose == "invite":
        await audit.record(db, actor_id=user.id, action="invite.redeemed", target_type="user",
                           target_id=user.id, details={"link_id": link_id})
    else:
        await audit.record(db, actor_id=user.id, action="password.reset", target_type="user",
                           target_id=user.id, details={"via": "link", "link_id": link_id})

    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_REFERRER)
    user.last_login_at = datetime.now(timezone.utc)
    await seams.issue_session(db, user, response)
    await db.commit()
    return response


__all__ = ["router"]
