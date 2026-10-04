# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""OIDC sign-in, invite binding, link-from-session, unlink and fresh re-auth through
the real routes (ADR-091 cl. 6, 23; MD-7; R3-2; W0B-3; US323). Fake IdP, no network;
real sessions (``LocalAuthProvider``); 1b's ``DELETE /api/me/account`` mounted to
prove ``consume_grant`` at its real caller."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select, update

from applire.auth import oidc
from applire.auth.sessions import SESSION_COOKIE
from applire.models.audit import AuditEvent
from applire.models.auth import AuthLink, AuthSession, ReauthGrant
from applire.models.user import User
from applire.services import erasure
from applire.services.admin import links as admin_links
from tests.support.fake_idp import (
    ISSUER,
    ORIGIN,
    FakeIdP,
    add_person,
    build_app,
    client_for,
    configure,
    no_cookie,
    sign_in,
)

PWHASH = "scrypt$15$8$1$placeholder$placeholder"


@pytest_asyncio.fixture
async def env(async_db, monkeypatch):
    fake = FakeIdP()
    configure(monkeypatch, fake)
    erased = []

    async def fake_erase(db, user_id, scope):
        erased.append((user_id, scope))
        return {}

    monkeypatch.setattr(erasure, "erase", fake_erase)
    async with client_for(build_app(async_db)) as client:
        yield async_db, client, fake, erased
    oidc.clear_caches()


async def _start(client, path="/api/auth/oidc/start?next=/applications"):
    r = await client.get(path)
    assert r.status_code == 302, r.text
    return r.headers["location"]


async def _callback(client, code, state):
    r = await client.get(f"/api/auth/oidc/callback?code={code}&state={state}")
    assert r.status_code == 302, r.text
    return r


async def _login(client, fake, **claims):
    url = await _start(client)
    code, state = fake.code_for(url, **claims)
    return await _callback(client, code, state)


async def _reload(db, user):
    # identity key, not ``user.id``: a rolled-back bind expires the test's objects
    return await db.get(User, sa_inspect(user).identity[0], populate_existing=True)


def _session_set(r) -> bool:
    return any(h.startswith(f"{SESSION_COOKIE}=") and "Max-Age=0" not in h
               for h in r.headers.get_list("set-cookie"))


# --- sign-in + MD-7 binding -------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_invite_binds_on_verified_email_and_signs_in(env):
    db, client, fake, _ = env
    person = await add_person(db, email="invitee@example.org", pending=True)
    r = await _login(client, fake)
    assert r.headers["location"] == "/applications" and _session_set(r)
    row = await _reload(db, person)
    assert (row.oidc_issuer, row.oidc_subject) == (ISSUER, "sub-1")
    assert row.last_login_at is not None and row.email_verified_at is not None
    (link,) = (await db.execute(select(AuthLink).where(AuthLink.user_id == person.id))).scalars()
    assert link.used_at is not None  # the invite cannot later set a password
    actions = {e.action for e in (await db.execute(select(AuditEvent))).scalars()}
    assert {"oidc.linked", "invite.redeemed"} <= actions
    # PKCE + client auth were really sent (the fake refuses otherwise).
    assert fake.token_requests == 1


@pytest.mark.asyncio
async def test_bound_identity_signs_in_again_without_an_invite(env):
    db, client, fake, _ = env
    await add_person(db, email="invitee@example.org", password_hash=None, oidc_subject="sub-1")
    r = await _login(client, fake, email="changed@example.org", email_verified=False)
    assert r.headers["location"] == "/applications" and _session_set(r)


@pytest.mark.asyncio
async def test_email_claim_is_matched_with_sql_lower(env):
    db, client, fake, _ = env
    person = await add_person(db, email="invitee@example.org", pending=True)
    await _login(client, fake, email="InViTee@Example.ORG")
    assert (await _reload(db, person)).oidc_subject == "sub-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("verified", ["false", "true", 1, None, False])
