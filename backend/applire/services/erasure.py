# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Per-user erasure (ADR-092 cl. 11, RD-3 / D-3, US336) — the one implementation.

Strawberry W0 (frozen interface F9): ``erase(db, user_id, scope)`` is the
contract both doors call (``DELETE /api/profile`` → ``"vault"``,
``DELETE /api/me/account`` → ``"account"``, W1 package 1b). The body is a
**delegation** to today's router code path (``routers.profile.erase_profile``)
without moving any router code — zero behaviour change, including today's
single-user semantics (all profiles deleted, ownerless uploads swept, user row
kept). Package 3b (W2) moves the cascade here, makes it owner-keyed, adds the
lock-then-refcount posting purge and turns the router into the adapter.

``scope="account"`` (tombstone the user row with a scrubbed email) does not
exist today, so it raises ``NotImplementedError`` until 3b fills it.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["ErasureFailed", "ErasureScope", "erase"]

ErasureScope = Literal["vault", "account"]


class ErasureFailed(Exception):
    """The erasure transaction rolled back — nothing was deleted."""


class _FixedUserAuth:
    """Resolves the given user id for the delegated router call (W0 only)."""

    def __init__(self, user_id: uuid.UUID) -> None:
        self._user_id = user_id

    async def get_current_user(self, request=None):  # noqa: ARG002 — router signature
        return _UserRef(self._user_id)


class _UserRef:
    def __init__(self, user_id: uuid.UUID) -> None:
        self.id = user_id


async def erase(db: AsyncSession, user_id: uuid.UUID, scope: ErasureScope) -> dict[str, int]:
    """Erase ``user_id``'s data for ``scope``; return per-table deleted-row counts.

    Raises :class:`ErasureFailed` when the transaction rolled back and
    ``NotImplementedError`` for ``scope="account"`` (W0).
    """
    if scope == "account":
        raise NotImplementedError(
            "erase(scope='account') is filled by Strawberry package 3b (ADR-092 cl. 11)"
        )
    if scope != "vault":
        raise ValueError(f"unknown erasure scope: {scope!r}")

    # Lazy import: W0 delegates to the router's handler body (no router edit).
    from applire.routers import profile as profile_router

    handler = profile_router.erase_profile
    params = inspect.signature(handler).parameters
    kwargs: dict = {"db": db}
    if "request" in params:
        kwargs["request"] = None
    if "storage" in params:
        kwargs["storage"] = profile_router._get_storage()
    if "auth" in params:
        kwargs["auth"] = _FixedUserAuth(user_id)
    if "user" in params:  # after W0-A1's dependency swap (F2)
        kwargs["user"] = _UserRef(user_id)
    try:
        result = await handler(**kwargs)
    except HTTPException as exc:
        raise ErasureFailed(str(exc.detail)) from exc
    return dict(result["records_deleted"])
