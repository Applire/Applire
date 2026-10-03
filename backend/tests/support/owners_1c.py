# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test support for package 1c — tokens, signed links, health split.

* ``token_db`` — an in-memory SQLite engine with every table, a session factory,
  and the instance secret installed in ``auth.links`` (reset afterwards).
* ``make_person`` — a user row with ADR-091 identity columns.
* ``make_document`` — a generated CV / cover letter row owned by a person.
* ``StubProvider`` — an ``AuthProvider`` whose ``get_current_user`` returns a fixed
  user (or ``None``); tests override ``get_auth_provider`` with it, never the five
  dependencies (contract §9).
* ``build_app`` — a bare FastAPI app with the given routers and a ``get_db``
  override bound to ``token_db``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Iterable

import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from applire.auth import get_auth_provider
from applire.auth import links as links_module
from applire.db.session import Base, get_db
from applire.models.user import User

TEST_SECRET = "test-instance-secret-1c"


@dataclass
class TokenDB:
    engine: Any
    maker: async_sessionmaker

    def session(self) -> AsyncSession:
        return self.maker()


@pytest_asyncio.fixture
async def token_db():
    import applire.models  # noqa: F401 — register every mapper

    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    links_module.set_instance_secret(TEST_SECRET)
    try:
        yield TokenDB(engine, maker)
    finally:
        links_module.set_instance_secret(None)
        await engine.dispose()


async def make_person(
    db: AsyncSession,
    *,
    role: str = "user",
    email: str | None = None,
    disabled: bool = False,
    deleted: bool = False,
) -> User:
    from datetime import datetime, timezone

    uid = uuid.uuid4()
    user = User(id=uid, email=email or f"p-{uid.hex[:8]}@example.org")
    user.role = role
    user.link_epoch = 0
    if disabled:
        user.disabled_at = datetime.now(timezone.utc)
    if deleted:
        user.deleted_at = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()
    return user


async def make_document(db: AsyncSession, kind: str, owner: User):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV

    model = GeneratedCV if kind == "cv" else GeneratedCoverLetter
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "job_analysis_id": uuid.uuid4(),
        "profile_id": uuid.uuid4(),
    }
    if kind == "cv":
        fields["tailored_data"] = {}
    if hasattr(model, "user_id"):
        fields["user_id"] = owner.id
    row = model(**fields)
    db.add(row)
    await db.commit()
    return row


class StubProvider:
    """``get_current_user`` → a fixed user id re-read from the request's DB session."""

    def __init__(self, user: User | None):
        self.user_id = user.id if user is not None else None

    async def get_current_user(self, request, db):
        if self.user_id is None:
            return None
        return await db.get(User, self.user_id)


def build_app(tdb: TokenDB, routers: Iterable[Any], *, as_user: User | None = None, middleware=()) -> FastAPI:
    app = FastAPI()
    for r in routers:
        app.include_router(r)
    for m in middleware:
        app.add_middleware(m)

    async def _get_db():
        async with tdb.maker() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db
    provider = StubProvider(as_user)

    async def _provider():
        return provider

    app.dependency_overrides[get_auth_provider] = _provider
    return app


# --- cross-user isolation registry (ADR-092 cl. 8c; W1 integration) ---------------

async def _personal_token_factory(world) -> str:
    """A personal ``api`` token of the world's owner — ``DELETE /api/me/tokens/{id}``
    by another user must be 404 (foreign = missing), by the owner 204."""
    from applire.auth.tokens import create_token

    row, _raw = await create_token(world.db, user_id=world.user.id, scope="api", name="iso")
    return str(row.id)


def _register_isolation_factories() -> None:
    from tests.support.isolation import RESOURCE_FACTORIES, register

    if "token_id@/api/me/tokens" not in RESOURCE_FACTORIES:
        register("token_id@/api/me/tokens", _personal_token_factory)


_register_isolation_factories()