async def test_email_verified_must_be_strictly_true(env, verified):
    db, client, fake, _ = env
    person = await add_person(db, email="invitee@example.org", pending=True)
    r = await _login(client, fake, email_verified=verified)
    assert r.headers["location"] == "/login?error=oidc_no_account" and not _session_set(r)
    assert (await _reload(db, person)).oidc_subject is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    ["no_account", "active_password", "active_password_with_open_invite", "already_bound", "signed_in_before", "expired_invite",
     "used_invite", "disabled_pending"],
)
async def test_no_binding_outside_a_pending_unexpired_invite(env, kind):
    db, client, fake, _ = env
    email = "invitee@example.org"
    person = None
    if kind == "active_password":
        person = await add_person(db, email=email, password_hash=PWHASH)
    elif kind == "active_password_with_open_invite":
        # e.g. invited, then given a password by the CLI reset: the invite is still open
        person = await add_person(db, email=email, pending=True)
        person.password_hash = PWHASH
        await db.commit()
    elif kind == "already_bound":
        person = await add_person(db, email=email, oidc_subject="another-sub")
    elif kind == "signed_in_before":
        person = await add_person(db, email=email, pending=True)
        person.last_login_at = datetime.now(timezone.utc)
        await db.commit()
    elif kind == "expired_invite":
        person = await add_person(db, email=email, pending=True, invite_expires_in=timedelta(seconds=-1))
    elif kind == "used_invite":
        person = await add_person(db, email=email, pending=True)
        await db.execute(update(AuthLink).values(used_at=datetime.now(timezone.utc)))
        await db.commit()
    elif kind == "disabled_pending":
        person = await add_person(db, email=email, pending=True, disabled=True)
    r = await _login(client, fake)
    assert r.headers["location"] == "/login?error=oidc_no_account" and not _session_set(r)
    if person is not None:
        row = await _reload(db, person)
        assert row.oidc_subject in (None, "another-sub")


@pytest.mark.asyncio
async def test_invite_link_cannot_set_a_password_after_the_bind(env):
    db, client, fake, _ = env
    person = await add_person(db, email="invitee@example.org", pending=True)
    _, raw = await admin_links.issue_link(db, person, "invite")
    await db.commit()
    await _login(client, fake)
    assert await admin_links.consume_link(db, raw) is None


@pytest.mark.asyncio
async def test_disabled_bound_account_gets_account_disabled(env):
    db, client, fake, _ = env
    await add_person(db, email="x@example.org", oidc_subject="sub-1", disabled=True)
    r = await _login(client, fake)
    assert r.headers["location"] == "/login?error=account_disabled" and not _session_set(r)


@pytest.mark.asyncio
async def test_replayed_state_is_refused_even_if_the_idp_accepts_the_code_again(env):
    db, client, fake, _ = env
    await add_person(db, email="x@example.org", oidc_subject="sub-1")
    url = await _start(client)
    code, state = fake.code_for(url)
    cookie = client.cookies.get(oidc.STATE_COOKIE)
    first = await _callback(client, code, state)
    assert first.headers["location"] == "/applications"
    client.cookies.set(oidc.STATE_COOKIE, cookie, domain="applire.test", path=oidc.STATE_COOKIE_PATH)
    client.cookies.delete(SESSION_COOKIE)
    second = await _callback(client, code, state)
    assert second.headers["location"] == "/login?error=oidc_failed" and not _session_set(second)
    assert fake.token_requests == 1


@pytest.mark.asyncio
async def test_state_mismatch_or_missing_cookie_fails(env):
    db, client, fake, _ = env
    await add_person(db, email="x@example.org", oidc_subject="sub-1")
    url = await _start(client)
    code, state = fake.code_for(url)
    r = await _callback(client, code, state + "x")
    assert r.headers["location"] == "/login?error=oidc_failed"
    async with no_cookie(client, oidc.STATE_COOKIE):
        client.cookies.delete(oidc.STATE_COOKIE)
        r = await _callback(client, code, state)
    assert r.headers["location"] == "/login?error=oidc_failed"
    assert fake.token_requests == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [{"iss": "https://evil.test"}, {"aud": "other-client"}, {"aud": ["applire-client", "x"]},
     {"azp": "other-client"}, {"nonce": "not-the-flow-nonce"}, {"exp": int(time.time()) - 600}],
)
async def test_bad_id_token_fails_and_binds_nothing(env, claims):
    db, client, fake, _ = env
    person = await add_person(db, email="invitee@example.org", pending=True)
    r = await _login(client, fake, **claims)
    assert r.headers["location"] == "/login?error=oidc_failed" and not _session_set(r)
    assert (await _reload(db, person)).oidc_subject is None


