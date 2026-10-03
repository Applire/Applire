# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""``/api/admin/users`` — create/invite, roles, disable, tokens, delete, last admin
(ADR-091 cl. 7, 22, 25–27; US324, US326 admin half)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from applire.models.audit import AuditEvent
from applire.models.auth import AuthLink, AuthSession, PersonalToken
from applire.models.user import User
from applire.services import erasure
from applire.services.admin import links as links_service
from applire.services.admin import users as accounts
from tests.support.owners_1b import add_user, build_app, client_for

ORIGIN_HEADERS = {"Origin": "http://applire.test"}


@pytest_asyncio.fixture
async def env(async_db):
    app, acting = build_app(async_db)
    admin = await add_user(async_db, email="admin@example.org", role="admin")
    acting.user_id = admin.id
    async with client_for(app) as client:
        yield async_db, client, acting, admin


async def _audit(db, action):
    return (await db.execute(select(AuditEvent).where(AuditEvent.action == action))).scalars().all()


@pytest.fixture
def erase_calls(monkeypatch):
    calls = []

    async def fake_erase(db, user_id, scope):
        calls.append((user_id, scope))
        return {"master_profiles": 1, "applications": 2}

    monkeypatch.setattr(erasure, "erase", fake_erase)
    return calls


# --- access ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_admin_gets_403_and_anonymous_401(env):
    db, client, acting, _ = env
    user = await add_user(db, role="user")
    acting.user_id = user.id
    r = await client.get("/api/admin/users")
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "forbidden"
    acting.user_id = None
    assert (await client.get("/api/admin/users")).status_code == 401


# --- create / invite ------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_makes_pending_account_with_fragment_invite_link(env):
    db, client, _, admin = env
    r = await client.post("/api/admin/users", json={"email": "New.Person@Example.org"},
                          headers=ORIGIN_HEADERS)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["user"]["status"] == "pending" and body["user"]["role"] == "user"
    link = body["link"]
    assert link["purpose"] == "invite" and link["mailed"] is False and link["mail_failed"] is False
    assert link["url"].startswith("http://applire.test/invite#")
    raw = link["url"].split("#", 1)[1]
    assert "?" not in link["url"] and len(raw) >= 40
    row = (await db.execute(select(AuthLink))).scalar_one()
    assert row.token_hash == links_service.hash_link_token(raw) and raw not in row.token_hash
    exp = datetime.fromisoformat(link["expires_at"].replace("Z", "+00:00"))
    assert timedelta(days=6, hours=23) < exp - datetime.now(timezone.utc) <= timedelta(days=7)
    assert body["user"]["invite_expires_at"] is not None
    (ev,) = await _audit(db, "user.created")
    assert ev.actor_user_id == admin.id and ev.detail == {"role": "user", "mailed": False}


@pytest.mark.asyncio
async def test_email_taken_is_case_insensitive(env):
    db, client, _, _ = env
    await add_user(db, email="taken@example.org")
    r = await client.post("/api/admin/users", json={"email": "TAKEN@example.org"}, headers=ORIGIN_HEADERS)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "email_taken"


@pytest.mark.asyncio
async def test_link_uses_applire_base_url_when_set(env, monkeypatch):
    _, client, _, _ = env
    from applire.config import settings
    monkeypatch.setattr(settings, "applire_base_url", "https://jobs.example.org/")
    r = await client.post("/api/admin/users", json={"email": "b@example.org"}, headers=ORIGIN_HEADERS)
    assert r.json()["link"]["url"].startswith("https://jobs.example.org/invite#")


@pytest.mark.asyncio
async def test_forged_origin_does_not_steer_the_link(env):
    _, client, _, _ = env
    r = await client.post("/api/admin/users", json={"email": "c@example.org"},
                          headers={"Origin": "http://evil.example"})
    assert r.json()["link"]["url"].startswith("http://applire.test/invite#")


@pytest.mark.asyncio
async def test_create_mails_when_smtp_is_on(env, monkeypatch):
    _, client, _, _ = env
    from applire.services import mail
    sent = []

    async def fake_send(purpose, **kw):
        sent.append((purpose, kw))
        return True

    monkeypatch.setattr(mail, "smtp_enabled", lambda: True)
    monkeypatch.setattr(mail, "send_link_mail", fake_send)
    r = await client.post("/api/admin/users", json={"email": "m@example.org", "role": "admin"},
                          headers=ORIGIN_HEADERS)
    assert r.json()["link"]["mailed"] is True
    purpose, kw = sent[0]
    assert purpose == "invite" and kw["to"] == "m@example.org" and kw["link"] == r.json()["link"]["url"]


