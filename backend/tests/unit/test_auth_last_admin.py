# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The last-admin predicate (ADR-091 cl. 7 + ruling MD-18; SF-IAM.7).

An admin counts as *active* only with role admin, not disabled, not tombstoned
and holding a credential. The concurrent case (two demotions racing) needs row
locks and is proven on Postgres in ``test_auth_pg_races.py``.
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from applire.auth.roles import assert_not_last_admin, effective_role, is_admin, lock_active_admins
from applire.models.user import User


def _u(role="admin", *, password=True, oidc=False, disabled=False, deleted=False):
    now = datetime.now(timezone.utc)
    uid = uuid.uuid4()
    return User(
        id=uid, email=f"{uid.hex[:8]}@example.org", role=role,
        password_hash="scrypt$x" if password else None,
        oidc_issuer="https://idp" if oidc else None, oidc_subject=uid.hex if oidc else None,
        disabled_at=now if disabled else None, deleted_at=now if deleted else None,
    )


@pytest.mark.asyncio
async def test_the_only_active_admin_cannot_be_removed(async_db):
    admin = _u()
    async_db.add_all([admin, _u("user")])
    await async_db.commit()
    with pytest.raises(HTTPException) as exc:
        await assert_not_last_admin(async_db, admin.id)
    assert exc.value.status_code == 409 and exc.value.detail["error_code"] == "last_admin"


@pytest.mark.asyncio
async def test_with_a_second_active_admin_it_is_allowed(async_db):
    a, b = _u(), _u(password=False, oidc=True)  # an OIDC-only admin counts
    async_db.add_all([a, b])
    await async_db.commit()
    await assert_not_last_admin(async_db, a.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other",
    [
        dict(disabled=True),
        dict(deleted=True),
        dict(password=False),  # MD-18: a pending invited admin cannot sign in
    ],
    ids=["disabled", "tombstoned", "pending"],
)
async def test_inactive_admins_do_not_count(async_db, other):
    a = _u()
    async_db.add_all([a, _u(**other)])
    await async_db.commit()
    with pytest.raises(HTTPException):
        await assert_not_last_admin(async_db, a.id)


@pytest.mark.asyncio
async def test_a_pending_admin_does_not_count_as_active(async_db):
    """Same property 1b's test of this name encodes (ruling MD-18)."""
    real, pending = _u(), _u(password=False)
    async_db.add_all([real, pending])
    await async_db.commit()
    assert await lock_active_admins(async_db) == [real.id]


@pytest.mark.asyncio
async def test_a_non_admin_target_never_trips_it(async_db):
    admin, user = _u(), _u("user")
    async_db.add_all([admin, user])
    await async_db.commit()
    await assert_not_last_admin(async_db, user.id)


def test_role_helpers():
    from types import SimpleNamespace

    assert is_admin(_u()) and not is_admin(_u("user"))
    harness_req = SimpleNamespace(state=SimpleNamespace(auth_via="harness"))
    assert effective_role(_u("user"), harness_req) == "admin"
