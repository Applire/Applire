# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Consent to content logs (ADR-091/096 amended 2026-11-18).

At LOG_LEVEL=DEBUG: the invite needs the box, the person reads/agrees/withdraws
through ``/api/me/log-consent``, and model work is refused with
``log_consent_required`` until there is a current consent — over REST (409,
``detail.error_code``) and over MCP (``-32004``, code first in the message,
through ``mcp.call_tool``). Reading stays open after a withdrawal. At INFO
none of this exists.
"""

from __future__ import annotations

import importlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from sqlalchemy import select

from applire.auth import get_auth_provider
from applire.config import settings
from applire.db.session import get_db
from applire.models.audit import AuditEvent
from applire.models.user import User
from applire.services import allowance, log_consent
from applire.services.admin import identity_seams as seams
from applire.services.admin import links
from tests.support.owners_1b import add_user, client_for

H = {"Origin": "http://applire.test"}
GOOD_PW = "a perfectly fine passphrase"
CONSENT_VALUES = {
    "log_consent_operator_name": "Operator Example",
    "log_consent_contact_email": "kontakt@example.org",
    "log_consent_assistant_provider": "Assistant Inc., USA",
    "log_consent_log_host": "Host GmbH, Deutschland",
    "log_consent_model_provider": "Gateway / Model Co.",
}


class Acting:
    def __init__(self) -> None:
        self.user_id: uuid.UUID | None = None
        self.via = "session"

    async def get_current_user(self, request, db):
        if self.user_id is None:
            return None
        request.state.auth_via = self.via
        return await db.get(User, self.user_id, populate_existing=True)


@pytest.fixture(autouse=True)
def _clean():
    allowance.reset_context_for_tests()
    yield
    allowance.reset_context_for_tests()


@pytest.fixture
def debug(monkeypatch):
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    monkeypatch.setattr(settings, "content_log_retention_days", 30)
    for k, v in CONSENT_VALUES.items():
        monkeypatch.setattr(settings, k, v)
    monkeypatch.setattr(settings, "llm_provider", "mock")


def _app(db) -> tuple[FastAPI, Acting]:
    from applire.routers import auth as auth_router
    from applire.routers import auth_links, me_log_consent

    app = FastAPI()
    app.include_router(auth_router.router)
    app.include_router(auth_links.router)
    app.include_router(me_log_consent.router)
    module = importlib.import_module("applire.routers.job")

    async def door(provider=Depends(module._get_provider)):
        return {"ok": True}

    app.add_api_route("/door/job", door, methods=["POST"])
    acting = Acting()

    async def _db():
        yield db

    async def _provider():
        return acting

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _provider
    return app, acting


@pytest_asyncio.fixture
async def env(async_db, monkeypatch):
    app, acting = _app(async_db)

    async def fake_issue_session(db, user, response):
        response.set_cookie("applire_session", "fresh", httponly=True, samesite="lax", path="/")

    monkeypatch.setattr(seams, "issue_session", fake_issue_session)
    person = await add_user(async_db, email="tester-01@example.org")
    acting.user_id = person.id
    async with client_for(app) as client:
        yield async_db, client, acting, person


# ── state ────────────────────────────────────────────────────────────────────


def test_states_and_version(debug, monkeypatch):
    u = User(email="x@example.org")
    assert log_consent.state(u) == "missing"
    v = log_consent.current_version()
    log_consent.record_consent(u, v)
    assert log_consent.state(u) == "current" and log_consent.consent_ok(u)
    monkeypatch.setattr(settings, "log_consent_log_host", "Another Host, Finland")
    assert log_consent.current_version() != v  # a changed recipient is a new text
    assert log_consent.state(u) == "outdated" and not log_consent.consent_ok(u)
    log_consent.withdraw(u)
    assert log_consent.state(u) == "withdrawn"
    log_consent.record_consent(u, log_consent.current_version())
    assert log_consent.state(u) == "current"  # agreeing again re-enables
    with pytest.raises(log_consent.ConsentVersionStale):
        log_consent.record_consent(u, v)


def test_above_debug_nothing_is_required(monkeypatch):
    monkeypatch.setattr(settings, "log_level", "INFO")
    u = User(email="x@example.org")
    assert log_consent.state(u) == "not_required" and log_consent.consent_ok(u)
    assert log_consent.summary(u) == {"required": False, "state": "not_required"}


def test_the_age_setting_is_part_of_the_text(debug, monkeypatch):
    v30 = log_consent.current_version()
    monkeypatch.setattr(settings, "content_log_retention_days", 0)
    assert log_consent.current_version() != v30
    assert log_consent.text_values()["retention_days"] == 0


def test_missing_settings_are_named(debug, monkeypatch):
    assert log_consent.missing_settings() == []
    monkeypatch.setattr(settings, "log_consent_operator_name", " ")
    monkeypatch.setattr(settings, "log_consent_model_provider", "")
    assert log_consent.missing_settings() == ["LOG_CONSENT_OPERATOR_NAME", "LOG_CONSENT_MODEL_PROVIDER"]
    assert log_consent.missing_notice_settings() == ["LOG_CONSENT_OPERATOR_NAME"]


# ── doors ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_agree_withdraw_agree_again(env, debug):
    db, client, _, person = env
    r = await client.get("/api/me/log-consent")
    body = r.json()
    assert r.status_code == 200 and body["required"] is True and body["state"] == "missing"
    assert body["text"]["operator"] == "Operator Example" and body["text"]["retention_days"] == 30
    version = body["version"]

    r = await client.post("/api/me/log-consent", json={"version": version}, headers=H)
    assert r.status_code == 200 and r.json()["state"] == "current"
    assert r.json()["consented_version"] == version

    r = await client.delete("/api/me/log-consent", headers=H)
    assert r.status_code == 200 and r.json()["state"] == "withdrawn"
    row = await db.get(User, person.id, populate_existing=True)
    assert row.log_consent_withdrawn_at is not None and row.deleted_at is None  # the account stays

    r = await client.post("/api/me/log-consent", json={"version": version}, headers=H)
    assert r.json()["state"] == "current"
    actions = [a.action for a in (await db.execute(select(AuditEvent))).scalars().all()]
    assert actions.count("log_consent.given") == 2 and actions.count("log_consent.withdrawn") == 1


@pytest.mark.asyncio
async def test_agreeing_to_a_stale_text_is_refused(env, debug):
    _, client, _, _ = env
    r = await client.post("/api/me/log-consent", json={"version": "1-deadbeef"}, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "log_consent_version_stale"


@pytest.mark.asyncio
async def test_an_api_token_cannot_consent_or_withdraw(env, debug):
    _, client, acting, _ = env
    acting.via = "bearer"
    r = await client.post("/api/me/log-consent", json={"version": log_consent.current_version()}, headers=H)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_doors_say_not_required_above_debug(env, monkeypatch):
    _, client, _, _ = env
    monkeypatch.setattr(settings, "log_level", "INFO")
    assert (await client.get("/api/me/log-consent")).json() == {
        "required": False, "state": "not_required", "version": None, "consented_at": None,
        "consented_version": None, "withdrawn_at": None, "text": None,
    }
    state = (await client.get("/api/auth/state")).json()
    assert state["log_consent_required"] is False and state["log_consent_text"] is None
    me = (await client.get("/api/auth/me")).json()
    assert me["log_consent"]["required"] is False


@pytest.mark.asyncio
async def test_auth_state_and_me_carry_the_consent_at_debug(env, debug):
    _, client, _, _ = env
    state = (await client.get("/api/auth/state")).json()
    assert state["log_consent_required"] is True
    assert state["log_consent_text"]["contact"] == "kontakt@example.org"
    me = (await client.get("/api/auth/me")).json()
    assert me["log_consent"]["state"] == "missing"


# ── invite ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_invite_at_debug_needs_the_box_and_the_link_survives_a_refusal(env, debug):
    db, client, _, _ = env
    pending = await add_user(db, email="tester-02@example.org", state="pending")
    _, raw = await links.issue_link(db, pending, "invite")
    await db.commit()

    r = await client.post("/api/auth/links/redeem", json={"token": raw, "password": GOOD_PW}, headers=H)
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "log_consent_required"
    assert (await links.inspect_link(db, raw)).state == "valid"  # not burnt

    r = await client.post(
        "/api/auth/links/redeem",
        json={"token": raw, "password": GOOD_PW, "log_consent_version": log_consent.current_version()},
        headers=H,
    )
    assert r.status_code == 204, r.text
    row = await db.get(User, pending.id, populate_existing=True)
    assert row.log_consent_at is not None and row.log_consent_version == log_consent.current_version()
    assert log_consent.state(row) == "current"


@pytest.mark.asyncio
async def test_an_invite_above_debug_needs_no_box(env, monkeypatch):
    db, client, _, _ = env
    monkeypatch.setattr(settings, "log_level", "INFO")
    pending = await add_user(db, email="tester-03@example.org", state="pending")
    _, raw = await links.issue_link(db, pending, "invite")
    await db.commit()
    r = await client.post("/api/auth/links/redeem", json={"token": raw, "password": GOOD_PW}, headers=H)
    assert r.status_code == 204
    row = await db.get(User, pending.id, populate_existing=True)
    assert row.log_consent_at is None


@pytest.mark.asyncio
async def test_a_password_reset_needs_no_box(env, debug):
    """A reset enters an existing account; the shell's interstitial asks then."""
    db, client, _, person = env
    _, raw = await links.issue_link(db, person, "reset")
    await db.commit()
    r = await client.post("/api/auth/links/redeem", json={"token": raw, "password": GOOD_PW}, headers=H)
    assert r.status_code == 204


