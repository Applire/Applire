# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The two auth dependencies package 1c fills (ADR-091 cl. 4, 18, 19; frozen F2).

``auth/deps.py`` (package 1a) re-exports both, so ``AUTH_DEPENDENCIES`` holds the
very objects defined here (work-packages-proposal §3: "1c's function lives in
``auth/deps_links.py``"). This module imports ``applire.auth.deps`` lazily inside
the functions — never at import time — so there is no import cycle.

* ``user_or_signed_link`` — the six document GETs. A session or ``api`` bearer
  (resolved by ``deps.require_user``, the single resolution path), or a valid
  signed link ``?exp=&uid=&sig=`` (``auth.links.verify_document_link``). A past or
  non-numeric ``exp`` is **410 ``link_expired``** unless the request is otherwise
  authenticated.
* ``admin_or_probe`` — ``GET /api/ops/health``. A ``probe``-scope bearer of an
  active admin returns ``None`` and sets **no** owner (the ops report runs
  ``unscoped("ops-aggregate")``); everything else goes through
  ``deps.require_admin`` (admin session or an admin's ``api`` bearer; 401/403).

Both are ``async def`` (adversarial re-check g2) and both set the ADR-092 owner
context for a resolved user.

``DocumentResponseHeaders`` is the ASGI middleware that stamps
``Referrer-Policy: no-referrer`` and ``Cache-Control: private, no-store`` on the
document GET responses (cl. 18): the routes return their own ``Response`` objects,
and FastAPI drops headers set on an injected ``Response`` in that case, so a
dependency cannot do it. Registered in ``main.py`` (NEEDS-EDIT 1c → 1a).
"""

from __future__ import annotations

import re
import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.auth.links import DocumentKind, LinkExpired, verify_document_link
from applire.auth.tokens import bearer_from_request, resolve_bearer
from applire.db.session import get_db
from applire.models.user import User
from applire.ownership import set_owner

LINK_EXPIRED = {"error_code": "link_expired", "message": "This link has expired."}

#: path parameter → document kind, for the six document GETs.
_KIND_BY_PATH_PARAM: dict[str, DocumentKind] = {"cv_id": "cv", "cl_id": "cover_letter"}


def _document_of(request: Request) -> tuple[DocumentKind, uuid.UUID] | None:
    for param, kind in _KIND_BY_PATH_PARAM.items():
        raw = request.path_params.get(param)
        if raw is None:
            continue
        try:
            return kind, uuid.UUID(str(raw))
        except ValueError:
            return None
    return None


def _has_link_params(request: Request) -> bool:
    q = request.query_params
    return "sig" in q or "exp" in q or "uid" in q


async def user_or_signed_link(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Document GETs: session, ``api`` bearer, or a valid signed link (cl. 16/18).

    * An ``Authorization`` header → only the bearer counts (``deps.require_user``);
      the link is not consulted (ADR-091 cl. 12: no fallback between credentials).
    * Link parameters present → verified; a valid link authenticates its signer.
      An invalid link (bad MAC, stale ``link_epoch``, inactive user, foreign or
      missing document) is ignored and the request must authenticate otherwise
      (401). An expired link is 410 unless the request is otherwise authenticated.
    """
    from applire.auth import deps

    document = _document_of(request)
    if (
        document is not None
        and _has_link_params(request)
        and bearer_from_request(request) is None
    ):
        kind, doc_id = document
        q = request.query_params
        try:
            user = await verify_document_link(
                kind, doc_id, q.get("exp", ""), q.get("sig", ""), db, q.get("uid")
            )
        except LinkExpired:
            try:
                return await deps.require_user(request, provider, db)
            except HTTPException as exc:
                if exc.status_code != status.HTTP_401_UNAUTHORIZED:
                    raise
            raise HTTPException(status_code=status.HTTP_410_GONE, detail=LINK_EXPIRED)
        if user is not None:
            set_owner(user.id)
            return user
    return await deps.require_user(request, provider, db)


async def admin_or_probe(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """``GET /api/ops/health``: an admin, or a ``probe``-scope bearer (S-16).

    Returns the admin ``User``, or ``None`` when a probe token authenticated
    (a probe token acts for no user's data, so no owner context is set). A probe
    token whose creator is no longer an active admin is refused like any invalid
    bearer (MD-3: validity re-checked per call).
    """
    from applire.auth import deps

    raw = bearer_from_request(request)
    if raw:
        owner = await resolve_bearer(db, raw, "probe")
        if owner is not None and getattr(owner, "role", None) == "admin":
            return None
    return await deps.require_admin(request, provider, db)


# ---------------------------------------------------------------------------
# Response headers on document GETs (cl. 18)
# ---------------------------------------------------------------------------

#: The six document GETs (REST and signed alike).
DOCUMENT_PATH_RE = re.compile(r"^/api/(?:cv|cover-letter)/[^/]+/(?:html|pdf|docx)/?$")
DOCUMENT_RESPONSE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"private, no-store"),
)


class DocumentResponseHeaders:
    """Pure-ASGI middleware: ``Referrer-Policy`` + ``Cache-Control`` on document GETs.

    Pure ASGI (not ``BaseHTTPMiddleware``) so streaming PDF/DOCX bodies and the
    request's context variables pass through untouched. Existing values of the
    two headers are replaced, never duplicated.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not DOCUMENT_PATH_RE.match(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        names = {name for name, _ in DOCUMENT_RESPONSE_HEADERS}

        async def _send(message):
            if message.get("type") == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() not in names
                ]
                headers.extend(DOCUMENT_RESPONSE_HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, _send)