@pytest.mark.asyncio
async def test_mail_failure_tells_admin_to_hand_over(env, monkeypatch):
    _, client, _, _ = env
    from applire.services import mail

    async def failing(purpose, **kw):
        return False

    monkeypatch.setattr(mail, "smtp_enabled", lambda: True)
    monkeypatch.setattr(mail, "send_link_mail", failing)
    r = await client.post("/api/admin/users", json={"email": "f@example.org"}, headers=ORIGIN_HEADERS)
    assert r.status_code == 201
    assert r.json()["link"]["mailed"] is False and r.json()["link"]["mail_failed"] is True


@pytest.mark.asyncio
async def test_reinvite_supersedes_older_link_and_refuses_non_pending(env):
    db, client, _, _ = env
    first = (await client.post("/api/admin/users", json={"email": "r@example.org"},
                               headers=ORIGIN_HEADERS)).json()
    uid = first["user"]["id"]
    old_raw = first["link"]["url"].split("#")[1]
    r = await client.post(f"/api/admin/users/{uid}/reinvite", headers=ORIGIN_HEADERS)
    assert r.status_code == 200 and r.json()["purpose"] == "invite"
    old = await links_service.inspect_link(db, old_raw)
    assert old.state == "used"
    active = await add_user(db)
    r = await client.post(f"/api/admin/users/{active.id}/reinvite", headers=ORIGIN_HEADERS)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "user_not_pending"
    assert len(await _audit(db, "user.reinvited")) == 1


@pytest.mark.asyncio
async def test_reset_link_for_active_only(env):
    db, client, _, _ = env
    active = await add_user(db)
    r = await client.post(f"/api/admin/users/{active.id}/reset-link", headers=ORIGIN_HEADERS)
    assert r.status_code == 200 and r.json()["url"].startswith("http://applire.test/reset#")
    exp = datetime.fromisoformat(r.json()["expires_at"].replace("Z", "+00:00"))
    assert exp - datetime.now(timezone.utc) <= timedelta(hours=1)
    for state in ("pending", "disabled"):
        other = await add_user(db, state=state)
        r = await client.post(f"/api/admin/users/{other.id}/reset-link", headers=ORIGIN_HEADERS)
        assert r.status_code == 409 and r.json()["detail"]["error_code"] == "user_not_active"
    (ev,) = await _audit(db, "reset_link.issued")
    assert ev.detail["via"] == "admin"


@pytest.mark.asyncio
async def test_unknown_id_is_404_plain(env):
    _, client, _, _ = env
    r = await client.patch(f"/api/admin/users/{uuid.uuid4()}", json={"role": "admin"},
                           headers=ORIGIN_HEADERS)
    assert r.status_code == 404 and r.json() == {"detail": "user not found"}


# --- roles / disable / last admin -----------------------------------------------

@pytest.mark.asyncio
async def test_last_admin_cannot_demote_or_disable_themselves(env):
    db, client, _, admin = env
    for body in ({"role": "user"}, {"disabled": True}):
        r = await client.patch(f"/api/admin/users/{admin.id}", json=body, headers=ORIGIN_HEADERS)
        assert r.status_code == 409 and r.json()["detail"]["error_code"] == "last_admin"
    await db.refresh(admin)
    assert admin.role == "admin" and admin.disabled_at is None


@pytest.mark.asyncio
async def test_a_pending_admin_does_not_count_as_active(env):
    db, client, _, admin = env
    await add_user(db, role="admin", state="pending")
    r = await client.patch(f"/api/admin/users/{admin.id}", json={"role": "user"}, headers=ORIGIN_HEADERS)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_with_two_admins_one_may_step_down(env):
    db, client, _, admin = env
    await add_user(db, role="admin")
    r = await client.patch(f"/api/admin/users/{admin.id}", json={"role": "user"}, headers=ORIGIN_HEADERS)
    assert r.status_code == 200 and r.json()["role"] == "user"
    (ev,) = await _audit(db, "user.role_changed")
    assert ev.detail == {"from_role": "admin", "to_role": "user"}


