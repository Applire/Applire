# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Personal-token resolvers (ADR-091 cl. 17; frozen interface F3 — filled by 1c).

Token format ``apl_<prefix8>_<43 b64url>``; ``personal_tokens`` stores the sha256,
lookup by prefix then ``hmac.compare_digest``. Scopes: ``agent`` (stdio MCP door
only), ``api`` (REST bearer with the owner's role), ``probe`` (``GET
/api/ops/health`` only). Validity — not revoked, user not disabled/deleted — is
re-checked on every call (MD-3).
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.user import User

TokenScope = Literal["agent", "api", "probe"]
TOKEN_SCOPES: tuple[TokenScope, ...] = ("agent", "api", "probe")
TOKEN_PREFIX = "apl_"


class InvalidToken(Exception):
    """Missing, malformed, unknown, revoked, wrong-scope, or owner inactive.

    One exception for every cause — the caller never learns which (no oracle).
    """


async def resolve_agent_token(db: AsyncSession, raw: str) -> User:
    """The live user owning the ``agent``-scope token ``raw``, else ``InvalidToken``.

    Used by ``mcp/__main__.py`` at start (exit 1 on failure) and per tool call
    (``mcp/identity.py``, MD-3).
    """
    raise NotImplementedError("auth.tokens.resolve_agent_token — filled by package 1c")


async def resolve_bearer(db: AsyncSession, raw: str, scope: TokenScope) -> User | None:
    """The live user owning ``raw`` if it is a valid token **of ``scope``**, else ``None``.

    ``scope`` is ``"api"`` for REST bearer auth and ``"probe"`` for the ops route.
    Updates ``last_used_at`` at most once per minute.
    """
    raise NotImplementedError("auth.tokens.resolve_bearer — filled by package 1c")
