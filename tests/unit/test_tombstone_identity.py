# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""785:L81 / founder ruling R-4 — one tombstone, two paths.

The explicit account deletion and the retention worker's 24-month inactivity
rule retire an account the same way (``erasure.tombstone_identity``): only
``id``, ``role``, ``created_at``, ``disabled_at``, ``deleted_at`` and
``link_epoch`` survive; the email becomes ``deleted+<id>@invalid`` (so the person
can be invited again), credentials, SSO binding and login timestamps are
cleared, and the account's sessions, tokens and open links are revoked.
Before, the retention tombstone set ``deleted_at`` only."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.auth.tokens import create_token
from applire.db.session import Base
from applire.models.auth import AuthSession, PersonalToken
from applire.models.user import User
from applire.retention import worker
from applire.services.erasure import TOMBSTONE_KEEPS, tombstone_identity


def _ago(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


@pytest_asyncio.fixture
async def factory():
    eng = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(eng, expire_on_commit=False)
    await eng.dispose()


def _full_user(**over) -> User:
    now = datetime.now(timezone.utc)
    fields = dict(
        id=uuid.uuid4(), email=f"anna-{uuid.uuid4().hex[:6]}@example.org", role="user",
        created_at=_ago(1000), last_active_at=_ago(800), last_login_at=_ago(800),
        password_hash="scrypt$15$8$1$x$y", oidc_issuer="https://idp.example",
        oidc_subject=f"sub-{uuid.uuid4().hex[:8]}", email_verified_at=now, photo_consent=True, photo_consent_at=now,
        link_epoch=3,
    )
    fields.update(over)
    return User(**fields)


def _identifying_columns() -> set[str]:
    return {c.key for c in sa_inspect(User).columns} - TOMBSTONE_KEEPS - {"email"}


def test_every_user_column_is_classified():
    """A new identifying column on `users` must be scrubbed or declared kept:
    the helper clears it (this asserts it does) or TOMBSTONE_KEEPS names it."""
    u = _full_user()
    tombstone_identity(u)
    for col in _identifying_columns():
        assert getattr(u, col) in (None, False), f"tombstone keeps users.{col}={getattr(u, col)!r}"
    assert u.email == f"deleted+{u.id}@invalid"
    assert u.link_epoch == 4 and u.deleted_at is not None and u.role == "user"


def test_an_existing_deletion_date_is_kept():
    when = _ago(5)
    u = _full_user(deleted_at=when)
    tombstone_identity(u)
    assert u.deleted_at == when


@pytest.mark.asyncio
async def test_the_retention_tombstone_scrubs_identity_and_revokes_access(factory):
    ttl = worker._INACTIVITY_TTL_DAYS
    with ownership.unscoped("retention"):
        async with factory() as s:
            gone = _full_user(created_at=_ago(ttl + 400), last_active_at=_ago(ttl + 5))
            kept = _full_user(created_at=_ago(ttl + 400), last_active_at=_ago(3))
            s.add_all([gone, kept])
            await s.flush()
            for u in (gone, kept):
                s.add(AuthSession(user_id=u.id, token_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                                  last_seen_at=_ago(1)))
                await create_token(s, user_id=u.id, scope="api", name="ci")
            await s.commit()
            original_email = gone.email
            assert await worker._tombstone_inactive_users(s) == 1
        async with factory() as s:
            g = await s.get(User, gone.id)
            k = await s.get(User, kept.id)
            for col in _identifying_columns():
                assert getattr(g, col) in (None, False), f"users.{col} survived the retention tombstone"
            assert g.email == f"deleted+{g.id}@invalid" and g.deleted_at is not None
            assert k.deleted_at is None and k.password_hash is not None
            sessions = {r.user_id: r.revoked_at for r in (await s.execute(select(AuthSession))).scalars()}
            tokens = {r.user_id: r.revoked_at for r in (await s.execute(select(PersonalToken))).scalars()}
            assert sessions[gone.id] is not None and tokens[gone.id] is not None
            assert sessions[kept.id] is None and tokens[kept.id] is None
            # the email is free again: the person can be invited anew
            s.add(User(id=uuid.uuid4(), email=original_email, role="user"))
            await s.commit()
