# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Package 1b test support: an app with the 1b routers, people in each account state.

The 1b routers are mounted on their own ``FastAPI`` here — ``main.py`` (1a) includes
them at integration. Authentication goes through the real ``auth.deps``
dependencies with ``get_auth_provider`` overridden (the ADR-008 override point),
so ``require_admin``'s role check and ``require_session_user`` run for real.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from applire.auth import get_auth_provider
from applire.db.session import get_db
from applire.models.user import User
from applire.routers import auth_links, me_account
from applire.routers.admin import users as admin_users


class ActingAs:
    """Provider double: ``get_current_user(request, db)`` returns ``self.user_id``'s row."""

    def __init__(self) -> None:
        self.user_id: uuid.UUID | None = None

    async def get_current_user(self, request, db):  # noqa: ARG002
        if self.user_id is None:
            return None
        user = await db.get(User, self.user_id)
        if user is None or user.deleted_at is not None or user.disabled_at is not None:
            return None
        return user


def build_app(db) -> tuple[FastAPI, ActingAs]:
    app = FastAPI()
    app.include_router(admin_users.router)
    app.include_router(auth_links.router)
    app.include_router(me_account.router)
    acting = ActingAs()

    async def _db():
        yield db

    async def _provider():
        return acting

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _provider
    return app, acting


def client_for(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://applire.test")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def add_user(
    db,
    *,
    email: str | None = None,
    role: str = "user",
    state: str = "active",
    password_hash: str | None = "scrypt$15$8$1$placeholder$placeholder",
) -> User:
    """``state``: ``active`` (has a credential), ``pending`` (none, never signed in),
    ``disabled``."""
    uid = uuid.uuid4()
    user = User(id=uid, email=email or f"p-{uid.hex[:8]}@example.org", role=role)
    if state in ("active", "disabled"):
        user.password_hash = password_hash
        user.last_login_at = _now()
    if state == "disabled":
        user.disabled_at = _now()
    db.add(user)
    await db.commit()
    return user


@asynccontextmanager
async def same_session_factory(db):
    """A session factory stand-in that hands out the test's own session (the
    in-memory SQLite DB lives on that one connection) and never closes it."""
    yield db
