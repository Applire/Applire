# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/api/admin/probe-tokens`` — read-only probe tokens for uptime monitors (US329).

A probe token (``personal_tokens.scope='probe'``) is accepted on
``GET /api/ops/health`` only (ADR-086 amended 2026-10-03 cl. 2, ADR-091 cl. 17).
Admins create, list and revoke them; the token belongs to the admin who created
it and stops working when that person stops being an active admin. The list
shows every live probe token of the instance (they are instance plumbing, not a
person's credential). The secret is returned once. Audited (S-11).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_admin_session
from applire.auth.tokens import (
    audit_token_event,
    create_token,
    list_tokens,
    revoke_token,
)
from applire.db.session import get_db
from applire.models.user import User
from applire.schemas.admin import (
    ProbeTokenCreatedResponse,
    ProbeTokenCreateRequest,
    ProbeTokenItem,
    ProbeTokenListResponse,
)

router = APIRouter(prefix="/api/admin/probe-tokens", tags=["admin"])

_PROBE = ("probe",)


def _item(row) -> ProbeTokenItem:
    return ProbeTokenItem(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


@router.get("", response_model=ProbeTokenListResponse)
async def get_probe_tokens(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin_session),
) -> ProbeTokenListResponse:
    rows = await list_tokens(db, user_id=None, scopes=_PROBE)
    return ProbeTokenListResponse(tokens=[_item(r) for r in rows])


@router.post("", response_model=ProbeTokenCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_probe_token(
    body: ProbeTokenCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin_session),
) -> ProbeTokenCreatedResponse:
    row, raw = await create_token(
        db, user_id=admin.id, scope="probe", name=body.name.strip() or body.name
    )
    await audit_token_event(
        db,
        actor_id=admin.id,
        action="token.created",
        owner_id=admin.id,
        token_id=row.id,
        scope="probe",
    )
    await db.commit()
    return ProbeTokenCreatedResponse(**_item(row).model_dump(), token=raw)


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_probe_token(
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin_session),
) -> Response:
    row = await revoke_token(db, token_id=token_id, scopes=_PROBE, user_id=None)
    if row is None:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="token not found")
    await audit_token_event(
        db,
        actor_id=admin.id,
        action="token.revoked",
        owner_id=row.user_id,
        token_id=row.id,
        scope="probe",
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
