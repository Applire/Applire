# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The built-in provider: sessions and ``api`` bearers (ADR-091 cl. 2, 11, 12, 17).

Credentials are resolved in one fixed order (cl. 12): **if an ``Authorization``
header is present, only the bearer is evaluated and the cookie is never read** —
an invalid or wrong-scope bearer resolves to ``None`` (401), with no fallback.
Otherwise the ``applire_session`` cookie is resolved server-side.

Generic OIDC sign-in (cl. 6) produces an ordinary session; its routes are
package 1d's. Disabled and tombstoned users never resolve (cl. 8).
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import _seams
from applire.auth.base import AuthProvider
from applire.auth.sessions import SESSION_COOKIE, resolve_session, user_is_live
from applire.models.user import User


class LocalAuthProvider(AuthProvider):
    """Email + password sessions, and personal ``api`` tokens on the REST API."""

    is_harness = False

    async def get_current_user(self, request: Request, db: AsyncSession) -> User | None:
        if "authorization" in request.headers:
            user = await _seams.bearer_user(request, db, scope="api")
            if not user_is_live(user):
                return None
            request.state.auth_via = "bearer"
            return user
        resolved = await resolve_session(db, request.cookies.get(SESSION_COOKIE))
        if resolved is None:
            return None
        session, user = resolved
        request.state.auth_via = "session"
        request.state.auth_session_id = session.id
        return user
