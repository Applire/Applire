# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in routes (ADR-091 cl. 5, 11–13; contract §3.2): state, login, logout, me, password.

``POST /api/auth/login`` is public, origin-checked and throttled (RD-8): every
attempt on a hot ``(email, client)`` key is delayed, never refused; unknown
emails and credential-less accounts hash against a dummy so body and timing
match a wrong password (SF-IAM.6). A correct password on a disabled account is
the only path to 403 ``account_disabled`` (founder ruling W0B-3).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import _seams, provider_name
from applire.auth.csrf import require_origin
from applire.auth.deps import require_session_user, require_user
from applire.auth.passwords import (
    check_password_policy,
    hash_password,
    needs_rehash,
    verify_password,
)
from applire.auth.roles import effective_role
from applire.auth.sessions import (
    SESSION_COOKIE,
    clear_session_cookie,
    issue_session,
    resolve_session,
    revoke_session,
    revoke_user_sessions,
)
from applire.auth.setup import setup_required
from applire.auth.throttle import login_key, login_throttle
from applire.config import settings
from applire.db.session import get_db
from applire.models.user import User
from applire.models.user_settings import UserSettings
from applire.schemas.auth import (
    AuthStateResponse,
    LoginRequest,
    MeResponse,
    PasswordChangeRequest,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

INVALID_CREDENTIALS_MESSAGE = "Email address or password is incorrect."


#: CONTRACT-CHANGE 1a (accepted 2026-10-03): a delayed failed login says so in a
#: header, identically for known and unknown emails (RD-8 — no enumeration).
THROTTLED_HEADER = "X-Applire-Throttled"


def auth_error(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"error_code": code, "message": message}, headers=headers
    )


def _harness_on() -> bool:
    try:
        return provider_name() == "harness"
    except ValueError:
        return False


@router.get("/state", response_model=AuthStateResponse)
async def auth_state(db: AsyncSession = Depends(get_db)) -> AuthStateResponse:
    """What the sign-in and setup pages need before anyone is signed in (public)."""
    return AuthStateResponse(
        setup_required=await setup_required(db),
        oidc_enabled=bool(settings.oidc_issuer.strip()),
        oidc_button_label=settings.oidc_button_label,
        smtp_enabled=bool(settings.smtp_host.strip()),
        harness=_harness_on(),
    )


async def find_user_by_email(db: AsyncSession, email: str) -> User | None:
    """Case-insensitive lookup in SQL with the index's own expression (cl. 6, 9)."""
    return (
        await db.execute(
            select(User).where(
                func.lower(User.email) == func.lower(email.strip()),
                User.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


@router.post(
    "/login",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    dependencies=[Depends(require_origin)],
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> None:
    key = login_key(body.email, request)
    delayed = await login_throttle.wait(key)
    user = await find_user_by_email(db, body.email)
    ok = await verify_password(body.password, user.password_hash if user else None)
    if not ok or user is None:
        login_throttle.record_failure(key)
        raise auth_error(
            401,
            "invalid_credentials",
            INVALID_CREDENTIALS_MESSAGE,
            headers={THROTTLED_HEADER: "1"} if delayed > 0 else None,
        )
    login_throttle.record_success(key)
    if user.disabled_at is not None:
        raise auth_error(
            403,
            "account_disabled",
            "This account is disabled. Contact the person who runs this Applire instance.",
        )
    if needs_rehash(user.password_hash or ""):
        user.password_hash = await hash_password(body.password)
    # A presented cookie is never adopted (session fixation): retire it, mint fresh.
    presented = await resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if presented is not None:
        await revoke_session(db, presented[0].id)
    await issue_session(db, user, response)
    await db.commit()


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None)
async def logout(
    request: Request,
    response: Response,
    _user: User = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    session_id = getattr(request.state, "auth_session_id", None)
    if session_id is not None:
        await revoke_session(db, session_id)
        await db.commit()
    clear_session_cookie(response)


@router.get("/me", response_model=MeResponse)
async def me(
    request: Request,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> MeResponse:
    ui_language = (
        await db.execute(select(UserSettings.ui_language).where(UserSettings.user_id == user.id))
    ).scalar_one_or_none()
    return MeResponse(
        id=user.id,
        email=user.email,
        role=effective_role(user, request),
        has_password=user.password_hash is not None,
        oidc_linked=user.oidc_subject is not None,
        ui_language=ui_language,
    )


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, response_model=None)
async def change_password(
    body: PasswordChangeRequest,
    request: Request,
    user: User = Depends(require_session_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Self-service change (session only). Other sessions are revoked; this one stays."""
    if not await verify_password(body.current, user.password_hash):
        # 403, not 401: the shell's 401 handler must not treat a typo as a lost session.
        raise auth_error(403, "invalid_credentials", "The current password is incorrect.")
    try:
        check_password_policy(body.new, user.email)
    except ValueError as exc:
        raise auth_error(422, "password_policy", str(exc)) from exc
    user.password_hash = await hash_password(body.new)
    await revoke_user_sessions(
        db, user.id, except_session_id=getattr(request.state, "auth_session_id", None)
    )
    await _seams.audit(
        db,
        actor_id=user.id,
        action="password.changed",
        target_type="user",
        target_id=user.id,
        details={},
    )
    await db.commit()
