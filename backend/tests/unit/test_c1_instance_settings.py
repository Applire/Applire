# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""WP-C1 — runtime instance settings (ADR-093; #710, #726, #738).

Evidence contract (WORK-PACKAGES §WP-C1):
* endpoint-level authz: anonymous 401, non-admin 403, admin bearer 403 on writes;
* a stored key never appears in any response, log line, debug record, audit row,
  ``llm_usage`` row or the raw table (sentinel test — mutation-killed);
* a switch reaches every process on its next unit of work: web (middleware),
  MCP (``_agent_call``), retention worker (``run``) — one seam test each, with the
  row written by ANOTHER session so no local cache can carry it;
* in-flight work keeps its pin (ruling C1-4).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, FastAPI
from sqlalchemy import select

from applire import config as app_config
from applire.auth import get_auth_provider, links
from applire.config import settings
from applire.db.session import get_db
from applire.models.audit import AuditEvent
from applire.models.instance_settings import InstanceSetting
from applire.models.llm_usage import LlmUsage
from applire.models.user import User
from applire.providers.llm import get_provider, unwrap_provider
from applire.routers.admin import instance as admin_instance
from applire.routers.admin import settings as admin_settings
from applire.services import instance_settings as svc
from tests.support.owners_1b import add_user, client_for

SENTINEL = "sk-or-SENTINEL-c1-7f3a9b2e"
SECRET = b"c1-test-instance-secret-32bytes!"
ORIGIN = {"Origin": "http://applire.test"}

ROUTES = [
    ("GET", "/api/admin/settings"),
    ("PUT", "/api/admin/settings"),
    ("DELETE", "/api/admin/settings/LLM_PROVIDER"),
    ("GET", "/api/admin/audit"),
    ("GET", "/api/admin/usage"),
    ("GET", "/api/admin/dashboard"),
    ("GET", "/api/admin/notices"),
]


class Acting:
    def __init__(self) -> None:
        self.user_id: uuid.UUID | None = None
        self.via = "session"

    async def get_current_user(self, request, db):
        if self.user_id is None:
            return None
        request.state.auth_via = self.via
        return await db.get(User, self.user_id)


def _factory_for(db):
    @asynccontextmanager
    async def _session():
        yield db

    return lambda: _session


@pytest.fixture(autouse=True)
def _clean_overlay(monkeypatch):
    """No test may leak an override into another (the overlay is process-global)."""
    previous_secret = links._instance_secret
    links.set_instance_secret(SECRET)
    app_config.set_overlay_latest({})
    svc._state.last_refresh = 0.0
    svc._state.unreadable = frozenset()
    yield
    app_config.set_overlay_latest({})
    svc._state.last_refresh = 0.0
    svc._state.unreadable = frozenset()
    links._instance_secret = previous_secret


@pytest_asyncio.fixture
async def env(async_db, monkeypatch):
    monkeypatch.setattr(svc, "_session_factory", _factory_for(async_db))
    # Budget 0 provider calls: the dashboard's ops collect() must never ping a provider.
    from applire.services.ops import probes

    async def _no_provider_probe(force: bool = False):
        return probes.ProbeResult("provider", probes.UNKNOWN, "stubbed in tests", {})

    monkeypatch.setitem(probes.PLAIN_PROBES, "provider", _no_provider_probe)
    monkeypatch.setattr(probes, "_code_head", lambda: None)
    app = FastAPI()
    app.include_router(admin_settings.router)
    app.include_router(admin_instance.router)
    acting = Acting()

    async def _db():
        yield async_db

    async def _provider():
        return acting

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _provider
    admin = await add_user(async_db, email="admin@example.org", role="admin")
    acting.user_id = admin.id
    async with client_for(app) as client:
        yield async_db, client, acting, admin


async def _put(client, changes):
    return await client.put("/api/admin/settings", json={"changes": changes}, headers=ORIGIN)


def _item(body, key):
    return next(i for i in body["items"] if i["key"] == key)


# --- authz at the endpoint ----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", ROUTES)
async def test_anonymous_gets_401_and_non_admin_403(env, method, path):
    db, client, acting, _ = env
    body = {"changes": {"LLM_PROVIDER": "ollama"}} if method == "PUT" else None
    acting.user_id = None
    r = await client.request(method, path, json=body, headers=ORIGIN)
    assert r.status_code == 401, (path, r.text)
    user = await add_user(db, role="user")
    acting.user_id = user.id
    r = await client.request(method, path, json=body, headers=ORIGIN)
    assert r.status_code == 403 and r.json()["detail"]["error_code"] == "forbidden", (path, r.text)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", [("PUT", "/api/admin/settings"),
                                         ("DELETE", "/api/admin/settings/LLM_PROVIDER")])
