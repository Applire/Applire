# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial review of the Epic C admin surface and the S-1 base-URL change
(Strawberry build 2, reviewer ``adv-admin``, 2026-10-07).

``test_adv_admin_<n>_*`` each FAIL on the integrated build-2 tip and prove one
finding; the fix belongs in product code, not here. ``test_adv_admin_sound_*``
pin probes that held, so a later change cannot quietly undo them.

Zero provider calls, zero outbound network: fetches go to an ``httpx``
MockTransport or a socket on 127.0.0.1; the provider probe's reachability call
is a spy.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from applire.config import settings
from applire.models.audit import AuditEvent
from applire.models.instance_settings import InstanceSetting
from applire.models.retention_run import RetentionRun
from applire.services import audit
from applire.services import safe_fetch, scraper
from applire.services.scraper import ScraperError, scrape_job_url
from tests.unit.test_c1_instance_settings import (  # noqa: F401 — fixtures reused
    ORIGIN,
    _clean_overlay,
    _put,
    env,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ago(days: float) -> datetime:
    return _now() - timedelta(days=days)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


async def _audit_row(db, *, action, at, actor=None, details=None):
    """Insert one audit row with its own ``at`` (an INSERT; the log refuses UPDATEs)."""
    row = AuditEvent(id=uuid.uuid4(), at=at, actor_user_id=actor, action=action,
                     detail=details or {})
    db.add(row)
    await db.commit()
    return row


# =====================================================================================
# 1. #738 "proof it stayed on": enabled_since ignores the runs that skipped
# =====================================================================================


@pytest.mark.asyncio
async def test_adv_admin_1_enabled_since_ignores_runs_that_skipped(env):
    """The dashboard's proof line (``enabled_since``, rendered as "durchgehend
    eingeschaltet seit …") is derived from ``settings.*`` transition rows only.
    A run the worker SKIPPED (``retention.skipped`` audit row, run record
    ``retention_enabled: false``) is never consulted, so an OFF period the web
    process did not observe — the worker's own env, an env flipped between two
    backend boots, a direct table edit — vanishes from the proof.
    """
    db, client, _, admin = env
    await _audit_row(db, action="setup.claimed", at=_ago(60), actor=admin.id, details={"via": "web"})
    skipped_at = _ago(3)
    await _audit_row(db, action="retention.skipped", at=skipped_at, details={"source": "env"})
    db.add(RetentionRun(run_at=skipped_at, ok=True, duration_ms=1,
                        report={"retention_enabled": False, "retention_source": "env"}))
    await db.commit()

    ret = (await client.get("/api/admin/dashboard")).json()["retention"]
    assert ret["enabled"] is True
    assert ret["last_run_skipped"] is True  # the evidence is in the very same payload …
    since = _parse(ret["enabled_since"])
    assert since >= skipped_at - timedelta(seconds=1), (
        f"enabled_since={ret['enabled_since']} claims retention ran continuously since the "
        f"claim, although a run skipped the personal-data TTLs at {skipped_at.isoformat()}"
    )


# =====================================================================================
# 2. #726 LinkedIn switch: the host check runs on the SUBMITTED url only
# =====================================================================================


def _public_dns(monkeypatch):
    async def _resolve(host, port):  # noqa: ARG001 — a public address for every name
        return ["93.184.216.34"]

    monkeypatch.setattr(safe_fetch, "_resolve", _resolve)


@pytest.mark.asyncio
async def test_adv_admin_2a_linkedin_switch_off_is_bypassed_by_a_redirect(monkeypatch):
    """Switch OFF; the user submits a non-LinkedIn short link that 302s to a
    LinkedIn posting. ``safe_get`` follows the hop (it re-checks the ADDRESS per
    hop, not the host) and fetches LinkedIn's guest page."""
    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", False)
    _public_dns(monkeypatch)
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        hosts.append(host)
        if host == "short.example":
            return httpx.Response(302, headers={"location": "https://www.linkedin.com/jobs/view/1"})
        body = "<div class='description__text'>" + "Senior engineer posting text. " * 60 + "</div>"
        return httpx.Response(200, text=f"<html><body>{body}</body></html>")

    monkeypatch.setattr(safe_fetch, "_TRANSPORT", httpx.MockTransport(handler))

    async def _no_tier2(_url):
        return None

    monkeypatch.setattr(scraper, "_fetch_tier2", _no_tier2)
    try:
        await scrape_job_url("https://short.example/j/abc")
    except ScraperError:
        pass
    assert not any("linkedin" in h for h in hosts), (
        f"LinkedIn was fetched although SCRAPER_FETCH_LINKEDIN_GUEST_PAGES is off: hops {hosts}"
    )


class _FakeRequest:
    def __init__(self, url: str) -> None:
        self.url = url
        self.resource_type = "document"
        self.method = "GET"


class _FakeRoute:
    def __init__(self, url: str) -> None:
        self.request = _FakeRequest(url)
        self.aborted = False
        self.fulfilled = False

    async def abort(self) -> None:
        self.aborted = True

    async def fulfill(self, **_kw) -> None:
        self.fulfilled = True


@pytest.mark.asyncio
async def test_adv_admin_2b_tier2_routes_a_linkedin_request_although_the_switch_is_off(monkeypatch):
    """Tier 2 (Chromium) sends EVERY request the page makes through
    ``_tier2_route`` → ``safe_get`` with no LinkedIn check: a JS/meta-refresh
    navigation, an iframe, or a WHATWG/Python parser differential
    (``https://www.linkedin.com\\@evil.example/`` is ``evil.example`` to
    ``urlsplit`` but ``www.linkedin.com`` to Chromium) reaches LinkedIn."""
    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", False)
    fetched: list[str] = []

    async def spy_safe_get(url, **_kw):
        fetched.append(url)
        return httpx.Response(200, text="<html></html>")

    monkeypatch.setattr(scraper, "safe_get", spy_safe_get)
    route = _FakeRoute("https://www.linkedin.com/jobs/view/1")
    await scraper._tier2_route(route)
    assert fetched == [] and route.aborted, f"tier 2 fetched {fetched} with the switch off"


# =====================================================================================
# 3. A reset (DELETE) creates the state the PUT refuses with 409
# =====================================================================================


@pytest.mark.asyncio
async def test_adv_admin_3_reset_can_leave_the_active_provider_without_a_key(env, monkeypatch):
    """PUT refuses ``LLM_PROVIDER`` → a provider with no effective key (409
    ``provider_not_ready``). DELETE of the same override falls back to the env
    provider without that check, so the panel can still put the instance into a
    state where every generation fails."""
    _, client, _, _ = env
    monkeypatch.setattr(settings, "llm_provider", "anthropic")  # .env: anthropic …
    monkeypatch.setattr(settings, "anthropic_api_key", "")      # … whose key was never set
    assert (await _put(client, {"LLM_PROVIDER": "ollama"})).status_code == 200
    r = await client.delete("/api/admin/settings/LLM_PROVIDER", headers=ORIGIN)
    active = next((p for p in r.json().get("providers", []) if p["active"]), None)
    assert r.status_code == 409, (
        f"reset accepted ({r.status_code}); active provider now {active and active['id']} "
        f"ready={active and active['ready']}"
    )
    detail = r.json()["detail"]
    # Ruling adv-admin-1 = A (MD2-17): the refusal names the missing key.
    assert detail["error_code"] == "provider_not_ready" and detail.get("provider") == "anthropic"
    assert "ANTHROPIC_API_KEY" in str(detail)


@pytest.mark.asyncio
async def test_adv_admin_3b_openai_without_key_and_without_base_url_reads_ready(env, monkeypatch):
    """``KEY_REQUIRED['openai'] = False`` because ``OPENAI_BASE_URL`` may point at a
    keyless local server. With NO base URL the provider talks to api.openai.com
    with the placeholder key ``"local"`` (``providers/llm/openai.py:64``), yet the
    panel reports it ``ready`` and the 409 guard lets the switch through."""
    _, client, _, _ = env
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "openai_base_url", "")
    r = await _put(client, {"LLM_PROVIDER": "openai"})
    openai_row = next((p for p in r.json().get("providers", []) if p["id"] == "openai"), None)
    assert r.status_code == 409, (
        f"switch accepted ({r.status_code}); openai ready={openai_row and openai_row['ready']}"
    )


