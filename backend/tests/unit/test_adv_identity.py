# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial review of identity (ADR-091), Strawberry build 1 — w4-adv-identity.

Every test here asserts the SECURE behaviour and FAILS on the tree it was written
against (011465c4): each one is the proof of a finding, not a regression guard
for behaviour that already holds. See
``Documents/Runs/Strawberry/build-1/w4-adv-identity/findings.md``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import time
import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.harness import forget_credential_cache
from applire.auth.local import LocalAuthProvider
from applire.auth.passwords import hash_password
from applire.auth.throttle import login_key, login_throttle, setup_throttle
from applire.auth.tokens import create_token
from applire.main import app as fastapi_app
from applire.models.user import User
from applire.services.admin import links

REPO = Path(__file__).resolve().parents[3]
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


async def _user(db: AsyncSession, email: str, *, role: str = "user") -> User:
    user = User(id=uuid.uuid4(), email=email, role=role,
                password_hash=await hash_password(PASSWORD))
    db.add(user)
    await db.commit()
    return user


# ---------------------------------------------------------------------------
# adv-id-1 — password-reset link poisoning through the Host header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adv_identity_1_forgot_mail_link_ignores_attacker_host(async_client, async_db, monkeypatch):
    """``POST /api/auth/forgot`` is public. With the shipped default
    ``APPLIRE_BASE_URL`` (most installs), the mailed reset link is built from the
    request's own ``Host``/``Origin`` (``links.request_origin``). The origin check
    only demands Origin == Host, which an attacker sending the request directly
    satisfies with ``Host: evil.example`` + ``Origin: http://evil.example``; nginx
    forwards ``$http_host`` and has ``server_name _``. The victim then receives a
    genuine mail from the instance whose link is ``http://evil.example/reset#<token>``
    — one click hands the attacker the reset token (account takeover, incl. the admin).

    Secure behaviour: a link mailed in response to an UNAUTHENTICATED request must
    never point at a host the requester chose.
    """
    from applire.routers import auth_links as router_mod
    from applire.services import mail

    await _user(async_db, "victim@example.org", role="admin")
    captured: list[tuple] = []
    monkeypatch.setattr(mail, "smtp_enabled", lambda: True)
    monkeypatch.setattr(router_mod, "_send_forgot_mail", lambda *a, **k: captured.append(a))

    resp = await async_client.post(
        "/api/auth/forgot",
        json={"email": "victim@example.org"},
        headers={"Host": "evil.example", "Origin": "http://evil.example"},
    )
    assert resp.status_code == 202
    assert captured, "the background mail task was not scheduled"
    _email, origin = captured[0][:2]
    assert "evil.example" not in origin, (
        f"the reset mail for victim@example.org would link to {origin}/reset#<token>"
    )


def test_adv_identity_1b_request_origin_trusts_any_host_header():
    """Unit form of adv-id-1: ``request_origin`` with the shipped default base URL
    returns whatever ``Host`` the request carried."""
    from starlette.requests import Request

    scope = {
        "type": "http", "method": "POST", "path": "/api/auth/forgot", "scheme": "http",
        "server": ("backend", 8000), "query_string": b"", "root_path": "",
        "headers": [(b"host", b"evil.example"), (b"origin", b"http://evil.example")],
    }
    origin = links.request_origin(Request(scope))
    assert "evil.example" not in origin