async def test_an_admin_bearer_cannot_write(env, method, path):
    """ADR-093 cl. 7: a leaked admin api token must not reroute every user's documents."""
    _, client, acting, _ = env
    acting.via = "bearer"
    body = {"changes": {"LLM_PROVIDER": "ollama"}} if method == "PUT" else None
    r = await client.request(method, path, json=body)
    assert r.status_code == 403, r.text
    acting.via = "session"
    assert (await client.get("/api/admin/settings")).status_code == 200


# --- read shape ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_lists_the_closed_panel_set_and_never_a_secret_value(env, monkeypatch):
    _, client, _, _ = env
    monkeypatch.setattr(settings, "openrouter_api_key", SENTINEL)  # an ENV key
    r = await client.get("/api/admin/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    from applire.settings_registry import PANEL_KEYS

    assert [i["key"] for i in body["items"]] == list(PANEL_KEYS)
    key = _item(body, "OPENROUTER_API_KEY")
    assert key["kind"] == "secret" and key["value"] is None and key["env_value"] is None
    assert key["is_set"] is True and key["env_is_set"] is True
    assert SENTINEL not in r.text
    assert _item(body, "LLM_PROVIDER")["choices"] == list(svc.PROVIDERS)
    assert _item(body, "RETENTION_ENABLED")["value"] is True
    assert _item(body, "SCRAPER_FETCH_LINKEDIN_GUEST_PAGES")["value"] is True
    luna = next(p for p in body["providers"] if p["id"] == "openrouter")
    assert set(luna) >= {"qualification", "qualification_reason", "qualification_as_of"}


# --- the switch (#710 acceptance) --------------------------------------------------


@pytest.mark.asyncio
async def test_switch_provider_model_and_key_in_one_put(env):
    db, client, _, admin = env
    r = await _put(client, {"LLM_PROVIDER": "openrouter", "OPENROUTER_MODEL": "openai/gpt-5.6-luna",
                            "OPENROUTER_API_KEY": SENTINEL})
    assert r.status_code == 200, r.text
    body = r.json()
    assert SENTINEL not in r.text
    assert _item(body, "LLM_PROVIDER")["value"] == "openrouter"
    assert _item(body, "LLM_PROVIDER")["source"] == "panel"
    assert _item(body, "LLM_PROVIDER")["updated_by_email"] == "admin@example.org"
    key = _item(body, "OPENROUTER_API_KEY")
    assert key["value"] is None and key["is_set"] is True and key["source"] == "panel"
    active = next(p for p in body["providers"] if p["active"])
    assert active["id"] == "openrouter" and active["qualification"] == "qualified"

    # The next call in this process resolves the new provider with the panel key.
    provider = unwrap_provider(get_provider())
    assert type(provider).__name__ == "OpenRouterProvider"
    assert provider._model == "openai/gpt-5.6-luna"

    rows = (await db.execute(select(AuditEvent).where(AuditEvent.action == "settings.changed"))).scalars().all()
    by_key = {r.detail["key"]: r for r in rows}
    assert set(by_key) == {"LLM_PROVIDER", "OPENROUTER_MODEL", "OPENROUTER_API_KEY"}
    assert by_key["OPENROUTER_API_KEY"].detail["write_only"] is True
    assert "to_value" not in by_key["OPENROUTER_API_KEY"].detail
    assert by_key["LLM_PROVIDER"].detail["to_value"] == "openrouter"
    assert all(r.actor_user_id == admin.id for r in rows)


@pytest.mark.asyncio
async def test_switching_to_a_provider_without_key_is_409(env, monkeypatch):
    _, client, _, _ = env
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    r = await _put(client, {"LLM_PROVIDER": "anthropic"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"error_code": "provider_not_ready",
                                  "message": "The provider has no API key.", "provider": "anthropic"}
    # keyless providers need none
    assert (await _put(client, {"LLM_PROVIDER": "ollama"})).status_code == 200


@pytest.mark.asyncio
async def test_reset_returns_to_the_environment_value(env):
    db, client, _, _ = env
    await _put(client, {"OPENROUTER_MODEL": "x/y"})
    r = await client.delete("/api/admin/settings/OPENROUTER_MODEL", headers=ORIGIN)
    assert r.status_code == 200, r.text
    item = _item(r.json(), "OPENROUTER_MODEL")
    assert item["source"] in ("env", "default") and item["value"] == settings.env_value("openrouter_model")
    assert (await db.execute(select(AuditEvent).where(AuditEvent.action == "settings.reset"))).scalars().all()
    assert (await client.delete("/api/admin/settings/NOPE", headers=ORIGIN)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"LLM_PROVIDER": "mock"},
    {"LLM_PROVIDER": True},
    {"RETENTION_ENABLED": "false"},
    {"OPENROUTER_API_KEY": ""},
    {"OPENROUTER_BASE_URL": "https://evil.example"},
    {"OPENROUTER_MODEL": "a\nb"},
])
async def test_invalid_changes_are_refused_atomically(env, changes):
    db, client, _, _ = env
    r = await _put(client, {"OLLAMA_MODEL": "llama3.3", **changes})
    assert r.status_code == 422 and r.json()["detail"]["error_code"] in (
        "invalid_setting_value", "unknown_setting"), r.text
    assert (await db.execute(select(InstanceSetting))).scalars().all() == []