# =====================================================================================
# 4. A panel key with an ASCII control character: httpx echoes it verbatim
# =====================================================================================

CTRL_KEY = "sk-or-ADVSENTINEL\x0b4f2a"

#: Ceiling for every network leg below: a regression fails fast, never hangs.
_GUARD_S = 20


class _ClosingServer:
    """A socket on 127.0.0.1 that closes every connection it accepts.

    On Python 3.12 ``Server.wait_closed()`` waits for open connections, so a
    server whose handler leaves the connection open hangs the test forever."""

    def __init__(self) -> None:
        self.accepted: list[asyncio.StreamWriter] = []
        self.server: asyncio.Server | None = None
        self.port = 0

    async def _handle(self, _reader, writer) -> None:
        self.accepted.append(writer)
        writer.close()

    async def __aenter__(self) -> "_ClosingServer":
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_exc) -> None:
        assert self.server is not None
        self.server.close()
        for writer in self.accepted:
            writer.close()
        await asyncio.wait_for(self.server.wait_closed(), 5)


@pytest.mark.asyncio
async def test_adv_admin_4_a_key_with_a_control_character_is_stored(env):
    """``_validate`` refuses only CR, LF and NUL. A pasted key carrying another
    control character (VT/FF/ESC …) is stored; the first provider call then
    fails inside h11 with ``Illegal header value b'Bearer <the whole key>'`` —
    an exception text that ``str(exc)`` paths (job ``error_message``, HTTP
    ``detail``, logs) carry onwards (cf. FMEA N-18)."""
    _, client, _, _ = env

    # Precondition, on a socket on 127.0.0.1 (no provider, no network): httpx's
    # error text carries the header value — i.e. the key — verbatim.
    async with _ClosingServer() as srv:
        async with httpx.AsyncClient(timeout=2) as c:
            with pytest.raises(httpx.LocalProtocolError) as exc:
                await asyncio.wait_for(
                    c.get(f"http://127.0.0.1:{srv.port}/",
                          headers={"Authorization": f"Bearer {CTRL_KEY}"}),
                    _GUARD_S,
                )
        assert "ADVSENTINEL" in str(exc.value)

    r = await _put(client, {"OPENROUTER_API_KEY": CTRL_KEY})
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "invalid_setting_value", (
        f"a key with a control character was accepted ({r.status_code})"
    )