@pytest.mark.asyncio
async def test_discovery_failure_at_start_redirects_to_login_error(env):
    _, client, fake, _ = env
    fake.discovery["issuer"] = "https://other.test"
    r = await client.get("/api/auth/oidc/start")
    assert r.status_code == 302 and r.headers["location"] == "/login?error=oidc_failed"


@pytest.mark.asyncio
async def test_next_is_sanitised(env):
    db, client, fake, _ = env
    await add_person(db, email="x@example.org", oidc_subject="sub-1")
    url = await _start(client, "/api/auth/oidc/start?next=//evil.com/x")
    r = await _callback(client, *fake.code_for(url))
    assert r.headers["location"] == "/"


@pytest.mark.asyncio
async def test_every_oidc_route_is_404_when_oidc_is_off(env, monkeypatch):
    from applire.config import settings

    db, client, _, _ = env
    me = await add_person(db, email="x@example.org", password_hash=PWHASH)
    await sign_in(db, client, me)
    monkeypatch.setattr(settings, "oidc_issuer", "")
    assert (await client.get("/api/auth/oidc/start")).status_code == 404
    assert (await client.get("/api/auth/oidc/callback?code=a&state=b")).status_code == 404
    assert (await client.post("/api/me/oidc/link", headers=ORIGIN)).status_code == 404
    assert (await client.request("DELETE", "/api/me/oidc", headers=ORIGIN)).status_code == 404
    r = await client.post("/api/me/reauth/start", headers=ORIGIN,
                          json={"action": "account.delete", "target_id": str(me.id)})
    assert r.status_code == 404


# --- link from a session ---------------------------------------------------------------

async def _link_url(client):
    r = await client.post("/api/me/oidc/link", headers=ORIGIN)
    assert r.status_code == 200, r.text
    return r.json()["authorize_url"]


@pytest.mark.asyncio
async def test_link_from_session_binds_the_signed_in_account(env):
    db, client, fake, _ = env
    me = await add_person(db, email="me@example.org", password_hash=PWHASH)
    await sign_in(db, client, me)
    url = await _link_url(client)
    r = await _callback(client, *fake.code_for(url, sub="my-sub", email="other@idp.org", email_verified=False))
    assert r.headers["location"] == "/settings?oidc=linked"
    assert (await _reload(db, me)).oidc_subject == "my-sub"
    (ev,) = (await db.execute(select(AuditEvent).where(AuditEvent.action == "oidc.linked"))).scalars()
    assert ev.detail == {"issuer": ISSUER}


@pytest.mark.asyncio
async def test_link_finished_by_another_session_user_is_refused(env):
    """R3-2iii: the attacker starts a link flow; the victim's browser finishes it."""
    db, client, fake, _ = env
    attacker = await add_person(db, email="attacker@example.org", password_hash=PWHASH)
    victim = await add_person(db, email="victim@example.org", password_hash=PWHASH)
    await sign_in(db, client, attacker)
    url = await _link_url(client)
    code, state = fake.code_for(url, sub="attacker-idp-sub")
    await sign_in(db, client, victim)  # same browser jar now holds the victim's session
    r = await _callback(client, code, state)
    assert r.headers["location"] == "/settings?oidc=failed"
    assert (await _reload(db, victim)).oidc_subject is None
    assert (await _reload(db, attacker)).oidc_subject is None


@pytest.mark.asyncio
async def test_link_without_session_or_to_a_taken_identity_fails(env):
    db, client, fake, _ = env
    await add_person(db, email="owner@example.org", oidc_subject="taken")
    me = await add_person(db, email="me@example.org", password_hash=PWHASH)
    await sign_in(db, client, me)
    url = await _link_url(client)
    r = await _callback(client, *fake.code_for(url, sub="taken"))
    assert r.headers["location"] == "/settings?oidc=failed"
    assert (await _reload(db, me)).oidc_subject is None
    url = await _link_url(client)
    code, state = fake.code_for(url, sub="fresh")
    client.cookies.delete(SESSION_COOKIE)
    r = await _callback(client, code, state)
    assert r.headers["location"] == "/settings?oidc=failed"