# --- secrets never leak (FMEA N-3) ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_validation_error_never_echoes_a_secret(env):
    """FastAPI's default 422 echoes ``input`` — the PUT body is validated raw."""
    _, client, _, _ = env
    too_many = {f"K{i}": SENTINEL for i in range(21)}
    for body in ({"changes": too_many}, {"changes": {"OPENROUTER_API_KEY": [SENTINEL]}},
                 {"changes": {"OPENROUTER_API_KEY": SENTINEL}, "extra": SENTINEL},
                 {"changes": {"OPENROUTER_API_KEY": SENTINEL * 20}},
                 {"changes": {"NOT_A_PANEL_KEY": SENTINEL}},
                 {"changes": {"LLM_PROVIDER": SENTINEL}},
                 {"changes": {"OPENROUTER_API_KEY": SENTINEL, "RETENTION_ENABLED": "no"}}):
        r = await client.put("/api/admin/settings", json=body, headers=ORIGIN)
        assert r.status_code == 422, r.text
        assert SENTINEL not in r.text


@pytest.mark.asyncio
async def test_a_stored_key_appears_in_no_response_log_audit_usage_or_debug_record(
    env, caplog, tmp_path, monkeypatch
):
    db, client, _, _ = env
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path))
    r = await _put(client, {"OPENROUTER_API_KEY": SENTINEL, "OPENROUTER_MODEL": "m/x"})
    assert r.status_code == 200

    bodies = [r.text]
    for path in ("/api/admin/settings", "/api/admin/dashboard", "/api/admin/notices",
                 "/api/admin/audit", "/api/admin/usage"):
        resp = await client.get(path)
        assert resp.status_code == 200, (path, resp.text)
        bodies.append(resp.text)

    # One recorded provider call (mock provider; the debug log and the usage row run).
    monkeypatch.setattr(settings, "llm_provider", "mock")
    from applire.providers.llm import usage as usage_mod

    recorded = []

    async def _sink(row):
        recorded.append(row)

    usage_mod.set_sink(_sink)
    try:
        await get_provider().acomplete("ping")
    finally:
        usage_mod.restore_sink()
    assert recorded, "the usage recorder must have run"
    assert list(tmp_path.rglob("*.jsonl")), "the debug log must have written a record"

    assert all(SENTINEL not in b for b in bodies)
    assert SENTINEL not in caplog.text
    assert SENTINEL not in repr(settings) and SENTINEL not in str(settings.model_dump())
    for f in tmp_path.rglob("*"):
        if f.is_file():
            assert SENTINEL not in f.read_text("utf-8", errors="replace")
    audit_rows = (await db.execute(select(AuditEvent))).scalars().all()
    assert audit_rows and all(SENTINEL not in json.dumps(a.detail) for a in audit_rows)
    usage_rows = (await db.execute(select(LlmUsage))).scalars().all()
    assert all(SENTINEL not in repr(vars(u)) for u in usage_rows)
    assert all(SENTINEL not in repr(r) for r in recorded)
    raw = (await db.get(InstanceSetting, "OPENROUTER_API_KEY"))
    assert raw.value is None and raw.secret_ciphertext and SENTINEL not in raw.secret_ciphertext
    assert SENTINEL not in repr(raw)
    # ... and yet it is the effective key.
    app_config.set_overlay_latest(await svc.refresh() or {})
    assert settings.openrouter_api_key == SENTINEL