@pytest.mark.asyncio
async def test_adv_admin_4b_a_key_below_the_panel_reaches_no_plain_account(env, monkeypatch):
    """The panel refuses a key outside printable ASCII (4a). A value that gets
    in BELOW the panel — an ``instance_settings`` row written directly — still
    reaches the provider. A plain (non-admin) account then analyses a posting
    through REST and through the agent door: neither answer may carry the
    stored key. The Mistral SDK is pointed at a socket on 127.0.0.1 that closes
    at once; no provider is contacted, every leg is time-bounded."""
    import functools

    from fastapi import FastAPI
    from mcp.shared.exceptions import McpError
    from mistralai.utils import BackoffStrategy, RetryConfig
    from unittest.mock import AsyncMock, MagicMock

    import applire.providers.llm.mistral as mistral_mod
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.mcp import server as mcp_server
    from applire.routers import job as job_router
    from applire.services import instance_settings as svc
    from tests.support.owners_1b import add_user, client_for

    db, _admin_client, _, _ = env
    db.add(InstanceSetting(key="LLM_PROVIDER", value="mistral"))
    db.add(InstanceSetting(key="MISTRAL_API_KEY", secret_ciphertext=svc.encrypt_secret(CTRL_KEY)))
    await db.commit()
    await svc.refresh()
    assert settings.mistral_api_key == CTRL_KEY  # the overlay carries it
    monkeypatch.setattr(settings, "llm_timeout", 5)

    user = await add_user(db, email="plain-user@example.org", role="user")

    class _AsUser:
        async def get_current_user(self, request, _db):
            request.state.auth_via = "session"
            return user

    app = FastAPI()
    app.include_router(job_router.router)

    async def _db():
        yield db

    async def _prov():
        return _AsUser()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _prov
    posting = ("Senior Backend Engineer (m/w/d) at Example GmbH, Berlin. You build Python "
               "services with FastAPI and PostgreSQL. Requirements: 5 years Python, SQL, "
               "Docker. Nice to have: Kubernetes. We offer 30 days of holiday. ") * 3

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(mcp_server, "get_db", lambda: cm)
    monkeypatch.setattr(mcp_server, "_current_user_id", AsyncMock(return_value=user.id))

    async with _ClosingServer() as srv:
        monkeypatch.setattr(
            mistral_mod, "Mistral",
            functools.partial(
                mistral_mod.Mistral,
                server_url=f"http://127.0.0.1:{srv.port}",
                retry_config=RetryConfig("none", BackoffStrategy(1, 1, 1.0, 1), False),
                timeout_ms=2000,
            ),
        )
        async with client_for(app) as client:
            res = await asyncio.wait_for(
                client.post("/api/job/analyze", json={"text": posting}, headers=ORIGIN), _GUARD_S
            )
        with pytest.raises(McpError) as mcp_exc:
            await asyncio.wait_for(mcp_server.analyze_jd(text=posting), _GUARD_S)

    assert "ADVSENTINEL" not in res.text, f"REST answer carried the stored key: {res.text[:160]!r}"
    assert res.status_code == 500 and res.json()["detail"]["error_code"] == "internal_error", res.text
    message = mcp_exc.value.error.message
    assert "ADVSENTINEL" not in message, f"MCP answer carried the stored key: {message[:160]!r}"
    assert "Mistral call failed" in message, message