# ── admission (REST) ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_work_is_refused_without_a_current_consent(env, debug):
    db, client, _, person = env
    r = await client.post("/door/job", headers=H)
    assert r.status_code == 409
    assert r.json()["detail"]["error_code"] == "log_consent_required"

    await client.post("/api/me/log-consent", json={"version": log_consent.current_version()}, headers=H)
    assert (await client.post("/door/job", headers=H)).status_code == 200

    await client.delete("/api/me/log-consent", headers=H)
    r = await client.post("/door/job", headers=H)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "log_consent_required"
    # Reading stays open: the consent door itself and the account.
    assert (await client.get("/api/me/log-consent")).status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 200


@pytest.mark.asyncio
async def test_model_work_runs_above_debug_without_any_consent(env, monkeypatch):
    _, client, _, _ = env
    monkeypatch.setattr(settings, "log_level", "INFO")
    monkeypatch.setattr(settings, "llm_provider", "mock")
    assert (await client.post("/door/job", headers=H)).status_code == 200


def test_the_factory_safety_net_refuses_an_owner_without_facts_at_debug(debug):
    from applire.ownership import owner_context

    with owner_context(uuid.uuid4()):
        with pytest.raises(allowance.AdmissionRefused) as exc:
            allowance.resolve_for_factory()
    assert exc.value.code == "log_consent_required"


