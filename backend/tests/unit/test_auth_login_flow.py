# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in, sessions, setup claim and CSRF on the real app (ADR-091 cl. 3, 5, 7, 11–14).

Every test drives ``applire.main.app`` through ``async_client`` (in-memory
SQLite, real routers and dependencies) with the **local** provider swapped in at
the ADR-008 override point — the harness the suite otherwise runs on would
answer every request as the admin and prove nothing about access control.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.harness import STUB_USER_ID, forget_credential_cache
from applire.auth.local import LocalAuthProvider
from applire.auth.passwords import hash_password
from applire.auth.sessions import SESSION_COOKIE, hash_token
from applire.auth.setup import ensure_stub_user, prepare_boot
from applire.auth.throttle import login_throttle, setup_throttle
from applire.main import app as fastapi_app
from applire.models.auth import AuthSession
from applire.models.instance_state import KEY_AUTH_SETUP_TOKEN_HASH
from applire.models.user import User
from applire.services.instance_state import read_state

ORIGIN = {"Origin": "http://test"}
PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _local_provider():
    fastapi_app.dependency_overrides[get_auth_provider] = lambda: LocalAuthProvider()
    login_throttle.clear()
    setup_throttle.clear()
    forget_credential_cache()
    yield
    fastapi_app.dependency_overrides.pop(get_auth_provider, None)
    login_throttle.clear()
    setup_throttle.clear()


async def _user(db: AsyncSession, email: str, *, role: str = "user", password: str | None = PASSWORD,
                disabled: bool = False) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        role=role,
        password_hash=await hash_password(password) if password else None,
        disabled_at=datetime.now(timezone.utc) if disabled else None,
    )
    db.add(user)
    await db.commit()
    return user


async def _login(client: AsyncClient, email: str, password: str = PASSWORD, headers=ORIGIN):
    return await client.post("/api/auth/login", json={"email": email, "password": password}, headers=headers)


# ---------------------------------------------------------------------------
# Login, me, logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_sets_an_httponly_lax_cookie_and_me_answers(async_client, async_db):
    await _user(async_db, "Alice@Example.org")
    resp = await _login(async_client, "alice@example.ORG")  # case-insensitive (cl. 9)
    assert resp.status_code == 204
    cookie = resp.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower() and "Path=/" in cookie
    assert "Secure" not in cookie  # COOKIE_SECURE defaults to false (S-15)
    me = await async_client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["email"] == "Alice@Example.org" and body["role"] == "user"
    assert body["has_password"] is True and body["oidc_linked"] is False


@pytest.mark.asyncio
async def test_without_a_session_every_protected_route_is_401_unauthenticated(async_client):
    for path in ("/api/auth/me", "/api/profile", "/openapi.json", "/docs"):
        resp = await async_client.get(path)
        assert resp.status_code == 401, path
        assert resp.json()["detail"]["error_code"] == "unauthenticated"