@pytest.mark.asyncio
async def test_disable_revokes_sessions_and_tokens_and_bumps_link_epoch(env):
    db, client, _, _ = env
    user = await add_user(db)
    db.add(AuthSession(user_id=user.id, token_hash="a" * 64))
    db.add(PersonalToken(user_id=user.id, scope="agent", name="t", prefix="abcdefgh", token_hash="b" * 64))
    await db.commit()
    r = await client.patch(f"/api/admin/users/{user.id}", json={"disabled": True}, headers=ORIGIN_HEADERS)
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    await db.refresh(user)
    assert user.link_epoch == 1
    assert (await db.execute(select(AuthSession.revoked_at))).scalar_one() is not None
    assert (await db.execute(select(PersonalToken.revoked_at))).scalar_one() is not None
    r = await client.patch(f"/api/admin/users/{user.id}", json={"disabled": False}, headers=ORIGIN_HEADERS)
    assert r.json()["status"] == "active"
    assert [len(await _audit(db, a)) for a in ("user.disabled", "user.enabled")] == [1, 1]


@pytest.mark.asyncio
async def test_empty_patch_is_422(env):
    db, client, _, _ = env
    user = await add_user(db)
    r = await client.patch(f"/api/admin/users/{user.id}", json={}, headers=ORIGIN_HEADERS)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_revoke_tokens_counts_and_audits(env):
    db, client, _, _ = env
    user = await add_user(db)
    for i, scope in enumerate(("agent", "api", "probe")):
        db.add(PersonalToken(user_id=user.id, scope=scope, name="t", prefix=f"pfx0000{i}",
                             token_hash=str(i) * 64))
    await db.commit()
    r = await client.post(f"/api/admin/users/{user.id}/revoke-tokens", headers=ORIGIN_HEADERS)
    assert r.json() == {"revoked": 2}  # probe tokens are the admin's own surface
    (ev,) = await _audit(db, "tokens.revoked_all")
    assert ev.detail == {"count": 2} and ev.target_user_id == user.id


# --- delete ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_delete_erases_tombstones_and_audits(env, erase_calls):
    db, client, _, admin = env
    user = await add_user(db, email="leaving@example.org")
    db.add(AuthSession(user_id=user.id, token_hash="c" * 64))
    await db.commit()
    await links_service.issue_link(db, user, "reset")
    await db.commit()
    r = await client.delete(f"/api/admin/users/{user.id}", headers=ORIGIN_HEADERS)
    assert r.status_code == 204
    assert erase_calls == [(user.id, "account")]
    row = await db.get(User, user.id, populate_existing=True)
    assert row.email == f"deleted+{user.id}@invalid" and row.password_hash is None
    assert row.deleted_at is not None and row.link_epoch >= 1
    assert (await db.execute(select(AuthSession.revoked_at))).scalar_one() is not None
    assert (await db.execute(select(AuthLink.used_at))).scalar_one() is not None
    (ev,) = await _audit(db, "user.deleted")
    assert ev.detail == {"by": "admin", "erased_rows": 3} and ev.actor_user_id == admin.id
    listed = (await client.get("/api/admin/users")).json()["users"]
    assert str(user.id) not in {u["id"] for u in listed}
    again = await client.delete(f"/api/admin/users/{user.id}", headers=ORIGIN_HEADERS)
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_last_admin_cannot_be_deleted_and_nothing_is_erased(env, erase_calls):
    db, client, _, admin = env
    r = await client.delete(f"/api/admin/users/{admin.id}", headers=ORIGIN_HEADERS)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "last_admin"
    assert erase_calls == []
    await db.refresh(admin)
    assert admin.disabled_at is None and admin.deleted_at is None


@pytest.mark.asyncio
async def test_erasure_failure_leaves_account_disabled_and_retryable(env, monkeypatch):
    db, _, _, admin = env
    user = await add_user(db)

    async def broken(db, user_id, scope):
        raise erasure.ErasureFailed("rolled back")

    monkeypatch.setattr(erasure, "erase", broken)
    with pytest.raises(erasure.ErasureFailed):
        await accounts.delete_account(db, actor=admin, user_id=user.id, by="admin")
    row = await db.get(User, user.id, populate_existing=True)
    assert row.disabled_at is not None and row.deleted_at is None and row.email != f"deleted+{user.id}@invalid"
    assert await _audit(db, "user.deleted") == []


@pytest.mark.asyncio
async def test_w0_erase_stub_raises_and_nothing_is_tombstoned(env):
    """Against the real F9 stub (3b fills it in W2): the endpoint never half-deletes."""
    db, _, _, admin = env
    user = await add_user(db)
    with pytest.raises(NotImplementedError):
        await accounts.delete_account(db, actor=admin, user_id=user.id, by="admin")
    row = await db.get(User, user.id, populate_existing=True)
    assert row.deleted_at is None
