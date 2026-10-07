# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The signed-in person's own credentials and account (ADR-091 cl. 17, 23, 25; F1).

Routes (all ``require_session_user`` — an ``api`` bearer is refused here):
``GET/POST /api/me/tokens`` · ``DELETE /api/me/tokens/{token_id}`` ·
``DELETE /api/me/account`` · ``POST /api/me/oidc/link`` · ``POST /api/me/reauth/start`` (1d).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from applire.schemas.auth import PASSWORD_MAX_LENGTH

#: A person creates ``agent`` and ``api`` tokens; ``probe`` tokens are admin-only
#: (``schemas/admin.py``).
PersonalTokenScope = Literal["agent", "api"]
ReauthAction = Literal["account.delete", "oidc.unlink"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenCreateRequest(_Strict):
    name: str = Field(min_length=1, max_length=80)
    scope: PersonalTokenScope


class TokenItem(BaseModel):
    """A token as listed — never the secret. Revoked tokens are not listed."""

    id: uuid.UUID
    name: str
    scope: PersonalTokenScope
    #: The 8 characters after ``apl_`` — lets a person match a token to a client config.
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None


class TokenCreatedResponse(TokenItem):
    """Returned once by ``POST /api/me/tokens`` (201): the only time ``token`` is shown."""

    #: ``apl_<prefix>_<43 b64url>``.
    token: str


class TokenListResponse(BaseModel):
    tokens: list[TokenItem]


class AccountDeleteRequest(_Strict):
    """``DELETE /api/me/account`` (RD-3). Re-auth: the password, or — for an
    account without one — a consumed ``reauth_grants`` row (send no password;
    without a verified grant the answer is 403 ``reauth_required``)."""

    password: str | None = Field(default=None, max_length=PASSWORD_MAX_LENGTH)


class AuthorizeRedirectResponse(BaseModel):
    """``POST /api/me/oidc/link`` and ``POST /api/me/reauth/start`` (200): the state
    cookie is set on this response; the browser then navigates to ``authorize_url``."""

    authorize_url: str


class ReauthStartRequest(_Strict):
    action: ReauthAction
    target_id: uuid.UUID
