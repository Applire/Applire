# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``DELETE /api/me/account`` — self-deletion (ADR-091 cl. 25, RD-3; US326).

Built against a stubbed F9 ``erase`` (3b fills ``scope="account"`` in W2; the
real-path test lands with 3b)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from applire.models.audit import AuditEvent
from applire.models.user import User
from applire.services import erasure
from applire.services.admin import identity_seams as seams
from tests.support.owners_1b import add_user, build_app, client_for

H = {"Origin": "http://applire.test"}
PW = "my own long passphrase"


@pytest_asyncio.fixture
async def env(async_db, monkeypatch):
    calls = []

    async def fake_erase(db, user_id, scope):
        calls.append((user_id, scope))
        return {"master_profiles": 1}

    monkeypatch.setattr(erasure, "erase", fake_erase)
    app, acting = build_app(async_db)
    async with client_for(app) as client:
        yield async_db, client, acting, calls


async def _person(db, *, role="user", password=PW):
    return await add_user(db, role=role, password_hash=await seams.hash_password(password) if password else None)


@pytest.mark.asyncio
async def test_self_delete_with_password_erases_tombstones_and_clears_cookie(env):
    db, client, acting, calls = env
    await _person(db, role="admin")  # someone else stays admin
    me = await _person(db)
    acting.user_id = me.id
    r = await client.request("DELETE", "/api/me/account", json={"password": PW}, headers=H)
    assert r.status_code == 204, r.text
    assert calls == [(me.id, "account")]
    assert "applire_session=" in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]
    row = await db.get(User, me.id, populate_existing=True)
    assert row.email == f"deleted+{me.id}@invalid" and row.deleted_at is not None
    (ev,) = (await db.execute(select(AuditEvent).where(AuditEvent.action == "user.deleted"))).scalars()
    assert ev.detail["by"] == "self" and ev.actor_user_id == me.id


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"password": "wrong but long enough"}, {}])
async def test_wrong_or_missing_password_is_403_not_401(env, body):
    db, client, acting, calls = env
    me = await _person(db)
    acting.user_id = me.id
    r = await client.request("DELETE", "/api/me/account", json=body, headers=H)
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "invalid_credentials"
    assert calls == []


@pytest.mark.asyncio
async def test_passwordless_account_needs_a_reauth_grant(env, monkeypatch):
    db, client, acting, calls = env
    me = await add_user(db, password_hash=None)
    me.oidc_issuer, me.oidc_subject = "https://idp.example", "sub-1"
    await db.commit()
    acting.user_id = me.id
    r = await client.request("DELETE", "/api/me/account", json={}, headers=H)
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "reauth_required"
    assert calls == []

    seen = []

    async def granted(db_, *, request, user, action, target_id):
        seen.append((user.id, action, target_id))
        return True

    monkeypatch.setattr(seams, "consume_reauth_grant", granted)
    r = await client.request("DELETE", "/api/me/account", json={}, headers=H)
    assert r.status_code == 204
    assert seen == [(me.id, "account.delete", me.id)] and calls == [(me.id, "account")]


@pytest.mark.asyncio
async def test_last_admin_cannot_delete_themselves(env):
    db, client, acting, calls = env
    me = await _person(db, role="admin")
    acting.user_id = me.id
    r = await client.request("DELETE", "/api/me/account", json={"password": PW}, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "last_admin"
    assert calls == []
    assert (await db.get(User, me.id, populate_existing=True)).disabled_at is None


@pytest.mark.asyncio
async def test_anonymous_is_401(env):
    _, client, acting, _ = env
    acting.user_id = None
    r = await client.request("DELETE", "/api/me/account", json={}, headers=H)
    assert r.status_code == 401
