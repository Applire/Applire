# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strawberry build 1, w4-fix-id — the guards for rulings MD-32…MD-36 (+ MD-38/39)
beyond the adversarial proofs in ``test_adv_identity.py``.

* MD-32 — a mailed link is built from ``APPLIRE_BASE_URL`` only; without it no
  mail is sent (forgot, invite, reinvite); a link SHOWN to a signed-in admin may
  use that admin's browser origin, a bearer/anonymous request never gets one.
* MD-34 — every admin route is classified session-only or bearer-allowed; the
  walk pins the table and each session-only route refuses an admin ``api`` bearer.
* MD-35 — attempts on one key run one at a time (wait → verify → record), and the
  login throttle is keyed on the account the SQL lookup resolved.
* observations — ``claim_stub`` refuses a tombstoned stub; retention never
  tombstones the unclaimed stub; ``reset-password`` refuses on an unclaimed instance.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from applire.auth import get_auth_provider
from applire.auth.deps import require_admin, require_admin_session
from applire.auth.deps_links import admin_or_probe
from applire.auth.harness import STUB_USER_ID, forget_credential_cache
from applire.auth.local import LocalAuthProvider
from applire.auth.passwords import hash_password
from applire.auth.throttle import (
    FREE_FAILURES,
    Throttle,
    ThrottleSaturated,
    login_throttle,
    setup_throttle,
)
from applire.auth.tokens import create_token
from applire.config import settings
from applire.main import app as fastapi_app
from applire.models.user import User
from applire.services.admin import links

PASSWORD = "correct horse battery staple"
ORIGIN = {"Origin": "http://test"}


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


async def _user(db: AsyncSession, email: str, *, role: str = "user", password: bool = True) -> User:
    user = User(id=uuid.uuid4(), email=email, role=role,
                password_hash=await hash_password(PASSWORD) if password else None)
    db.add(user)
    await db.commit()
    return user


async def _admin_session(client, db) -> User:
    admin = await _user(db, f"admin-{uuid.uuid4().hex[:6]}@example.org", role="admin")
    resp = await client.post("/api/auth/login", json={"email": admin.email, "password": PASSWORD},
                             headers=ORIGIN)
    assert resp.status_code == 204, resp.text
    return admin


# ---------------------------------------------------------------------------
# MD-32 — links that leave the browser
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_md32_forgot_mail_uses_the_configured_base_url_not_the_host(async_client, async_db, monkeypatch):
    from applire.routers import auth_links as router_mod
    from applire.services import mail

    await _user(async_db, "dora@example.org")
    captured: list[tuple] = []
    monkeypatch.setattr(mail, "smtp_enabled", lambda: True)
    monkeypatch.setattr(settings, "applire_base_url", "https://applire.example.org/")
    monkeypatch.setattr(router_mod, "_send_forgot_mail", lambda *a, **k: captured.append(a))
    # With APPLIRE_BASE_URL set the origin check already refuses a foreign Host
    # (anti-rebinding) — so prove the refusal, then the mail origin on a legal Host.
    evil = await async_client.post("/api/auth/forgot", json={"email": "dora@example.org"},
                                   headers={"Host": "evil.example", "Origin": "http://evil.example"})
    assert evil.status_code == 403 and captured == []
    resp = await async_client.post("/api/auth/forgot", json={"email": "dora@example.org"},
                                   headers={"Host": "localhost", "Origin": "https://applire.example.org"})
    assert resp.status_code == 202, resp.text
    assert captured[0][1] == "https://applire.example.org"