@pytest.mark.asyncio
async def test_wrong_password_unknown_email_and_credential_less_account_answer_identically(
    async_client, async_db
):
    await _user(async_db, "bob@example.org")
    await _user(async_db, "pending@example.org", password=None)
    wrong = await _login(async_client, "bob@example.org", "not the password at all")
    unknown = await _login(async_client, "nobody@example.org")
    pending = await _login(async_client, "pending@example.org")
    assert wrong.status_code == unknown.status_code == pending.status_code == 401
    assert wrong.json() == unknown.json() == pending.json()
    assert wrong.json()["detail"]["error_code"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_every_failed_login_spends_exactly_one_scrypt(async_client, async_db, monkeypatch):
    """SF-IAM.6 timing half: unknown email and a credential-less account still
    hash (against the dummy), so they cost what a wrong password costs."""
    import applire.routers.auth as auth_router
    from applire.auth import passwords

    await _user(async_db, "pw@example.org")
    await _user(async_db, "invited@example.org", password=None)
    calls: list[object] = []
    real = passwords.verify_password

    async def counting(raw, stored):
        calls.append(stored)
        return await real(raw, stored)

    monkeypatch.setattr(auth_router, "verify_password", counting)
    for email in ("pw@example.org", "unknown@example.org", "invited@example.org"):
        calls.clear()
        assert (await _login(async_client, email, "wrong wrong wrong")).status_code == 401
        assert len(calls) == 1, email
    assert calls == [None]  # the credential-less account went through the dummy path


@pytest.mark.asyncio
async def test_disabled_account_is_named_only_after_a_correct_password(async_client, async_db):
    """Founder ruling W0B-3."""
    await _user(async_db, "off@example.org", disabled=True)
    wrong = await _login(async_client, "off@example.org", "not the password at all")
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["error_code"] == "invalid_credentials"
    right = await _login(async_client, "off@example.org")
    assert right.status_code == 403
    assert right.json()["detail"]["error_code"] == "account_disabled"
    assert "set-cookie" not in right.headers


@pytest.mark.asyncio
async def test_disabling_a_signed_in_user_acts_on_the_next_request(async_client, async_db):
    user = await _user(async_db, "soon-off@example.org")
    assert (await _login(async_client, "soon-off@example.org")).status_code == 204
    assert (await async_client.get("/api/auth/me")).status_code == 200
    user.disabled_at = datetime.now(timezone.utc)
    await async_db.commit()
    assert (await async_client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_logout_revokes_the_session_server_side(async_client, async_db):
    await _user(async_db, "carol@example.org")
    await _login(async_client, "carol@example.org")
    raw = async_client.cookies.get(SESSION_COOKIE)
    out = await async_client.post("/api/auth/logout", headers=ORIGIN)
    assert out.status_code == 204
    assert 'applire_session=""' in out.headers["set-cookie"] or "Max-Age=0" in out.headers["set-cookie"]
    # Replaying the old cookie value after logout: dead server-side.
    async_client.cookies.set(SESSION_COOKIE, raw)
    assert (await async_client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_login_ignores_a_presented_session(async_client, async_db):
    """Session fixation (ADR-091 cl. 11): a planted cookie is never adopted."""
    await _user(async_db, "dave@example.org")
    planted = "attacker-chosen-value-attacker-chosen-value"
    async_client.cookies.set(SESSION_COOKIE, planted)
    resp = await _login(async_client, "dave@example.org")
    assert resp.status_code == 204
    issued = resp.cookies.get(SESSION_COOKIE)
    assert issued and issued != planted
    rows = (await async_db.execute(select(AuthSession.token_hash))).scalars().all()
    assert hash_token(planted) not in rows


@pytest.mark.asyncio
async def test_a_fresh_login_retires_the_presented_live_session(async_client, async_db):
    await _user(async_db, "erin@example.org")
    await _login(async_client, "erin@example.org")
    first = async_client.cookies.get(SESSION_COOKIE)
    await _login(async_client, "erin@example.org")
    second = async_client.cookies.get(SESSION_COOKIE)
    assert first != second
    async_client.cookies.set(SESSION_COOKIE, first)
    assert (await async_client.get("/api/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_idle_and_absolute_timeouts_end_a_session(async_client, async_db):
    await _user(async_db, "frank@example.org")
    await _login(async_client, "frank@example.org")
    session = (await async_db.execute(select(AuthSession))).scalar_one()
    now = datetime.now(timezone.utc)
    session.last_seen_at = now - timedelta(days=14, minutes=1)
    await async_db.commit()
    assert (await async_client.get("/api/auth/me")).status_code == 401
    session.last_seen_at = now
    session.created_at = now - timedelta(days=90, minutes=1)
    await async_db.commit()
    assert (await async_client.get("/api/auth/me")).status_code == 401
    session.created_at = now - timedelta(days=89)
    await async_db.commit()
    assert (await async_client.get("/api/auth/me")).status_code == 200


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_change_revokes_other_sessions_and_keeps_this_one(async_client, async_db):
    await _user(async_db, "gina@example.org")
    await _login(async_client, "gina@example.org")
    other = async_client.cookies.get(SESSION_COOKIE)
    async_client.cookies.clear()
    await _login(async_client, "gina@example.org")
    mine = async_client.cookies.get(SESSION_COOKIE)

    bad = await async_client.post(
        "/api/auth/password", json={"current": "wrong wrong wrong", "new": "a new long password"},
        headers=ORIGIN,
    )
    assert bad.status_code == 403
    assert bad.json()["detail"]["error_code"] == "invalid_credentials"
    same_as_email = await async_client.post(
        "/api/auth/password", json={"current": PASSWORD, "new": "gina@example.org"}, headers=ORIGIN,
    )
    assert same_as_email.status_code == 422
    assert same_as_email.json()["detail"]["error_code"] == "password_policy"

    ok = await async_client.post(
        "/api/auth/password", json={"current": PASSWORD, "new": "a new long password"},
        headers=ORIGIN,
    )
    assert ok.status_code == 204
    assert (await async_client.get("/api/auth/me")).status_code == 200
    async_client.cookies.set(SESSION_COOKIE, other)
    assert (await async_client.get("/api/auth/me")).status_code == 401
    async_client.cookies.set(SESSION_COOKIE, mine)
    assert (await _login(async_client, "gina@example.org", "a new long password")).status_code == 204


# ---------------------------------------------------------------------------
# CSRF (cl. 12) on the real app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cookie_authenticated_post_without_origin_is_403(async_client, async_db):
    await _user(async_db, "hank@example.org")
    await _login(async_client, "hank@example.org")
    resp = await async_client.post("/api/auth/logout")
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["error_code"] == "origin_mismatch"
    assert "forward the Host header" in detail["message"] and "APPLIRE_BASE_URL" in detail["message"]
    evil = await async_client.post("/api/auth/logout", headers={"Origin": "http://evil.example"})
    assert evil.status_code == 403
    assert (await async_client.post("/api/auth/logout", headers=ORIGIN)).status_code == 204


@pytest.mark.asyncio
async def test_login_without_origin_is_403_login_csrf(async_client, async_db):
    await _user(async_db, "ivy@example.org")
    resp = await _login(async_client, "ivy@example.org", headers={})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "origin_mismatch"


@pytest.mark.asyncio
async def test_an_authorization_header_means_the_cookie_is_never_read(async_client, async_db):
    """cl. 12: an invalid bearer next to a valid cookie is 401 — no cookie fallback."""
    await _user(async_db, "jack@example.org")
    await _login(async_client, "jack@example.org")
    assert (await async_client.get("/api/auth/me")).status_code == 200
    resp = await async_client.get("/api/auth/me", headers={"Authorization": "Bearer apl_bogus_x"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_require_session_user_refuses_an_api_bearer_with_403(async_client, async_db, monkeypatch):
    user = await _user(async_db, "kim@example.org")

    async def fake_bearer(request, db, scope="api"):
        return await db.get(User, user.id)

    monkeypatch.setattr("applire.auth._seams.bearer_user", fake_bearer)
    headers = {"Authorization": "Bearer apl_x"}
    assert (await async_client.get("/api/auth/me", headers=headers)).status_code == 200
    # Bearer POST needs no Origin (valid api bearer is the CSRF exemption) …
    resp = await async_client.post("/api/auth/logout", headers=headers)
    # … but credential management needs a session.
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "forbidden"


@pytest.mark.asyncio
async def test_require_admin_refuses_a_user_and_admits_an_admin(async_client, async_db):
    await _user(async_db, "user@example.org")
    await _user(async_db, "admin@example.org", role="admin")
    await _login(async_client, "user@example.org")
    resp = await async_client.post("/api/settings/upgrade-notice/dismiss", headers=ORIGIN)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "forbidden"
    async_client.cookies.clear()
    await _login(async_client, "admin@example.org")
    assert (await async_client.post("/api/settings/upgrade-notice/dismiss", headers=ORIGIN)).status_code != 403


# ---------------------------------------------------------------------------
# Throttle at the door (RD-8): known and unknown emails alike, never a refusal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_throttle_delays_known_and_unknown_emails_alike_and_never_refuses(
    async_client, async_db, monkeypatch
):
    await _user(async_db, "lena@example.org")
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(login_throttle, "_sleep", fake_sleep)
    for email in ("lena@example.org", "ghost@example.org"):
        slept.clear()
        flags = []
        for _ in range(7):
            resp = await _login(async_client, email, "wrong wrong wrong")
            assert resp.status_code == 401
            flags.append(resp.headers.get("X-Applire-Throttled"))
        assert slept == [1.0, 2.0], email  # attempts 6 and 7 waited; 1–5 were free
        # CONTRACT-CHANGE 1a: the delayed 401 says so — same for a known and an unknown email.
        assert flags == [None] * 5 + ["1", "1"], email
    slept.clear()
    right = await _login(async_client, "lena@example.org")
    assert right.status_code == 204  # a correct password on a hot key: delayed, not refused
    assert slept == [4.0]
    slept.clear()
    assert (await _login(async_client, "lena@example.org", "wrong wrong wrong")).status_code == 401
    assert slept == []  # success cleared the key


# ---------------------------------------------------------------------------
# Setup claim (cl. 14) — web route
# ---------------------------------------------------------------------------


async def _boot(db: AsyncSession) -> str:
    await ensure_stub_user(db)
    code = await prepare_boot(db)
    await db.commit()
    assert code is not None
    return code


@pytest.mark.asyncio
async def test_setup_claims_the_stub_in_place_and_signs_in(async_client, async_db, monkeypatch):
    monkeypatch.setattr("applire.routers.setup.settings.auth_harness", False)
    code = await _boot(async_db)
    state = await async_client.get("/api/auth/state")
    assert state.json()["setup_required"] is True and state.json()["harness"] is False

    body = {"setup_token": code.lower().replace("-", " "), "email": "Owner@Example.org",
            "password": PASSWORD}
    resp = await async_client.post("/api/setup", json=body, headers=ORIGIN)
    assert resp.status_code == 204, resp.text
    assert SESSION_COOKIE in resp.headers["set-cookie"]

    stub = await async_db.get(User, STUB_USER_ID)
    await async_db.refresh(stub)
    assert stub.email == "Owner@Example.org" and stub.role == "admin"
    assert stub.password_hash and stub.email_verified_at is not None
    assert await read_state(async_db, KEY_AUTH_SETUP_TOKEN_HASH) is None
    me = await async_client.get("/api/auth/me")
    assert me.json()["id"] == str(STUB_USER_ID) and me.json()["role"] == "admin"
    assert (await async_client.get("/api/auth/state")).json()["setup_required"] is False

    again = await async_client.post("/api/setup", json=body, headers=ORIGIN)
    assert again.status_code == 409
    assert again.json()["detail"]["error_code"] == "setup_done"


@pytest.mark.asyncio
async def test_setup_refuses_a_wrong_or_stale_code(async_client, async_db, monkeypatch):
    monkeypatch.setattr("applire.routers.setup.settings.auth_harness", False)
    first = await _boot(async_db)
    second = await _boot(async_db)  # a restart: MD-1 rotates the code
    assert first != second
    for token in (first, "AAAA-AAAA-AAAA-AAAA-AAAA-AAAA"):
        resp = await async_client.post(
            "/api/setup", json={"setup_token": token, "email": "o@example.org", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error_code"] == "invalid_setup_token"
    stub = await async_db.get(User, STUB_USER_ID)
    await async_db.refresh(stub)
    assert stub.password_hash is None and stub.role == "user"


@pytest.mark.asyncio
async def test_setup_password_policy_and_origin(async_client, async_db, monkeypatch):
    monkeypatch.setattr("applire.routers.setup.settings.auth_harness", False)
    code = await _boot(async_db)
    same = await async_client.post(
        "/api/setup", json={"setup_token": code, "email": "longaddress@example.org",
                            "password": "longaddress@example.org"}, headers=ORIGIN,
    )
    assert same.status_code == 422 and same.json()["detail"]["error_code"] == "password_policy"
    short = await async_client.post(
        "/api/setup", json={"setup_token": code, "email": "o@example.org", "password": "short"},
        headers=ORIGIN,
    )
    assert short.status_code == 422
    no_origin = await async_client.post(
        "/api/setup", json={"setup_token": code, "email": "o@example.org", "password": PASSWORD},
    )
    assert no_origin.status_code == 403
    assert no_origin.json()["detail"]["error_code"] == "origin_mismatch"


@pytest.mark.asyncio
async def test_setup_is_refused_while_the_harness_is_on(async_client, async_db, monkeypatch):
    monkeypatch.setattr("applire.routers.setup.settings.auth_harness", True)
    code = await _boot(async_db)
    resp = await async_client.post(
        "/api/setup", json={"setup_token": code, "email": "o@example.org", "password": PASSWORD},
        headers=ORIGIN,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "harness_active"


@pytest.mark.asyncio
async def test_setup_bucket_is_separate_from_login(async_client, async_db, monkeypatch):
    monkeypatch.setattr("applire.routers.setup.settings.auth_harness", False)
    code = await _boot(async_db)
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(login_throttle, "_sleep", fake_sleep)
    monkeypatch.setattr(setup_throttle, "_sleep", fake_sleep)
    for _ in range(8):
        await _login(async_client, "x@example.org", "wrong wrong wrong")
    slept.clear()
    resp = await async_client.post(
        "/api/setup", json={"setup_token": code, "email": "o@example.org", "password": PASSWORD},
        headers=ORIGIN,
    )
    assert resp.status_code == 204 and slept == []
