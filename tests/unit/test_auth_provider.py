# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The provider contract and the factory (ADR-008; ADR-091 cl. 2–4; D-2, MD-2).

The seam a Cloud provider implements: ``async get_current_user(request, db) ->
User | None``. The dependencies turn ``None`` into 401 and pass a user through
(stub-provider seam test, US319).
"""

import asyncio
import inspect
import uuid
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from applire.auth import get_auth_provider, provider_name
from applire.auth.base import AuthProvider
from applire.auth.deps import require_user
from applire.auth.harness import HarnessAuthProvider
from applire.auth.local import LocalAuthProvider
from applire.db.session import get_db
from applire.models.user import User


def _factory(provider: str, harness: bool = False):
    with patch("applire.auth.settings") as s:
        s.auth_provider = provider
        s.auth_harness = harness
        return asyncio.run(get_auth_provider())


def test_default_and_re_meant_none_build_the_local_provider():
    assert isinstance(_factory("local"), LocalAuthProvider)
    assert isinstance(_factory("none"), LocalAuthProvider)  # MD-2: none → local
    assert isinstance(_factory("None"), LocalAuthProvider)


def test_the_harness_flag_builds_the_harness():
    assert isinstance(_factory("local", harness=True), HarnessAuthProvider)
    assert isinstance(_factory("none", harness=True), HarnessAuthProvider)


def test_an_unknown_value_raises():
    with pytest.raises(ValueError, match="Unknown AUTH_PROVIDER"):
        _factory("magic")
    with patch("applire.auth.settings") as s:
        s.auth_provider = "zitadel"
        with pytest.raises(ValueError):
            provider_name()


def test_the_config_default_is_local():
    from applire.config import Settings

    assert Settings.model_fields["auth_provider"].default == "local"
    assert Settings.model_fields["auth_harness"].default is False
    assert Settings.model_fields["cookie_secure"].default is False


def test_the_contract_is_async_and_takes_the_session():
    sig = inspect.signature(AuthProvider.get_current_user)
    assert list(sig.parameters) == ["self", "request", "db"]
    assert inspect.iscoroutinefunction(AuthProvider.get_current_user)
    for cls in (LocalAuthProvider, HarnessAuthProvider):
        assert inspect.iscoroutinefunction(cls.get_current_user)
        assert list(inspect.signature(cls.get_current_user).parameters) == ["self", "request", "db"]


def test_auth_provider_is_abstract():
    with pytest.raises(TypeError):
        AuthProvider()  # type: ignore[abstract]


class _StubProvider(AuthProvider):
    """A provider written against the contract — what a Cloud provider is."""

    def __init__(self, user):
        self.user = user
        self.seen = None

    async def get_current_user(self, request, db):
        self.seen = (request, db)
        return self.user


async def _call(provider):
    app = FastAPI()

    @app.get("/who")
    async def who(u: User = Depends(require_user)):
        return {"id": str(u.id)}

    sentinel_db = object()

    async def fake_db():
        yield sentinel_db

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_auth_provider] = lambda: provider
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get("/who"), sentinel_db


@pytest.mark.asyncio
async def test_seam_none_is_401():
    resp, _ = await _call(_StubProvider(None))
    assert resp.status_code == 401
    assert resp.json()["detail"]["error_code"] == "unauthenticated"


@pytest.mark.asyncio
async def test_seam_user_passes_through_with_the_request_session():
    uid = uuid.uuid4()
    provider = _StubProvider(User(id=uid, email="c@example.org"))
    resp, db = await _call(provider)
    assert resp.status_code == 200 and resp.json() == {"id": str(uid)}
    assert provider.seen[1] is db  # the DB session reaches the provider (D-2)
