# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The person's side of the app colour schemes (ADR-023 amended 2026-10-11, US357).

``GET /api/color-schemes`` — the schemes the admin offers, for the Settings picker.
``GET /api/color-schemes/effective`` — the scheme that paints this person's UI.

Both are ``require_user``. The public ``GET /api/admin/color-schemes/active``
(ADR-091 cl. 20 allowlist) is untouched and keeps answering the instance default
for the login page; it never reads a session (SF-THEME.4). Choosing a scheme is a
``PATCH /api/settings`` field. Not on the MCP surface (ADR-081 cl. 8 precedent).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_user
from applire.db.session import get_db
from applire.models.user import User
from applire.schemas.color_scheme import (
    ActiveSchemeResponse,
    OfferedSchemeResponse,
    SchemeChoiceResponse,
)
from applire.services.color_schemes import (
    list_offered_schemes,
    resolve_choice,
    resolve_effective_scheme,
)

router = APIRouter(prefix="/api/color-schemes", tags=["settings"])


@router.get("", response_model=SchemeChoiceResponse)
async def list_choices(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(require_user)
) -> SchemeChoiceResponse:
    default, choice = await resolve_choice(db, getattr(current_user, "id", None))
    default_id = default.id if default else None
    return SchemeChoiceResponse(
        default_id=default_id,
        selected_id=choice.id if choice else None,
        schemes=[
            OfferedSchemeResponse(
                id=s.id,
                name=s.name,
                seed_primary=s.seed_primary,
                seed_accent=s.seed_accent,
                seed_secondary=s.seed_secondary,
                derived=s.derived,
                is_default=s.id == default_id,
            )
            for s in await list_offered_schemes(db)
        ],
    )


@router.get("/effective", response_model=ActiveSchemeResponse)
async def effective(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(require_user)
) -> ActiveSchemeResponse:
    scheme = await resolve_effective_scheme(db, getattr(current_user, "id", None))
    if scheme is None:
        raise HTTPException(status_code=404, detail="No color scheme found")
    return ActiveSchemeResponse(id=scheme.id, name=scheme.name, derived=scheme.derived)
