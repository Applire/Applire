# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/api/me/tokens`` — a person's own agent and API tokens (ADR-091 cl. 17; US327, US337).

Session only (``require_session_user``): an ``api`` bearer cannot mint or revoke
tokens — a leaked API token must not be able to make itself permanent
(ADR-091 cl. 17, contract §3.5). The secret is returned exactly once, by the
``POST``; listings carry the 8-character prefix so a person can match a token to
the client config it lives in. A foreign or missing id is one 404 (S-10).
Every create/revoke writes an audit row (S-11) in the same transaction.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_session_user
from applire.auth.tokens import (
    audit_token_event,
    create_token,
    list_tokens,
    revoke_token,
)
from applire.db.session import get_db
from applire.models.user import User
from applire.schemas.me import (
    TokenCreatedResponse,
    TokenCreateRequest,
    TokenItem,
    TokenListResponse,
)

router = APIRouter(prefix="/api/me/tokens", tags=["me"])

#: A person manages these scopes; ``probe`` is the admin's (``/api/admin/probe-tokens``).
PERSONAL_SCOPES = ("agent", "api")


def _item(row) -> TokenItem:
    return TokenItem(
        id=row.id,
        name=row.name,
        scope=row.scope,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


@router.get("", response_model=TokenListResponse)
async def get_my_tokens(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_session_user),
) -> TokenListResponse:
    rows = await list_tokens(db, user_id=current_user.id, scopes=PERSONAL_SCOPES)
    return TokenListResponse(tokens=[_item(r) for r in rows])


@router.post("", response_model=TokenCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_my_token(
    body: TokenCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_session_user),
) -> TokenCreatedResponse:
    row, raw = await create_token(
        db, user_id=current_user.id, scope=body.scope, name=body.name.strip() or body.name
    )
    await audit_token_event(
        db,
        actor_id=current_user.id,
        action="token.created",
        owner_id=current_user.id,
        token_id=row.id,
        scope=row.scope,
    )
    await db.commit()
    return TokenCreatedResponse(**_item(row).model_dump(), token=raw)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_my_token(
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_session_user),
) -> Response:
    row = await revoke_token(
        db, token_id=token_id, scopes=PERSONAL_SCOPES, user_id=current_user.id
    )
    if row is None:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token not found")
    await audit_token_event(
        db,
        actor_id=current_user.id,
        action="token.revoked",
        owner_id=current_user.id,
        token_id=row.id,
        scope=row.scope,
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