# --- fresh re-auth ------------------------------------------------------------------------

async def _oidc_only(db, client):
    me = await add_person(db, email="sso@example.org", password_hash=None, oidc_subject="sub-1")
    await sign_in(db, client, me)
    return me


async def _reauth_url(client, me, action="account.delete"):
    r = await client.post("/api/me/reauth/start", headers=ORIGIN,
                          json={"action": action, "target_id": str(me.id)})
    assert r.status_code == 200, r.text
    return r.json()["authorize_url"]


async def _delete_account(client):
    return await client.request("DELETE", "/api/me/account", json={}, headers=ORIGIN)


@pytest.mark.asyncio
async def test_reauth_then_account_delete_consumes_the_grant_once(env):
    db, client, fake, erased = env
    await add_person(db, email="admin@example.org", password_hash=PWHASH)  # not the last admin case
    me = await _oidc_only(db, client)
    assert (await _delete_account(client)).json()["detail"]["error_code"] == "reauth_required"
    url = await _reauth_url(client, me)
    q = dict(__import__("urllib.parse").parse.parse_qsl(url.split("?", 1)[1]))
    assert q["prompt"] == "login" and q["max_age"] == "0"
    r = await _callback(client, *fake.code_for(url, auth_time=int(time.time()) + 1))
    assert r.headers["location"] == "/settings?reauth=account.delete"
    (grant,) = (await db.execute(select(ReauthGrant))).scalars()
    assert grant.verified_at is not None and grant.used_at is None
    d = await _delete_account(client)
    assert d.status_code == 204, d.text
    assert erased == [(me.id, "account")]
    await db.refresh(grant)
    assert grant.used_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [{"auth_time": None},                                  # IdP sent no auth_time → fail closed
     {"auth_time": int(time.time()) - 120},                # a login before the grant started
     {"auth_time": "9999999999"},                          # not an integer
     {"sub": "someone-else"}],                             # not the account's own identity
)
async def test_reauth_refusals_leave_the_grant_unverified(env, claims):
    db, client, fake, erased = env
    me = await _oidc_only(db, client)
    url = await _reauth_url(client, me)
    r = await _callback(client, *fake.code_for(url, **claims))
    assert r.headers["location"] == "/settings?oidc=failed"
    (grant,) = (await db.execute(select(ReauthGrant))).scalars()
    assert grant.verified_at is None
    assert (await _delete_account(client)).status_code == 403 and erased == []


@pytest.mark.asyncio
async def test_expired_grant_is_neither_verified_nor_consumed(env):
    db, client, fake, erased = env
    me = await _oidc_only(db, client)
    url = await _reauth_url(client, me)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.execute(update(ReauthGrant).values(expires_at=past))
    await db.commit()
    r = await _callback(client, *fake.code_for(url))
    assert r.headers["location"] == "/settings?oidc=failed"
    # verified but expired: consume refuses
    await db.execute(update(ReauthGrant).values(verified_at=past))
    await db.commit()
    assert (await _delete_account(client)).status_code == 403 and erased == []


@pytest.mark.asyncio
async def test_grant_is_bound_to_session_action_and_target(env):
    db, client, fake, erased = env
    me = await _oidc_only(db, client)
    url = await _reauth_url(client, me, action="oidc.unlink")
    r = await _callback(client, *fake.code_for(url))
    assert r.headers["location"] == "/settings?reauth=oidc.unlink"
    # wrong action: an oidc.unlink grant does not delete the account
    assert (await _delete_account(client)).json()["detail"]["error_code"] == "reauth_required"
    # right action on another session of the same person: refused
    await db.execute(update(ReauthGrant).values(action="account.delete"))
    await db.commit()
    await sign_in(db, client, me)
    assert (await _delete_account(client)).json()["detail"]["error_code"] == "reauth_required"
    assert erased == []


@pytest.mark.asyncio
async def test_reauth_callback_from_another_session_does_not_verify(env):
    db, client, fake, _ = env
    me = await _oidc_only(db, client)
    url = await _reauth_url(client, me)
    await sign_in(db, client, me)  # same person, a different session finishes the flow
    r = await _callback(client, *fake.code_for(url))
    assert r.headers["location"] == "/settings?oidc=failed"
    (grant,) = (await db.execute(select(ReauthGrant))).scalars()
    assert grant.verified_at is None