# =====================================================================================
# 5. Any settings write makes the next dashboard GET spend a provider call inline
# =====================================================================================


@pytest.mark.asyncio
async def test_adv_admin_5_a_settings_write_makes_the_next_dashboard_get_call_the_provider(env, monkeypatch):
    """``probe_provider`` promises it is "never triggered synchronously by a
    request". ``PUT``/``DELETE`` call ``reset_provider_cache()`` after EVERY write
    (even a LinkedIn toggle), and ``GET /api/admin/dashboard`` — which the
    settings page loads too — runs ``collect()`` inline, so the next page load
    performs a paid reachability call and waits on it (up to the LLM timeout in
    the 2026-09-16 slow-provider case the switch exists for)."""
    _, client, _, _ = env
    from applire.services.ops import config as ops_config
    from applire.services.ops import probes

    monkeypatch.setitem(probes.PLAIN_PROBES, "provider", probes.probe_provider)  # undo env's stub
    monkeypatch.setattr(ops_config, "provider_probe_enabled", lambda: True)
    monkeypatch.setattr(ops_config, "reachability_probe_enabled", lambda: True)
    monkeypatch.setattr(ops_config, "credit_probe_enabled", lambda: False)
    calls: list[int] = []

    async def spy() -> tuple[str, str]:
        calls.append(1)
        return "ok", ""

    monkeypatch.setattr(probes, "_probe_reachability", spy)
    try:
        await probes.probe_provider(force=True)  # the background loop warmed the cache
        calls.clear()
        assert (await _put(client, {"SCRAPER_FETCH_LINKEDIN_GUEST_PAGES": False})).status_code == 200
        assert (await client.get("/api/admin/dashboard")).status_code == 200
        assert calls == [], (
            f"the dashboard GET made {len(calls)} provider call(s) inline after a LinkedIn toggle"
        )
    finally:
        probes.reset_provider_cache()


# =====================================================================================
# 6. S-1: an EMPTY APPLIRE_BASE_URL is "unset" for MD-32/CSRF/OIDC but not for links
# =====================================================================================


def test_adv_admin_6_an_empty_base_url_is_unset_yet_agent_links_lose_their_origin(monkeypatch):
    """``configured_base_url()`` reads ``APPLIRE_BASE_URL=`` as unset (mail off,
    no Host allow-list), but every link builder (``routers/cv.py:77``,
    ``routers/flow.py:52``, ``mcp/server.py`` html_url/pdf_url) reads the raw
    field: the S-1 default ``http://localhost`` is not applied and agents get
    origin-less ``/api/...`` links. One value, two answers."""
    from applire.config import SHIPPED_DEFAULT_BASE_URL, Settings, configured_base_url

    monkeypatch.setenv("APPLIRE_BASE_URL", "")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    s = Settings(_env_file=None)
    assert configured_base_url(s.applire_base_url) is None  # unset, per the S-1 predicate
    assert s.applire_base_url.rstrip("/") == str(SHIPPED_DEFAULT_BASE_URL), (
        f"link origin is {s.applire_base_url!r} for an install the predicate calls unset"
    )


