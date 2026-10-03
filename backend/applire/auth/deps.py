# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The five auth dependencies every route takes (ADR-091 cl. 4; frozen interface F2).

Routes never take the provider. They depend on exactly one of:

==========================  =======================================================
``require_user``            any signed-in user (session or ``api`` bearer) — 401
``require_admin``           role ``admin`` — 401 if anonymous, 403 otherwise
``require_session_user``    credential management — a **session** only (an ``api``
                            bearer is refused, ADR-091 cl. 17) — 401
``user_or_signed_link``     the document GETs — session, ``api`` bearer, or a valid
                            signed link (cl. 18; filled by 1c in ``deps_links.py``)
``admin_or_probe``          ``GET /api/ops/health`` — admin, or a ``probe`` token
==========================  =======================================================

Each one **raises itself** and **sets the ADR-092 owner context** to the resolved
user, and each is ``async def``: a sync dependency runs in a threadpool copy of
the context, so the owner it set would never reach the endpoint (adversarial
re-check g2). ``tests/unit/test_auth_deps_async.py`` asserts
``iscoroutinefunction`` over the whole dependant tree.

**W0 (Strawberry build 1) — harness-backed, behaviour-neutral.** All five resolve
the user through ``get_auth_provider`` (the override point, ADR-008) exactly as
the routers did before the swap, so the app on the NoAuth stub behaves as today.
Package 1a fills session/bearer resolution, the role check and the harness
fences; 1c fills the signed-link branch and the probe-token branch.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
from applire.db.session import get_db
from applire.models.user import User
from applire.ownership import set_owner

#: Error ``detail`` objects (contract: docs/dev/api-contract-strawberry.md §Errors;
#: shape = ``schemas.auth.ErrorDetail``, the codebase's ``error_code``/``message`` form).
UNAUTHENTICATED = {"error_code": "unauthenticated", "message": "Sign in to continue."}
FORBIDDEN = {"error_code": "forbidden", "message": "This needs an administrator."}


def _unauthenticated() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=UNAUTHENTICATED)


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=FORBIDDEN)


async def _resolve(request: Request, provider: AuthProvider, db: AsyncSession) -> User:
    user = await provider.get_current_user(request, db)
    if user is None:
        raise _unauthenticated()
    set_owner(user.id)
    return user


def _is_admin(user: User) -> bool:
    # W0: ``users.role`` arrives with migration 0071 (package 1a). Until then the
    # only identity is the NoAuth stub, which the harness treats as admin
    # (ADR-091 cl. 3). 1a replaces this with ``user.role == "admin"``.
    role = getattr(user, "role", None)
    return role is None or role == "admin"


async def require_user(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Any authenticated user; 401 ``unauthenticated`` otherwise."""
    return await _resolve(request, provider, db)


async def require_admin(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Role ``admin``; 401 if anonymous, 403 ``forbidden`` for a non-admin."""
    user = await _resolve(request, provider, db)
    if not _is_admin(user):
        raise _forbidden()
    return user


async def require_session_user(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticated by a **session** (credential-management routes, cl. 17).

    W0: identical to ``require_user``; 1a refuses bearer-authenticated requests (401).
    """
    return await _resolve(request, provider, db)


async def user_or_signed_link(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Document GETs: session, ``api`` bearer, or a valid signed link (cl. 16/18).

    W0: identical to ``require_user``. 1c adds the link branch in
    ``auth/deps_links.py`` (``?exp=&sig=`` → ``auth.links.verify_document_link``;
    past or non-numeric ``exp`` → 410 ``link_expired``).
    """
    return await _resolve(request, provider, db)


async def admin_or_probe(
    request: Request,
    provider: AuthProvider = Depends(get_auth_provider),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """``GET /api/ops/health``: an admin, or a ``probe``-scope bearer (S-16).

    Returns the admin ``User``, or ``None`` when a probe token authenticated
    (a probe token has an owner row but acts for no user's data). W0: admin
    only, via the provider; 1c adds the probe branch.
    """
    return await require_admin(request, provider, db)


#: The five, for the route-auth inventory (US319) and the async test.
AUTH_DEPENDENCIES = (
    require_user,
    require_admin,
    require_session_user,
    user_or_signed_link,
    admin_or_probe,
)