@pytest.mark.asyncio
async def test_a_secret_is_never_stored_in_plain_text_without_the_instance_secret(env, monkeypatch):
    db, client, _, _ = env
    links.set_instance_secret(None)

    async def _no_secret(_db):
        return None

    monkeypatch.setattr(links, "load_instance_secret", _no_secret)
    r = await _put(client, {"OPENROUTER_API_KEY": SENTINEL})
    assert r.status_code == 503 and r.json()["detail"]["error_code"] == "settings_secret_unavailable"
    assert SENTINEL not in r.text
    assert (await db.execute(select(InstanceSetting))).scalars().all() == []


@pytest.mark.asyncio
async def test_an_undecryptable_secret_reads_as_unset_with_a_critical_notice(env, monkeypatch):
    db, client, _, _ = env
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    await _put(client, {"OPENROUTER_API_KEY": SENTINEL})
    links.set_instance_secret(b"another-instance-secret-32bytes!")  # rotated
    await svc.refresh()
    assert settings.openrouter_api_key == ""  # env value, never ciphertext
    r = await client.get("/api/admin/notices")
    assert {"code": "settings_secret_unreadable", "severity": "critical"} in r.json()["items"]
    assert _item((await client.get("/api/admin/settings")).json(), "OPENROUTER_API_KEY")["is_set"] is False


# --- every process sees the switch (seam test per process) -------------------------


async def _write_row_as_another_process(db, key, value):
    """A row written straight to the table — no local cache, no local refresh."""
    db.add(InstanceSetting(key=key, value=value))
    await db.commit()


@pytest.mark.asyncio
async def test_seam_web_middleware_pins_the_fresh_override(env):
    db, _, _, _ = env
    await _write_row_as_another_process(db, "LLM_PROVIDER", "ollama")
    seen = {}

    async def app(scope, receive, send):
        seen["provider"] = settings.llm_provider
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = svc.InstanceSettingsPin(app)

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(_msg):
        return None

    await mw({"type": "http", "method": "GET", "path": "/x", "headers": []}, receive, send)
    assert seen["provider"] == "ollama"


@pytest.mark.asyncio
async def test_seam_mcp_tool_call_uses_the_switched_provider(env, monkeypatch):
    """#710: "The MCP door uses the switched provider on its next call" — analyze_jd
    resolves its provider BEFORE opening a session, so the pin must come first."""
    db, _, _, _ = env
    from applire.mcp import server

    await _write_row_as_another_process(db, "LLM_PROVIDER", "ollama")
    await _write_row_as_another_process(db, "OLLAMA_MODEL", "llama-seam")
    captured = {}

    async def fake_analyze(jd_text, _db, provider, **_kw):
        captured["provider"] = unwrap_provider(provider)
        result = MagicMock()
        result.model_dump.return_value = {"id": str(uuid.uuid4())}
        return result

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.job_svc.analyze_jd", fake_analyze),
    ):
        await server.analyze_jd(text="Senior Backend Engineer at Acme GmbH")
    assert type(captured["provider"]).__name__ == "OllamaProvider"
    assert captured["provider"]._model == "llama-seam"


@pytest.mark.asyncio
async def test_seam_retention_worker_reads_the_toggle_at_run_start(env, monkeypatch):
    db, _, _, _ = env
    from applire.retention import worker

    await _write_row_as_another_process(db, "RETENTION_ENABLED", False)
    seen = {}

    async def _probe():
        seen["on"] = worker._RUN_ENABLED.get()
        return {}

    monkeypatch.setattr(worker, "_sweep_unscoped", _probe)

    @asynccontextmanager
    async def _session():
        yield db

    monkeypatch.setattr(worker, "AsyncSessionLocal", _session)
    report = await worker._sweep()
    assert seen["on"] is False
    assert report["retention_enabled"] is False and report["retention_source"] == "panel"
    skipped = (await db.execute(select(AuditEvent).where(AuditEvent.action == "retention.skipped"))).scalars().all()
    assert len(skipped) == 1 and skipped[0].detail == {"source": "panel", "target_type": "instance",
                                                       "target_id": None}


# --- in-flight work keeps its provider (ruling C1-4) --------------------------------


