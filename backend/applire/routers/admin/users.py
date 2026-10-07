# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/api/admin/users`` — people, roles, invites, reset links, token revoke, delete
(ADR-091 cl. 7, 22, 25–27; S-4, S-6, S-7, S-11; W0B-1).

**Metadata only (S-4, cl. 27).** This router imports identity models and
``services/admin/*`` (whose metadata SQL is column-allowlisted) — never a content
model; ``tests/unit/test_admin_metadata_boundary.py`` checks the imports and that
no response field can carry content. Every action writes an audit row (no IP).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_admin, require_admin_session
from applire.db.session import get_db
from applire.models.user import User
from applire.schemas.admin import (
    AdminUserCreatedResponse,
    AdminUserCreateRequest,
    AdminUserItem,
    AdminUserListResponse,
    AdminUserMetadata,
    AdminUserPatchRequest,
    IssuedLink,
    RevokeTokensResponse,
)
from applire.services import mail
from applire.services.admin import metadata as admin_metadata
from applire.services.admin import users as accounts
from applire.services.admin.links import (
    build_link_url,
    mail_origin,
    newest_open_invite_expiry,
    request_origin,
)

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


def _item(user: User, meta: admin_metadata.UserMetadata, invite_expires_at=None) -> AdminUserItem:
    status_ = accounts.user_status(user)
    return AdminUserItem(
        id=user.id,
        email=user.email,
        role=user.role,
        status=status_,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        last_active_at=user.last_active_at,
        invite_expires_at=invite_expires_at if status_ == "pending" else None,
        metadata=AdminUserMetadata(
            application_count=meta.application_count,
            document_count=meta.document_count,
            storage_bytes=meta.storage_bytes,
            ai_tokens_30d=meta.ai_tokens_30d,
        ),
    )


async def _one_item(db: AsyncSession, user: User) -> AdminUserItem:
    meta = (await admin_metadata.collect(db, [user.id]))[user.id]
    invites = await newest_open_invite_expiry(db, [user.id])
    return _item(user, meta, invites.get(user.id))


async def _deliver(
    db: AsyncSession, request: Request, admin: User, issued: accounts.IssuedLinkResult, *, send: bool
) -> IssuedLink:
    # Shown to the signed-in admin: may use that admin's browser origin (MD-32).
    url = build_link_url(request_origin(request, signed_in=True), issued.purpose, issued.raw_token)
    mailed = mail_failed = False
    reason = None
    if send:
        # Mailed: ONLY from APPLIRE_BASE_URL — never a request header (MD-32).
        origin = mail_origin()
        if origin is None:
            mail_failed, reason = True, "base_url_unset"
        else:
            lang = (await accounts.ui_language_of(db, issued.user.id)
                    or await accounts.ui_language_of(db, admin.id))
            mailed = await mail.send_link_mail(
                issued.purpose, to=issued.user.email, lang=lang,
                link=build_link_url(origin, issued.purpose, issued.raw_token),
                expires_at=issued.expires_at, instance_url=origin,
            )
            mail_failed = not mailed
            reason = None if mailed else "send_failed"
    return IssuedLink(purpose=issued.purpose, url=url, expires_at=issued.expires_at,
                      mailed=mailed, mail_failed=mail_failed, mail_failed_reason=reason)


@router.get("", response_model=AdminUserListResponse)
async def list_users(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserListResponse:
    users = await accounts.list_users(db)
    ids = [u.id for u in users]
    metas = await admin_metadata.collect(db, ids)
    invites = await newest_open_invite_expiry(db, ids)
    return AdminUserListResponse(users=[_item(u, metas[u.id], invites.get(u.id)) for u in users])


@router.post("", response_model=AdminUserCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminUserCreateRequest,
    request: Request,
    admin: User = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> AdminUserCreatedResponse:
    send = mail.smtp_enabled() and body.send_mail
    try:
        issued = await accounts.create_user(db, actor=admin, email=body.email, role=body.role,
                                            mailed=send and mail_origin() is not None)
    except accounts.AccountError as exc:
        raise exc.to_http() from exc
    link = await _deliver(db, request, admin, issued, send=send)
    return AdminUserCreatedResponse(user=await _one_item(db, issued.user), link=link)


@router.patch("/{user_id}", response_model=AdminUserItem)
async def patch_user(
    user_id: uuid.UUID,
    body: AdminUserPatchRequest,
    admin: User = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> AdminUserItem:
    try:
        user = await accounts.patch_user(db, actor=admin, user_id=user_id,
                                         role=body.role, disabled=body.disabled)
    except accounts.AccountError as exc:
        raise exc.to_http() from exc
    return await _one_item(db, user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        await accounts.delete_account(db, actor=admin, user_id=user_id, by="admin")
    except accounts.AccountError as exc:
        raise exc.to_http() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{user_id}/reinvite", response_model=IssuedLink)
async def reinvite(
    user_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> IssuedLink:
    send = mail.smtp_enabled()
    try:
        issued = await accounts.reissue_invite(db, actor=admin, user_id=user_id,
                                                mailed=send and mail_origin() is not None)
    except accounts.AccountError as exc:
        raise exc.to_http() from exc
    return await _deliver(db, request, admin, issued, send=send)


@router.post("/{user_id}/reset-link", response_model=IssuedLink)
async def reset_link(
    user_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> IssuedLink:
    # The admin hands a reset link over in person (S-7: the admin never sees a
    # password); it is not mailed — the reset dialog copy has no mailed variant.
    try:
        issued = await accounts.issue_reset_link(db, actor=admin, user_id=user_id,
                                                 via="admin", mailed=False)
    except accounts.AccountError as exc:
        raise exc.to_http() from exc
    return await _deliver(db, request, admin, issued, send=False)


@router.post("/{user_id}/revoke-tokens", response_model=RevokeTokensResponse)
async def revoke_tokens(
    user_id: uuid.UUID,
    admin: User = Depends(require_admin_session),
    db: AsyncSession = Depends(get_db),
) -> RevokeTokensResponse:
    try:
        count = await accounts.revoke_tokens(db, actor=admin, user_id=user_id)
    except accounts.AccountError as exc:
        raise exc.to_http() from exc
    return RevokeTokensResponse(revoked=count)