@pytest.mark.asyncio
async def test_md32_forgot_without_base_url_sends_nothing_and_warns(caplog):
    """The background task gets ``""`` and returns before touching the DB or SMTP."""
    from applire.routers import auth_links as router_mod

    def exploding_factory():  # any DB use would be a lookup → a mail
        raise AssertionError("the forgot task looked the account up without a base URL")

    with caplog.at_level(logging.WARNING, logger=router_mod.logger.name):
        await router_mod._send_forgot_mail("dora@example.org", "", session_factory=exploding_factory)
    assert any("APPLIRE_BASE_URL" in r.getMessage() for r in caplog.records)
    assert not any("dora@example.org" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_md32_admin_invite_without_base_url_is_not_mailed_and_says_why(async_client, async_db, monkeypatch):
    from applire.services import mail

    sent: list = []

    async def fake_send(*a, **k):
        sent.append(k)
        return True

    monkeypatch.setattr(mail, "smtp_enabled", lambda: True)
    monkeypatch.setattr(mail, "send_link_mail", fake_send)
    monkeypatch.setattr(settings, "applire_base_url", links.SHIPPED_DEFAULT_BASE_URL)
    await _admin_session(async_client, async_db)
    resp = await async_client.post("/api/admin/users", json={"email": "neu@example.org"}, headers=ORIGIN)
    assert resp.status_code == 201, resp.text
    link = resp.json()["link"]
    assert sent == []
    assert (link["mailed"], link["mail_failed"], link["mail_failed_reason"]) == (False, True, "base_url_unset")
    # Shown to the signed-in admin: the admin's own browser origin is fine (MD-32).
    assert link["url"].startswith("http://test/invite#")


@pytest.mark.asyncio
async def test_md32_admin_invite_mail_link_uses_base_url_even_on_another_host(async_client, async_db, monkeypatch):
    from applire.services import mail

    sent: list = []

    async def fake_send(purpose, **k):
        sent.append(k)
        return True

    monkeypatch.setattr(mail, "smtp_enabled", lambda: True)
    monkeypatch.setattr(mail, "send_link_mail", fake_send)
    await _admin_session(async_client, async_db)
    monkeypatch.setattr(settings, "applire_base_url", "https://applire.example.org")
    # A legal request on the configured address (the origin check refuses others).
    via_base = {"Host": "localhost", "Origin": "https://applire.example.org"}
    resp = await async_client.post("/api/admin/users", json={"email": "neu2@example.org"}, headers=via_base)
    assert resp.status_code == 201, resp.text
    assert sent and sent[0]["link"].startswith("https://applire.example.org/invite#")
    assert sent[0]["instance_url"] == "https://applire.example.org"
    assert resp.json()["link"]["mail_failed_reason"] is None

    async def failing_send(purpose, **k):
        return False

    monkeypatch.setattr(mail, "send_link_mail", failing_send)
    resp = await async_client.post("/api/admin/users", json={"email": "neu3@example.org"}, headers=via_base)
    assert resp.json()["link"]["mail_failed_reason"] == "send_failed"


def _request(headers: dict[str, str], auth_via: str | None):
    from starlette.requests import Request

    scope = {"type": "http", "method": "POST", "path": "/x", "scheme": "http",
             "server": ("backend", 8000), "query_string": b"", "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    req = Request(scope)
    if auth_via is not None:
        req.state.auth_via = auth_via
    return req


@pytest.mark.parametrize("signed_in,via,expected", [
    (True, "session", "http://nas:8080"),
    (True, "harness", "http://nas:8080"),
    (True, "bearer", ""),
    (False, "session", ""),
    (False, None, ""),
])
def test_md32_shown_origin_only_for_a_signed_in_browser(signed_in, via, expected, monkeypatch):
    monkeypatch.setattr(settings, "applire_base_url", links.SHIPPED_DEFAULT_BASE_URL)
    req = _request({"Host": "nas:8080", "Origin": "http://nas:8080"}, via)
    assert links.request_origin(req, signed_in=signed_in) == expected


def test_md32_mail_origin_is_only_the_configured_base_url(monkeypatch):
    monkeypatch.setattr(settings, "applire_base_url", links.SHIPPED_DEFAULT_BASE_URL)
    assert links.mail_origin() is None
    monkeypatch.setattr(settings, "applire_base_url", "https://a.example/")
    assert links.mail_origin() == "https://a.example"


def test_md32_startup_warning_when_smtp_is_on_without_base_url(monkeypatch):
    from applire.services import mail

    monkeypatch.setattr(settings, "applire_base_url", links.SHIPPED_DEFAULT_BASE_URL)
    monkeypatch.setattr(mail, "smtp_enabled", lambda: True)
    assert "APPLIRE_BASE_URL" in (links.mail_without_base_url_warning() or "")
    monkeypatch.setattr(mail, "smtp_enabled", lambda: False)
    assert links.mail_without_base_url_warning() is None
    monkeypatch.setattr(mail, "smtp_enabled", lambda: True)
    monkeypatch.setattr(settings, "applire_base_url", "https://a.example")
    assert links.mail_without_base_url_warning() is None


# ---------------------------------------------------------------------------
# MD-34 — admin route classification (session-only vs bearer-allowed)
# ---------------------------------------------------------------------------

#: The pinned table. A new admin route fails the walk until it is classified here.
ADMIN_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/admin/users"): "bearer",
    ("POST", "/api/admin/users"): "session",               # creates an account + invite link
    ("PATCH", "/api/admin/users/{user_id}"): "session",    # role change (and disable)
    ("DELETE", "/api/admin/users/{user_id}"): "bearer",
    ("POST", "/api/admin/users/{user_id}/reinvite"): "session",
    ("POST", "/api/admin/users/{user_id}/reset-link"): "session",
    ("POST", "/api/admin/users/{user_id}/revoke-tokens"): "session",
    ("GET", "/api/admin/probe-tokens"): "session",
    ("POST", "/api/admin/probe-tokens"): "session",
    ("DELETE", "/api/admin/probe-tokens/{token_id}"): "session",
    ("GET", "/api/admin/color-schemes"): "bearer",
    ("POST", "/api/admin/color-schemes/preview"): "bearer",
    ("POST", "/api/admin/color-schemes"): "bearer",
    ("PATCH", "/api/admin/color-schemes/{scheme_id}/activate"): "bearer",
    ("DELETE", "/api/admin/color-schemes/{scheme_id}"): "bearer",
    ("POST", "/api/settings/upgrade-notice/dismiss"): "bearer",
    ("GET", "/api/ops/health"): "bearer",                  # admin_or_probe (probe tokens by design)
}


def _dep_calls(route: APIRoute) -> set:
    seen: set = set()

    def walk(dep):
        for sub in dep.dependencies:
            seen.add(sub.call)
            walk(sub)

    walk(route.dependant)
    return seen


def _walk_admin_routes() -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for route in fastapi_app.routes:
        if not isinstance(route, APIRoute):
            continue
        calls = _dep_calls(route)
        if not calls & {require_admin, admin_or_probe}:
            continue
        cls = "session" if require_admin_session in calls else "bearer"
        for method in route.methods:
            found[(method, route.path)] = cls
    return found


def test_md34_admin_route_walk_pins_the_classification():
    assert _walk_admin_routes() == ADMIN_ROUTES


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", sorted(k for k, v in ADMIN_ROUTES.items() if v == "session"))
async def test_md34_session_only_admin_routes_refuse_an_admin_api_bearer(method, path, async_client, async_db):
    admin = await _user(async_db, f"own-{uuid.uuid4().hex[:6]}@example.org", role="admin")
    target = await _user(async_db, f"t-{uuid.uuid4().hex[:6]}@example.org")
    _row, raw = await create_token(async_db, user_id=admin.id, scope="api", name="ci")
    await async_db.commit()
    url = path.replace("{user_id}", str(target.id)).replace("{token_id}", str(uuid.uuid4()))
    body = {"email": "x@example.org", "role": "admin"} if (method, path) == ("POST", "/api/admin/users") else (
        {"name": "uptime"} if (method, path) == ("POST", "/api/admin/probe-tokens") else (
            {"role": "admin"} if method == "PATCH" else None))
    resp = await async_client.request(method, url, json=body, headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 403, (method, path, resp.status_code, resp.text)
    assert resp.json()["detail"]["error_code"] == "forbidden"


@pytest.mark.asyncio
async def test_md34_a_session_still_manages_credentials_and_a_bearer_still_lists(async_client, async_db):
    admin = await _admin_session(async_client, async_db)
    resp = await async_client.post("/api/admin/users", json={"email": "s@example.org"}, headers=ORIGIN)
    assert resp.status_code == 201
    _row, raw = await create_token(async_db, user_id=admin.id, scope="api", name="ci")
    await async_db.commit()
    listed = await async_client.get("/api/admin/users", headers={"Authorization": f"Bearer {raw}"})
    assert listed.status_code == 200


# ---------------------------------------------------------------------------
# MD-35 — per-key serialisation and the one normalisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_md35_attempts_on_a_cold_key_are_serialised_so_no_free_burst():
    """8 concurrent attempts on a key with 0 failures: without the lock all 8 pass
    ``wait`` before any failure is recorded (8 free guesses); with it, attempts 6–8
    see the earlier failures and are delayed."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        await asyncio.sleep(0)

    t = Throttle(sleep=fake_sleep)
    key = ("victim@example.org", "203.0.113.9")

    async def one_attempt():
        async with t.attempt(key):
            await t.wait(key)
            await asyncio.sleep(0)  # the password check yields (scrypt in a thread)
            t.record_failure(key)

    await asyncio.gather(*(one_attempt() for _ in range(FREE_FAILURES + 3)))
    assert len(slept) == 3, slept
    assert not t._locks, "the per-key lock must be dropped once nobody holds it"


@pytest.mark.asyncio
async def test_md35_login_route_holds_the_key_lock_across_wait_verify_record(async_client, async_db, monkeypatch):
    await _user(async_db, "lena@example.org")
    events: list[str] = []
    real_attempt, real_wait, real_fail = login_throttle.attempt, login_throttle.wait, login_throttle.record_failure
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def spy_attempt(key):
        events.append("enter")
        try:
            async with real_attempt(key):
                yield
        finally:
            events.append("exit")

    async def spy_wait(key):
        events.append("wait")
        return await real_wait(key)

    def spy_fail(key):
        events.append("fail")
        real_fail(key)

    monkeypatch.setattr(login_throttle, "attempt", spy_attempt)
    monkeypatch.setattr(login_throttle, "wait", spy_wait)
    monkeypatch.setattr(login_throttle, "record_failure", spy_fail)
    resp = await async_client.post("/api/auth/login", json={"email": "lena@example.org", "password": "nope nope nope"},
                                   headers=ORIGIN)
    assert resp.status_code == 401
    assert events == ["enter", "wait", "fail", "exit"]


@pytest.mark.asyncio
async def test_md35_bound_refuses_unchecked_beyond_the_queue_bound():
    """Ruling fix-id-1 = B (``MAX_QUEUE_SECONDS = 30.0``): an attempt that would
    queue past the bound is held for the bound and refused. The throttle runs on
    the MODULE value (no per-instance override), so the shipped constant is what
    this test pins."""
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        await asyncio.sleep(0)

    assert login_throttle._bound() == 30.0 and setup_throttle._bound() == 30.0
    t = Throttle(sleep=fake_sleep)
    key = ("victim@example.org", "203.0.113.9")
    for _ in range(10):
        t.record_failure(key)  # hot: 30 s
    verified: list[int] = []

    async def one_attempt(i):
        try:
            async with t.attempt(key):
                await t.wait(key)
                verified.append(i)
                t.record_failure(key)
        except ThrottleSaturated:
            return "refused"
        return "checked"

    results = await asyncio.gather(*(one_attempt(i) for i in range(6)))
    assert results.count("checked") == 2 and results.count("refused") == 4, results
    assert max(slept) <= 30.0


@pytest.mark.asyncio
async def test_md35_login_saturated_answers_the_throttled_401(async_client, async_db, monkeypatch):
    async def saturated(key):
        raise ThrottleSaturated()

    monkeypatch.setattr(login_throttle, "wait", saturated)
    resp = await async_client.post("/api/auth/login", json={"email": "x@example.org", "password": PASSWORD},
                                   headers=ORIGIN)
    assert resp.status_code == 401
    assert resp.headers.get("X-Applire-Throttled") == "1"


@pytest.mark.asyncio
async def test_md35_spellings_the_lookup_merges_share_one_throttle_key(async_client, async_db, monkeypatch):
    """One normalisation: the key comes from the account the SQL ``lower()``
    lookup resolved. Simulate Postgres/glibc, where ``lower('İ')`` is ``'i'`` but
    Python's ``casefold`` gives ``'i̇'``: a spelling the lookup maps to the same
    account must not open a fresh bucket."""
    from applire.routers import auth as auth_router

    lena = await _user(async_db, "lina@example.org")
    real_find = auth_router.find_user_by_email

    async def postgres_like_find(db, email):
        if email.strip() in ("lİna@example.org", "lina@example.org"):
            return await real_find(db, "lina@example.org")
        return await real_find(db, email)

    monkeypatch.setattr(auth_router, "find_user_by_email", postgres_like_find)
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(login_throttle, "_sleep", fake_sleep)
    for _ in range(FREE_FAILURES):
        r = await async_client.post("/api/auth/login", json={"email": "lina@example.org", "password": "wrong wrong wrong"},
                                    headers=ORIGIN)
        assert r.status_code == 401
    assert slept == []
    r = await async_client.post("/api/auth/login", json={"email": "lİna@example.org", "password": "wrong wrong wrong"},
                                headers=ORIGIN)
    assert r.status_code == 401
    assert slept == [1.0], "a spelling variant of a known account got its own bucket"
    assert lena.id is not None


# ---------------------------------------------------------------------------
# Observations — tombstoned stub, retention, reset-password on an unclaimed instance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_stub_refuses_a_tombstoned_stub(async_db):
    from applire.auth.setup import claim_stub, ensure_stub_user

    await ensure_stub_user(async_db)
    await async_db.execute(update(User).where(User.id == STUB_USER_ID)
                           .values(deleted_at=datetime.now(timezone.utc)))
    await async_db.commit()
    claimed = await claim_stub(async_db, email="me@example.org", password_hash="x")
    assert claimed is False


@pytest_asyncio.fixture
async def ret_factory():
    from applire import ownership
    from applire.db.session import Base

    eng = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(eng, expire_on_commit=False)
    await eng.dispose()


@pytest.mark.asyncio
@pytest.mark.no_owner_context
async def test_retention_never_tombstones_the_unclaimed_stub(ret_factory):
    from applire import ownership
    from applire.retention import worker

    old = datetime.now(timezone.utc) - timedelta(days=worker._INACTIVITY_TTL_DAYS + 30)
    with ownership.unscoped("retention"):
        async with ret_factory() as s:
            s.add(User(id=STUB_USER_ID, email="stub@localhost", role="user", created_at=old))
            other = User(id=uuid.uuid4(), email="o@example.org", role="user", created_at=old)
            s.add(other)
            await s.commit()
            n = await worker._tombstone_inactive_users(s)
        async with ret_factory() as s:
            rows = {u.id: u.deleted_at for u in (await s.execute(select(User))).scalars()}
    assert rows[STUB_USER_ID] is None
    assert rows[other.id] is not None and n == 1


@pytest.mark.asyncio
@pytest.mark.no_owner_context
async def test_retention_treats_a_claimed_stub_like_any_account(ret_factory):
    from applire import ownership
    from applire.retention import worker

    old = datetime.now(timezone.utc) - timedelta(days=worker._INACTIVITY_TTL_DAYS + 30)
    with ownership.unscoped("retention"):
        async with ret_factory() as s:
            s.add(User(id=STUB_USER_ID, email="me@example.org", role="user", created_at=old,
                       password_hash="scrypt$x"))
            await s.commit()
            assert await worker._tombstone_inactive_users(s) == 1


@pytest.mark.asyncio
async def test_reset_password_cli_refuses_on_an_unclaimed_instance(async_db):
    from applire.admin import reset
    from applire.auth.setup import ensure_stub_user

    await ensure_stub_user(async_db)
    await async_db.commit()
    with pytest.raises(reset.ResetRefused, match="create-admin"):
        await reset.reset_password(async_db, "stub@localhost", PASSWORD)
    stub = (await async_db.execute(select(User).where(User.id == STUB_USER_ID))).scalar_one()
    assert stub.password_hash is None
