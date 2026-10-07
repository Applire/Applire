# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Invite/reset links: forgot, inspect, redeem (ADR-091 cl. 22–23, RD-8; US325).

Single use and expiry are proven twice: on the atomic ``consume_link`` statement
itself and through the route."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, update

from applire.models.audit import AuditEvent
from applire.models.auth import AuthLink, AuthSession
from applire.models.user import User
from applire.routers import auth_links as router_mod
from applire.services import mail
from applire.services.admin import identity_seams as seams
from applire.services.admin import links
from tests.support.owners_1b import add_user, build_app, client_for, same_session_factory

H = {"Origin": "http://applire.test"}
GOOD_PW = "a perfectly fine passphrase"


@pytest_asyncio.fixture
async def env(async_db, monkeypatch):
    app, acting = build_app(async_db)
    issued = []

    async def fake_issue_session(db, user, response):
        issued.append(user.id)
        response.set_cookie("applire_session", "fresh", httponly=True, samesite="lax", path="/")

    monkeypatch.setattr(seams, "issue_session", fake_issue_session)
    async with client_for(app) as client:
        yield async_db, client, issued


async def _link(db, user, purpose="invite"):
    link, raw = await links.issue_link(db, user, purpose)
    await db.commit()
    return link, raw


async def _expire(db, link):
    await db.execute(update(AuthLink).where(AuthLink.id == link.id)
                     .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    await db.commit()


# --- the atomic statement ---------------------------------------------------------

@pytest.mark.asyncio
async def test_consume_link_is_single_use(async_db):
    user = await add_user(async_db, state="pending")
    link, raw = await _link(async_db, user)
    first = await links.consume_link(async_db, raw)
    second = await links.consume_link(async_db, raw)
    assert first == (link.id, user.id, "invite")
    assert second is None


@pytest.mark.asyncio
async def test_consume_link_refuses_expired(async_db):
    user = await add_user(async_db, state="pending")
    link, raw = await _link(async_db, user)
    await _expire(async_db, link)
    assert await links.consume_link(async_db, raw) is None


@pytest.mark.asyncio
async def test_consume_link_refuses_unknown(async_db):
    assert await links.consume_link(async_db, "not-a-token") is None


@pytest.mark.asyncio
async def test_newer_link_supersedes_older_of_same_purpose_only(async_db):
    user = await add_user(async_db)
    _, reset_old = await _link(async_db, user, "reset")
    _, invite = await _link(async_db, user, "invite")
    _, reset_new = await _link(async_db, user, "reset")
    assert (await links.inspect_link(async_db, reset_old)).state == "used"
    assert (await links.inspect_link(async_db, reset_new)).state == "valid"
    assert (await links.inspect_link(async_db, invite)).state == "valid"


def test_only_the_hash_is_stored():
    assert set(AuthLink.__table__.columns.keys()) == {
        "id", "user_id", "purpose", "token_hash", "created_at", "expires_at", "used_at"}
    assert len(links.hash_link_token("x")) == 64


# --- inspect ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inspect_states(env):
    db, client, _ = env
    user = await add_user(db, email="inv@example.org", state="pending")
    link, raw = await _link(db, user)
    r = await client.post("/api/auth/links/inspect", json={"token": raw}, headers=H)
    assert r.status_code == 200
    assert r.json() == {"purpose": "invite", "email": "inv@example.org", "state": "valid"}
    assert r.headers["referrer-policy"] == "no-referrer"
    await _expire(db, link)
    r = await client.post("/api/auth/links/inspect", json={"token": raw}, headers=H)
    assert r.json()["state"] == "expired"
    r = await client.post("/api/auth/links/inspect", json={"token": "garbage"}, headers=H)
    assert r.status_code == 404 and r.json()["detail"]["error_code"] == "link_invalid"


@pytest.mark.asyncio
async def test_link_of_a_disabled_account_is_invalid(env):
    db, client, _ = env
    user = await add_user(db, state="disabled")
    _, raw = await _link(db, user, "reset")
    r = await client.post("/api/auth/links/inspect", json={"token": raw}, headers=H)
    assert r.status_code == 404


# --- redeem -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_redeem_invite_sets_password_signs_in_and_is_single_use(env):
    db, client, issued = env
    user = await add_user(db, email="new@example.org", state="pending")
    link, raw = await _link(db, user)
    r = await client.post("/api/auth/links/redeem", json={"token": raw, "password": GOOD_PW}, headers=H)
    assert r.status_code == 204, r.text
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "applire_session=fresh" in r.headers["set-cookie"]
    assert issued == [user.id]
    row = await db.get(User, user.id, populate_existing=True)
    assert row.password_hash and await seams.verify_password(GOOD_PW, row.password_hash)
    assert row.last_login_at is not None
    (ev,) = (await db.execute(select(AuditEvent))).scalars().all()
    assert ev.action == "invite.redeemed" and ev.detail == {"link_id": str(link.id)}

    again = await client.post("/api/auth/links/redeem", json={"token": raw, "password": GOOD_PW + "!"},
                              headers=H)
    assert again.status_code == 409 and again.json()["detail"]["error_code"] == "link_used"
    row = await db.get(User, user.id, populate_existing=True)
    assert await seams.verify_password(GOOD_PW, row.password_hash)


@pytest.mark.asyncio
async def test_redeem_expired_is_410_and_changes_nothing(env):
    db, client, issued = env
    user = await add_user(db, state="pending")
    link, raw = await _link(db, user)
    await _expire(db, link)
    r = await client.post("/api/auth/links/redeem", json={"token": raw, "password": GOOD_PW}, headers=H)
    assert r.status_code == 410 and r.json()["detail"]["error_code"] == "link_expired"
    assert (await db.get(User, user.id, populate_existing=True)).password_hash is None
    assert issued == []


@pytest.mark.asyncio
async def test_redeem_policy_failure_does_not_burn_the_link(env):
    db, client, _ = env
    user = await add_user(db, email="pp@example.org", state="pending")
    _, raw = await _link(db, user)
    r = await client.post("/api/auth/links/redeem", json={"token": raw, "password": "PP@example.org"},
                          headers=H)
    assert r.status_code == 422
    assert (await links.inspect_link(db, raw)).state == "valid"
    r = await client.post("/api/auth/links/redeem", json={"token": raw, "password": "short"}, headers=H)
    assert r.status_code == 422  # schema length
    assert (await links.inspect_link(db, raw)).state == "valid"


@pytest.mark.asyncio
async def test_redeem_loses_a_race_between_inspect_and_consume(env, monkeypatch):
    """Another request consumed the link after this one inspected it: the atomic
    statement is the decision, not the earlier read."""
    db, client, issued = env
    user = await add_user(db, state="pending")
    _, raw = await _link(db, user)
    real = links.consume_link

    async def competitor_first(db_, raw_):
        await real(db_, raw_)  # the other request wins
        return await real(db_, raw_)

    monkeypatch.setattr(links, "consume_link", competitor_first)
    r = await client.post("/api/auth/links/redeem", json={"token": raw, "password": GOOD_PW}, headers=H)
    assert r.status_code == 409 and issued == []


@pytest.mark.asyncio
async def test_redeem_reset_signs_out_other_sessions_and_audits(env):
    db, client, _ = env
    user = await add_user(db)
    db.add(AuthSession(user_id=user.id, token_hash="d" * 64))
    await db.commit()
    link, raw = await _link(db, user, "reset")
    r = await client.post("/api/auth/links/redeem", json={"token": raw, "password": GOOD_PW}, headers=H)
    assert r.status_code == 204
    assert (await db.execute(select(AuthSession.revoked_at))).scalar_one() is not None
    (ev,) = (await db.execute(select(AuditEvent))).scalars().all()
    assert ev.action == "password.reset" and ev.detail == {"via": "link", "link_id": str(link.id)}


@pytest.mark.asyncio
async def test_token_never_travels_in_a_path_or_query():
    paths = {r.path for r in router_mod.router.routes}
    assert paths == {"/api/auth/forgot", "/api/auth/links/inspect", "/api/auth/links/redeem"}
    assert all("{" not in p for p in paths)
    assert all(r.methods == {"POST"} for r in router_mod.router.routes)


def test_public_routes_take_the_origin_check():
    for route in router_mod.router.routes:
        deps = [d.call for d in route.dependant.dependencies]
        assert router_mod.require_origin in deps, route.path


# --- forgot -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_forgot_is_202_without_body_for_known_and_unknown(env, monkeypatch):
    db, client, _ = env
    await add_user(db, email="known@example.org")
    tasks = []
    monkeypatch.setattr(mail, "smtp_enabled", lambda: True)
    monkeypatch.setattr(router_mod, "_send_forgot_mail", lambda *a, **k: tasks.append(a))
    a = await client.post("/api/auth/forgot", json={"email": "known@example.org"}, headers=H)
    b = await client.post("/api/auth/forgot", json={"email": "nobody@example.org"}, headers=H)
    assert a.status_code == b.status_code == 202
    assert a.content == b.content == b""
    assert len(tasks) == 2  # the lookup happens in the background for both


@pytest.mark.asyncio
async def test_forgot_with_smtp_off_does_nothing(env, monkeypatch):
    _, client, _ = env
    tasks = []
    monkeypatch.setattr(mail, "smtp_enabled", lambda: False)
    monkeypatch.setattr(router_mod, "_send_forgot_mail", lambda *a, **k: tasks.append(a))
    r = await client.post("/api/auth/forgot", json={"email": "x@example.org"}, headers=H)
    assert r.status_code == 202 and tasks == []


@pytest.fixture
def sent(monkeypatch):
    out = []

    async def fake_send(purpose, **kw):
        out.append((purpose, kw))
        return True

    monkeypatch.setattr(mail, "send_link_mail", fake_send)
    return out


@pytest.mark.asyncio
async def test_forgot_background_mails_a_reset_link(async_db, sent):
    user = await add_user(async_db, email="Known@Example.org")
    await router_mod._send_forgot_mail("known@example.org", "http://applire.test",
                                       session_factory=lambda: same_session_factory(async_db))
    (purpose, kw), = sent
    assert purpose == "reset" and kw["to"] == "Known@Example.org"
    raw = kw["link"].split("#")[1]
    assert kw["link"].startswith("http://applire.test/reset#")
    found = await links.inspect_link(async_db, raw)
    assert found.user.id == user.id and found.state == "valid"
    (ev,) = (await async_db.execute(select(AuditEvent))).scalars().all()
    assert ev.action == "reset_link.issued" and ev.detail["via"] == "forgot" and ev.actor_user_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "disabled"])
async def test_forgot_background_ignores_non_active_accounts(async_db, sent, state):
    await add_user(async_db, email="s@example.org", state=state)
    await router_mod._send_forgot_mail("s@example.org", "http://h",
                                       session_factory=lambda: same_session_factory(async_db))
    assert sent == []


@pytest.mark.asyncio
async def test_forgot_background_unknown_email_sends_nothing(async_db, sent):
    await router_mod._send_forgot_mail("ghost@example.org", "http://h",
                                       session_factory=lambda: same_session_factory(async_db))
    assert sent == []


@pytest.mark.asyncio
async def test_forgot_is_throttled_to_three_per_hour_per_account(async_db, sent, caplog):
    await add_user(async_db, email="t@example.org")
    with caplog.at_level(logging.INFO):
        for _ in range(5):
            await router_mod._send_forgot_mail("t@example.org", "http://h",
                                               session_factory=lambda: same_session_factory(async_db))
    assert len(sent) == 3
    assert "t@example.org" not in caplog.text