# =====================================================================================
# Sound probes (pass today; pinned)
# =====================================================================================


@pytest.mark.parametrize("url", [
    "HTTPS://WWW.LINKEDIN.COM/jobs/view/1",
    "https://user:pw@www.linkedin.com:443/jobs/view/1",
    "https://www.linkedin.com./jobs/view/1",
    "https://www.linkedin.com?@evil.example/",
    "https://www.linkedin.com#@evil.example/",
    "https://LNKD.IN/abc",
])
def test_adv_admin_sound_1_linkedin_host_spellings(url):
    from applire.services.scraper import is_linkedin_url

    assert is_linkedin_url(url) is True


@pytest.mark.asyncio
async def test_adv_admin_sound_2_no_secret_in_any_settings_refusal(env):
    """Every refusal path of PUT names the key only (stronger than C1's set:
    a nested secret, a secret under an unknown key's neighbour, a list value)."""
    _, client, _, _ = env
    s = "sk-or-ADVSOUND-9c1e"
    bodies = [
        {"changes": {"OPENROUTER_API_KEY": [s]}},
        {"changes": {"OPENROUTER_API_KEY": {"v": s}}},
        {"changes": {"OPENROUTER_API_KEY": s, "NOPE": s}},
        {"changes": {"OPENROUTER_API_KEY": s, "LLM_PROVIDER": "mock"}},
        {"changes": {"OPENROUTER_API_KEY": s + "\nX"}},
        {"changes": {"OPENROUTER_API_KEY": s}, "extra": s},
    ]
    for body in bodies:
        r = await client.put("/api/admin/settings", json=body, headers=ORIGIN)
        assert r.status_code == 422, (body, r.status_code)
        assert s not in r.text
    db = env[0]
    assert (await db.execute(select(InstanceSetting))).scalars().first() is None


@pytest.mark.asyncio
async def test_adv_admin_sound_3_csrf_guards_the_settings_writes_on_the_real_app(async_client, async_db, monkeypatch):
    """C1's endpoint tests run on a provider double without a cookie, so
    ``needs_cookie_csrf_check`` never fired there. On the real app with a real
    session cookie: no Origin, a foreign Origin and ``Origin: null`` are 403
    ``origin_mismatch`` on PUT and DELETE, and nothing is written."""
    from applire.auth import get_auth_provider
    from applire.auth.local import LocalAuthProvider
    from applire.auth.passwords import hash_password
    from applire.auth.throttle import login_throttle
    from applire.main import app as fastapi_app
    from applire.models.user import User

    fastapi_app.dependency_overrides[get_auth_provider] = lambda: LocalAuthProvider()
    login_throttle.clear()
    try:
        async_db.add(User(id=uuid.uuid4(), email="csrf-admin@example.org", role="admin",
                          password_hash=await hash_password("correct horse battery staple")))
        await async_db.commit()
        same = {"Origin": "http://test"}
        login = await async_client.post(
            "/api/auth/login",
            json={"email": "csrf-admin@example.org", "password": "correct horse battery staple"},
            headers=same,
        )
        assert login.status_code in (200, 204), login.text
        body = {"changes": {"RETENTION_ENABLED": False}}
        for headers in ({}, {"Origin": "http://evil.example"}, {"Origin": "null"},
                        {"Referer": "http://evil.example/x"}):
            put = await async_client.put("/api/admin/settings", json=body, headers=headers)
            assert put.status_code == 403 and put.json()["detail"]["error_code"] == "origin_mismatch", (
                headers, put.status_code)
            dele = await async_client.delete("/api/admin/settings/RETENTION_ENABLED", headers=headers)
            assert dele.status_code == 403, (headers, dele.status_code)
        assert (await async_db.execute(select(InstanceSetting))).scalars().first() is None
        ok = await async_client.put("/api/admin/settings", json=body, headers=same)
        assert ok.status_code == 200, ok.text
    finally:
        fastapi_app.dependency_overrides.pop(get_auth_provider, None)
        login_throttle.clear()