@pytest.mark.asyncio
async def test_reauth_start_refuses_foreign_target_and_unbound_account(env):
    db, client, _, _ = env
    other = await add_person(db, email="o@example.org", password_hash=PWHASH)
    me = await _oidc_only(db, client)
    r = await client.post("/api/me/reauth/start", headers=ORIGIN,
                          json={"action": "account.delete", "target_id": str(other.id)})
    assert r.status_code == 403
    await sign_in(db, client, other)
    r = await client.post("/api/me/reauth/start", headers=ORIGIN,
                          json={"action": "account.delete", "target_id": str(other.id)})
    assert r.status_code == 403
    assert (await db.execute(select(ReauthGrant))).first() is None
    assert me.id != other.id


# --- unlink -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unlink_refuses_the_last_credential_even_with_a_grant(env):
    db, client, fake, _ = env
    me = await _oidc_only(db, client)
    url = await _reauth_url(client, me, action="oidc.unlink")
    await _callback(client, *fake.code_for(url))
    r = await client.request("DELETE", "/api/me/oidc", headers=ORIGIN)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "last_credential"
    assert (await _reload(db, me)).oidc_subject == "sub-1"
    (grant,) = (await db.execute(select(ReauthGrant))).scalars()
    assert grant.used_at is None


@pytest.mark.asyncio
async def test_unlink_needs_a_fresh_grant_then_clears_the_binding(env):
    db, client, fake, _ = env
    me = await add_person(db, email="both@example.org", password_hash=PWHASH, oidc_subject="sub-1")
    await sign_in(db, client, me)
    r = await client.request("DELETE", "/api/me/oidc", headers=ORIGIN)
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "reauth_required"
    url = await _reauth_url(client, me, action="oidc.unlink")
    await _callback(client, *fake.code_for(url))
    r = await client.request("DELETE", "/api/me/oidc", headers=ORIGIN)
    assert r.status_code == 204, r.text
    row = await _reload(db, me)
    assert row.oidc_issuer is None and row.oidc_subject is None
    assert (await db.execute(select(AuditEvent).where(AuditEvent.action == "oidc.unlinked"))).first()
    # the grant was single-use: relinking + unlinking again needs a new one
    row.oidc_issuer, row.oidc_subject = ISSUER, "sub-1"
    await db.commit()
    r = await client.request("DELETE", "/api/me/oidc", headers=ORIGIN)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_api_bearer_cannot_use_the_session_routes(env):
    db, client, _, _ = env
    me = await add_person(db, email="me@example.org", password_hash=PWHASH)
    await sign_in(db, client, me)
    r = await client.post("/api/me/oidc/link", headers={**ORIGIN, "Authorization": "Bearer apl_x_y"})
    assert r.status_code == 401
    assert (await db.execute(select(AuthSession))).first() is not None


# --- consume_grant as an API (1b's caller always passes its own id; the bind must hold anyway)

class _Req:
    def __init__(self, sid, via="session"):
        self.state = type("S", (), {"auth_via": via, "auth_session_id": sid})()


@pytest.mark.asyncio
async def test_consume_grant_is_bound_to_the_target_and_needs_a_session(env):
    import uuid as _uuid

    from applire.auth import reauth

    db, client, fake, _ = env
    me = await _oidc_only(db, client)
    url = await _reauth_url(client, me)
    await _callback(client, *fake.code_for(url))
    (grant,) = (await db.execute(select(ReauthGrant))).scalars()
    sid, uid = grant.session_id, sa_inspect(me).identity[0]
    user = await db.get(User, uid)
    assert not await reauth.consume_grant(db, request=_Req(sid), user=user,
                                          action="account.delete", target_id=_uuid.uuid4())
    assert not await reauth.consume_grant(db, request=_Req(sid, via="bearer"), user=user,
                                          action="account.delete", target_id=uid)
    assert not await reauth.consume_grant(db, request=_Req(None), user=user,
                                          action="account.delete", target_id=uid)
    assert await reauth.consume_grant(db, request=_Req(sid), user=user,
                                      action="account.delete", target_id=uid)
    assert not await reauth.consume_grant(db, request=_Req(sid), user=user,
                                          action="account.delete", target_id=uid)