# ---------------------------------------------------------------------------
# adv-id-2 — an admin's API bearer manages credentials (session-only rule bypassed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adv_identity_2_admin_api_token_cannot_reset_its_own_password(async_client, async_db):
    """ADR-091 cl. 17: an ``api`` bearer works everywhere EXCEPT credential
    management (``/api/auth/password``, ``/api/me/*tokens*`` …, session only) so a
    leaked token cannot make itself permanent or take over the account. But an
    admin's ``api`` token can ``POST /api/admin/users/{own id}/reset-link``, get
    the reset URL in the response, redeem it, and set the account's password —
    signing the real owner out everywhere. (It can equally ``POST /api/admin/users
    {role: admin}`` and redeem the returned invite: a permanent second admin.)

    Secure behaviour: issuing a set-password link through a bearer is refused.
    """
    admin = await _user(async_db, "owner@example.org", role="admin")
    _row, raw = await create_token(async_db, user_id=admin.id, scope="api", name="ci")
    await async_db.commit()

    resp = await async_client.post(
        f"/api/admin/users/{admin.id}/reset-link",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code in (401, 403), (
        f"an api bearer obtained a set-password link: {resp.status_code} "
        f"{resp.json().get('purpose') if resp.status_code == 200 else resp.text}"
    )


@pytest.mark.asyncio
async def test_adv_identity_2b_admin_api_token_cannot_mint_an_admin_invite(async_client, async_db):
    admin = await _user(async_db, "owner2@example.org", role="admin")
    _row, raw = await create_token(async_db, user_id=admin.id, scope="api", name="ci")
    await async_db.commit()
    resp = await async_client.post(
        "/api/admin/users",
        json={"email": "persist@evil.example", "role": "admin"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code in (401, 403), (
        f"an api bearer created an admin account + invite link: {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# adv-id-3 — the login throttle delays each request, it does not rate-limit a key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adv_identity_3_hot_key_concurrent_attempts_are_serialised():
    """RD-8: after 5 failures in 15 min every further attempt on the key waits
    1 s doubling to 30 s. ``Throttle.wait`` sleeps the CURRENT delay and returns;
    nothing serialises the key, so N parallel attempts all wait the same delay and
    then all run — the delay bounds latency, not the guess rate. With 30 s and
    1 000 concurrent requests an attacker keeps ~33 guesses/s on one key.

    Secure behaviour: on a hot key, K concurrent attempts are spaced, not batched —
    at most one guess per delay window reaches the password check. Under ruling
    fix-id-1 = B (``MAX_QUEUE_SECONDS = 30.0``) an attempt whose slot lies past the
    bound is refused unchecked (``ThrottleSaturated``) instead of queueing; the
    option-A expectation "the last of 20 waits longer than one window" is retired.
    """
    from applire.auth.throttle import Throttle, ThrottleSaturated

    slept: list[float] = []
    clock = [1000.0]

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += 0  # no time passes for the attacker's parallel requests

    t = Throttle(clock=lambda: clock[0], sleep=fake_sleep)
    key = ("victim@example.org", "203.0.113.9")
    for _ in range(10):
        t.record_failure(key)  # key hot: 10 failures -> 30 s cap
    # 20 attempts launched together — what an attacker with 20 connections does.
    results = await asyncio.gather(*(t.wait(key) for _ in range(20)), return_exceptions=True)
    # Unspaced, every one is released after a single 30 s wait: 20 guesses in 30 s.
    released = sorted(r for r in results if not isinstance(r, BaseException))
    refused = [r for r in results if isinstance(r, ThrottleSaturated)]
    assert len(released) + len(refused) == 20, results
    assert sum(1 for w in released if w <= 30.0) <= 1, (
        f"{len(released)} of 20 parallel attempts were released after one delay "
        f"window ({released}) — they run batched, not spaced"
    )
    assert all(b - a >= 30.0 for a, b in zip(released, released[1:])), released
    assert max(slept) <= 30.0, "a refused attempt is held at most the queue bound"


# ---------------------------------------------------------------------------
# adv-id-4 — the client half of the throttle key is spoofable from the LAN
# ---------------------------------------------------------------------------


def _nginx_real_ip(conf: str, peer: str, xff: str) -> str:
    """nginx ``real_ip`` with ``real_ip_recursive on`` (ngx_http_realip_module)."""
    trusted = [ipaddress.ip_network(m) for m in re.findall(r"set_real_ip_from\s+([^;]+);", conf)]
    assert re.search(r"real_ip_header\s+X-Forwarded-For;", conf)
    assert re.search(r"real_ip_recursive\s+on;", conf)

    def is_trusted(addr: str) -> bool:
        ip = ipaddress.ip_address(addr)
        return any(ip.version == n.version and ip in n for n in trusted)

    if not is_trusted(peer):
        return peer
    chain = [h.strip() for h in xff.split(",") if h.strip()]
    addr = peer
    for hop in reversed(chain):
        addr = hop
        if not is_trusted(hop):
            break
    return addr


@pytest.mark.parametrize("conf_name", ["self-hosted.conf"])
def test_adv_identity_4_lan_client_cannot_choose_its_throttle_identity(conf_name):
    """ADR-091 cl. 13 keys the throttle on (email, client) with client =
    ``X-Real-IP`` after nginx ``real_ip``; ``set_real_ip_from`` lists all of RFC
    1918. A self-hosted household's clients ARE on RFC 1918 addresses, so any LAN
    device is a "trusted proxy": it sends ``X-Forwarded-For: <anything>`` and nginx
    sets ``$remote_addr`` — and so ``X-Real-IP``, the throttle key — to that value.
    One new XFF value per attempt and the key is never hot: no delay at all.

    Secure behaviour: a direct LAN client (192.168.1.50, no proxy in between) is
    keyed as 192.168.1.50 whatever X-Forwarded-For it sends.
    """
    conf = (REPO / "nginx" / conf_name).read_text()
    seen = {_nginx_real_ip(conf, "192.168.1.50", f"198.51.100.{i}") for i in range(1, 6)}
    keys = {login_key("admin@example.org", _req_with_real_ip(c)) for c in seen}
    assert seen == {"192.168.1.50"}, (
        f"a LAN client chose its throttle identity: {sorted(seen)} -> {len(keys)} keys"
    )


def _req_with_real_ip(ip: str):
    from starlette.requests import Request

    return Request({"type": "http", "headers": [(b"x-real-ip", ip.encode())], "client": ("10.0.0.2", 1)})


# ---------------------------------------------------------------------------
# adv-id-5 — a password change leaves open reset links alive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adv_identity_5_password_change_kills_open_reset_links(async_client, async_db):
    """A person who suspects compromise changes the password (session-only route);
    other sessions are revoked — but an outstanding reset link (forgot-password
    mail, admin-issued link, or one an attacker obtained, cf. adv-id-1) stays
    redeemable for its hour and resets the password straight back.
    Redeem and the CLI reset both call ``revoke_open_links``; the self change does not.
    """
    user = await _user(async_db, "carol@example.org")
    _link, raw = await links.issue_link(async_db, user, "reset")
    await async_db.commit()

    login = await async_client.post(
        "/api/auth/login", json={"email": "carol@example.org", "password": PASSWORD}, headers=ORIGIN
    )
    assert login.status_code == 204
    change = await async_client.post(
        "/api/auth/password",
        json={"current": PASSWORD, "new": "a brand new passphrase 42"},
        headers=ORIGIN,
    )
    assert change.status_code == 204, change.text
    found = await links.inspect_link(async_db, raw)
    assert found is None or found.state != "valid", "the old reset link still works"


# ---------------------------------------------------------------------------
# adv-id-6 — `python -m applire.admin reset-password` crashes after doing the work
# ---------------------------------------------------------------------------


def test_adv_identity_6_reset_password_cli_entry_point_exits_cleanly(monkeypatch, capsys):
    """``admin/__main__.py`` runs ``asyncio.run(args.func(args))``; ``create-admin``'s
    ``run`` is a coroutine function, ``reset-password``'s ``run`` is a plain function
    returning an int (it calls ``asyncio.run`` itself). So the documented recovery
    command (Operator Branch L, "the only admin forgot the password") sets the
    password and then dies with ``ValueError: a coroutine was expected, got 0`` —
    a traceback and a non-zero exit after a successful reset; a script that checks
    the exit code reports failure. The existing tests call ``reset.run`` directly.
    """
    import io

    from applire.admin import __main__ as admin_main
    from applire.admin import reset

    async def ok(email, password):
        return "password set — role: admin; signed out everywhere"

    monkeypatch.setattr(reset, "_run_async", ok)
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    code = admin_main.main(["reset-password", "--email", "a@example.org", "--password-stdin"])
    assert code == 0