@pytest.mark.asyncio
async def test_a_task_started_under_a_pin_keeps_it_after_a_switch():
    app_config.set_overlay_latest({"llm_provider": "requesty"})
    started, release, seen = asyncio.Event(), asyncio.Event(), []

    async def generation():
        seen.append(settings.llm_provider)  # the writer
        started.set()
        await release.wait()
        seen.append(settings.llm_provider)  # the self-audit / critic, after the switch

    token = app_config.pin_overlay()
    try:
        task = asyncio.create_task(generation())
    finally:
        app_config.unpin_overlay(token)
    await started.wait()
    app_config.set_overlay_latest({"llm_provider": "openrouter"})  # the admin switches
    release.set()
    await task
    assert seen == ["requesty", "requesty"]
    assert settings.llm_provider == "openrouter"  # the NEXT unit of work


@pytest.mark.asyncio
async def test_background_tasks_of_a_request_inherit_the_request_pin(env):
    db, client, _, _ = env
    seen = []

    app = FastAPI()

    @app.get("/gen")
    async def gen(background: BackgroundTasks):
        async def job():
            app_config.set_overlay_latest({"llm_provider": "ollama"})  # switch mid-job
            seen.append(settings.llm_provider)

        background.add_task(job)
        return {}

    await _write_row_as_another_process(db, "LLM_PROVIDER", "mistral")
    wrapped = svc.InstanceSettingsPin(app)
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=wrapped), base_url="http://t") as c:
        assert (await c.get("/gen")).status_code == 200
    assert seen == ["mistral"]


# --- dependencies, boot observation ---------------------------------------------------


@pytest.mark.asyncio
async def test_ocr_dependency_is_shown_not_broken(env, monkeypatch):
    _, client, _, _ = env
    monkeypatch.setattr(settings, "ocr_backend", "mistral_vision")
    monkeypatch.setattr(settings, "mistral_api_key", "")
    body = (await client.get("/api/admin/settings")).json()
    assert body["dependencies"] == [{"code": "ocr_needs_mistral_key", "satisfied": False,
                                     "keys": ["OCR_BACKEND", "MISTRAL_API_KEY"]}]
    await _put(client, {"LLM_PROVIDER": "ollama"})  # switching never touches the Mistral key
    assert (await client.get("/api/admin/settings")).json()["dependencies"][0]["satisfied"] is False


@pytest.mark.asyncio
async def test_boot_observation_audits_env_changes_of_tracked_keys(async_db, monkeypatch):
    db = async_db
    assert await svc.observe_boot(db) == 3  # first observation: a baseline row each
    await db.commit()
    assert await svc.observe_boot(db) == 0
    monkeypatch.setattr(settings, "retention_enabled", False)  # an .env edit + restart
    assert await svc.observe_boot(db) == 1
    await db.commit()
    rows = (await db.execute(select(AuditEvent).where(AuditEvent.action == "settings.env_observed")
                             .order_by(AuditEvent.at))).scalars().all()
    last = rows[-1]
    assert last.actor_user_id is None
    assert last.detail["key"] == "RETENTION_ENABLED"
    assert last.detail["from_value"] is True and last.detail["to_value"] is False


# --- registry / overlay invariants ----------------------------------------------------


def test_panel_fields_match_the_registry_and_exclude_base_urls():
    from applire.settings_registry import PANEL_KEYS, get

    assert app_config.PANEL_FIELDS == {k.lower() for k in PANEL_KEYS}
    assert all(get(k).panel for k in PANEL_KEYS)
    assert not any(k.endswith("_BASE_URL") for k in PANEL_KEYS)
    assert set(svc.META) == set(PANEL_KEYS)


def test_no_module_level_or_default_argument_read_of_a_panel_field():
    """FMEA N-9: a reader that copies a panel field at import freezes the switch."""
    import ast
    from pathlib import Path

    root = Path(svc.__file__).resolve().parents[1]
    offenders = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        frozen_nodes = list(tree.body)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                frozen_nodes += node.args.defaults + node.args.kw_defaults
        for top in frozen_nodes:
            if top is None or isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for n in ast.walk(top):
                if (isinstance(n, ast.Attribute) and n.attr in app_config.PANEL_FIELDS
                        and isinstance(n.value, ast.Name) and n.value.id == "settings"):
                    offenders.append(f"{path.relative_to(root)}:{n.lineno}")
    assert offenders == []


def test_cloud_cannot_switch_retention_off(monkeypatch):
    monkeypatch.setattr(svc, "HAS_CLOUD", True)
    with pytest.raises(svc.SettingsError) as exc:
        svc._validate("RETENTION_ENABLED", False)
    assert exc.value.extra["reason"] == "mandatory_in_cloud"
    app_config.set_overlay_latest({"retention_enabled": False})
    assert svc.retention_state() == (True, "default")