def test_the_factory_safety_net_is_unchanged_above_debug(monkeypatch):
    from applire.ownership import owner_context

    monkeypatch.setattr(settings, "log_level", "INFO")
    with owner_context(uuid.uuid4()):
        assert allowance.resolve_for_factory().payer == "instance"


# ── admission (MCP, through the FastMCP tool layer) ───────────────────────────


@pytest.mark.asyncio
async def test_mcp_model_work_is_refused_with_the_same_code_through_call_tool(debug):
    from applire.mcp import server

    person = User(email="tester-04@example.org")
    person.id = uuid.uuid4()  # never consented

    body = AsyncMock()
    with patch.object(server.mcp_identity, "bound", return_value=None), patch.object(
        server.mcp_identity, "revalidate", AsyncMock(return_value=person)
    ), patch.object(server.job_svc, "analyze_jd", body), patch.object(
        server, "get_db", MagicMock()
    ), patch.object(server, "get_provider", MagicMock(name="factory")) as factory, patch(
        "applire.services.instance_settings.refresh", AsyncMock(return_value={})
    ):
        with pytest.raises(Exception) as exc:
            await server.mcp.call_tool("analyze_jd", {"text": "Backend Engineer at Example GmbH"})
    assert "log_consent_required" in str(exc.value)
    factory.assert_not_called()
    body.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_refusal_code_and_message(debug):
    from mcp.shared.exceptions import McpError

    from applire.mcp import server

    person = User(email="tester-05@example.org")
    person.id = uuid.uuid4()
    person.log_consent_withdrawn_at = datetime.now(timezone.utc)
    person.log_consent_at = datetime.now(timezone.utc) - timedelta(days=1)
    with patch.object(server.mcp_identity, "bound", return_value=None), patch.object(
        server.mcp_identity, "revalidate", AsyncMock(return_value=person)
    ), patch.object(server, "get_db", MagicMock()), patch.object(
        server, "get_provider", MagicMock(name="factory")
    ), patch("applire.services.instance_settings.refresh", AsyncMock(return_value={})):
        with pytest.raises(McpError) as exc:
            await server.analyze_jd(text="Backend Engineer at Example GmbH")
    assert exc.value.error.code == -32004
    assert exc.value.error.message.startswith("log_consent_required: ")
