# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strawberry W3 integration — 1d (US323) as mounted in the REAL app.

1d's own tests drive its routers on a test app. These seams pin the wiring the
integration added: the five 1d routes are on ``applire.main.app``; a session
route runs under the caller's owner context (``set_owner`` via the shared auth
dependency, the same as every other door) with the guard ON; the lifespan stops
the boot on an unusable OIDC configuration before any migration runs; 1b's
account-deletion seam reaches 1d's ``consume_grant`` directly.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from applire import ownership
from applire.auth import get_auth_provider
from applire.config import settings
from applire.db.session import get_db
from applire.main import app
from applire.models.user import User

ROUTES_1D = {
    ("GET", "/api/auth/oidc/start"),
    ("GET", "/api/auth/oidc/callback"),
    ("POST", "/api/me/oidc/link"),
    ("DELETE", "/api/me/oidc"),
    ("POST", "/api/me/reauth/start"),
}


def test_the_1d_routes_are_mounted_on_the_real_app():
    mounted = {
        (m, r.path)
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods - {"HEAD", "OPTIONS"}
    }
    assert ROUTES_1D <= mounted, ROUTES_1D - mounted


class _SessionProvider:
    """A signed-in session user with a password AND an OIDC binding."""

    is_harness = False

    def __init__(self, user: User):
        self.user = user

    async def get_current_user(self, request, db):
        request.state.auth_via = "session"
        return self.user


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_a_mounted_1d_session_route_runs_under_the_callers_owner_context(
    async_db, monkeypatch
):
    from applire.routers import me_oidc

    monkeypatch.setattr(ownership, "GUARD_ENABLED", True)
    monkeypatch.setattr(ownership, "GUARD_REPORT_ONLY", False)
    monkeypatch.setattr(settings, "oidc_issuer", "https://idp.example.org")
    uid = uuid.uuid4()
    user = User(
        id=uid, email="w3int@example.org", password_hash="x",
        oidc_issuer="https://idp.example.org", oidc_subject="sub-1",
    )
    seen: list = []

    async def _consume(db, *, request, user, action, target_id):
        seen.append((ownership.current_owner(), action, target_id))
        return False

    monkeypatch.setattr(me_oidc, "consume_grant", _consume)

    async def _db():
        yield async_db

    async def _provider():
        return _SessionProvider(user)

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _provider
    try:
        assert ownership.current_owner() is None
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://applire.test"
        ) as c:
            resp = await c.delete(
                "/api/me/oidc", headers={"Origin": "http://applire.test"}
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth_provider, None)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error_code"] == "reauth_required"
    assert len(seen) == 1
    owner, action, target = seen[0]
    assert owner is not None and owner.user_id == uid
    assert (action, target) == ("oidc.unlink", uid)


@pytest.mark.asyncio
async def test_the_lifespan_refuses_an_unusable_oidc_config_before_migrating(monkeypatch):
    import applire.main as main_mod
    from applire.auth.oidc import OidcConfigError

    monkeypatch.setattr(settings, "oidc_issuer", "https://idp.example.org")
    monkeypatch.setattr(settings, "oidc_client_id", "")
    ran: list = []
    monkeypatch.setattr(main_mod.subprocess, "run", lambda *a, **k: ran.append(a))
    with pytest.raises(OidcConfigError, match="OIDC_CLIENT_ID"):
        async with main_mod.lifespan(app):
            pass
    assert ran == []


def test_the_account_deletion_seam_is_1ds_consume_grant():
    from applire.auth import reauth
    from applire.services.admin import identity_seams

    assert identity_seams.consume_grant is reauth.consume_grant
    assert not hasattr(identity_seams, "importlib")
