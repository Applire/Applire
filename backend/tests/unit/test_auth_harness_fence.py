# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The harness fence (ADR-091 cl. 3; MD-9; SF-IAM.2) — every fence, both at
startup and per request, on the real app where it matters.

(a) flag · (b) no credential anywhere (per request, 503 once claimed) ·
(c) test database: SQLite in-memory, or ``*_ci``/``*_test`` Postgres + boot latch ·
(d) it says so (``/api/auth/state.harness``).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider, provider_name
from applire.auth import harness
from applire.auth.harness import (
    STUB_USER_ID,
    HarnessAuthProvider,
    HarnessRefused,
    any_credential,
    database_fence_reason,
    enforce_at_startup,
    forget_credential_cache,
    is_sqlite_memory,
    is_test_postgres,
)
from applire.auth.local import LocalAuthProvider
from applire.main import app as fastapi_app
from applire.models.user import User


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    forget_credential_cache()
    latch = harness.boot_latch()
    yield
    harness._set_boot_latch(latch)
    forget_credential_cache()
    fastapi_app.dependency_overrides.pop(get_auth_provider, None)


@pytest.fixture
def harness_on(monkeypatch):
    monkeypatch.setattr(harness.settings, "auth_harness", True)
    monkeypatch.setattr(harness.settings, "database_url", "sqlite+aiosqlite://")
    fastapi_app.dependency_overrides[get_auth_provider] = lambda: HarnessAuthProvider()


# ---------------------------------------------------------------------------
# (a) the flag picks the provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["local", "none", "NONE", " Local "])
async def test_without_the_flag_the_factory_builds_the_local_provider(monkeypatch, value):
    monkeypatch.setattr("applire.auth.settings.auth_provider", value)
    monkeypatch.setattr("applire.auth.settings.auth_harness", False)
    assert isinstance(await get_auth_provider(), LocalAuthProvider)


@pytest.mark.asyncio
async def test_the_flag_builds_the_harness(monkeypatch):
    monkeypatch.setattr("applire.auth.settings.auth_provider", "local")
    monkeypatch.setattr("applire.auth.settings.auth_harness", True)
    assert isinstance(await get_auth_provider(), HarnessAuthProvider)
    assert provider_name() == "harness"


@pytest.mark.asyncio
async def test_an_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr("applire.auth.settings.auth_provider", "zitadel")
    with pytest.raises(ValueError, match="Unknown AUTH_PROVIDER"):
        await get_auth_provider()


# ---------------------------------------------------------------------------
# (c) the test-database proof
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,ok",
    [
        ("sqlite+aiosqlite://", True),
        ("sqlite+aiosqlite:///:memory:", True),
        ("sqlite+aiosqlite:///./test.db", False),  # a FILE survives restarts
        ("postgresql+asyncpg://a:b@postgres:5432/applire", False),
        ("postgresql+asyncpg://a:b@postgres:5432/applire_ci", True),
        ("postgresql+asyncpg://a:b@postgres:5432/unit_test", True),
        ("postgresql+asyncpg://a:b@postgres:5432/test", False),  # suffix, not the word
        ("postgresql+asyncpg://a:b@postgres:5432/applire_test_prod", False),
    ],
)
def test_the_database_proof(url, ok):
    harness._set_boot_latch(None)
    assert (database_fence_reason(url) is None) is ok


def test_a_failed_boot_latch_refuses_a_test_named_postgres():
    url = "postgresql+asyncpg://a:b@h/applire_ci"
    harness._set_boot_latch(False)
    assert database_fence_reason(url) is not None
    harness._set_boot_latch(True)
    assert database_fence_reason(url) is None
    assert is_sqlite_memory("sqlite+aiosqlite://") and not is_test_postgres("sqlite+aiosqlite://")


# ---------------------------------------------------------------------------
# Startup enforcement (backend lifespan + MCP): exit 1 on any failed fence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_startup_is_a_no_op_without_the_flag(async_db, monkeypatch):
    monkeypatch.setattr(harness.settings, "auth_harness", False)
    monkeypatch.setattr(harness.settings, "database_url", "postgresql+asyncpg://x@h/applire")
    await enforce_at_startup(async_db)  # no raise


@pytest.mark.asyncio
async def test_startup_refuses_a_production_database_name(async_db, monkeypatch):
    monkeypatch.setattr(harness.settings, "auth_harness", True)
    monkeypatch.setattr(harness.settings, "database_url", "postgresql+asyncpg://x@h/applire")
    with pytest.raises(HarnessRefused) as exc:
        await enforce_at_startup(async_db)
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_startup_refuses_when_an_account_holds_a_credential(async_db, monkeypatch):
    monkeypatch.setattr(harness.settings, "auth_harness", True)
    monkeypatch.setattr(harness.settings, "database_url", "sqlite+aiosqlite://")
    async_db.add(User(id=uuid.uuid4(), email="a@example.org", password_hash="scrypt$x"))
    await async_db.commit()
    with pytest.raises(HarnessRefused) as exc:
        await enforce_at_startup(async_db)
    assert "credential" in exc.value.reason or "password" in exc.value.reason


@pytest.mark.asyncio
async def test_startup_refuses_a_test_named_postgres_that_holds_a_vault(
    async_db, seed_profile, monkeypatch
):
    """The boot latch: an existing vault is never served without login."""
    from applire.schemas.profile import MasterProfileData

    monkeypatch.setattr(harness.settings, "auth_harness", True)
    monkeypatch.setattr(harness.settings, "database_url", "postgresql+asyncpg://x@h/applire_ci")
    await seed_profile(MasterProfileData())
    with pytest.raises(HarnessRefused) as exc:
        await enforce_at_startup(async_db)
    assert "profile" in exc.value.reason
    assert harness.boot_latch() is False


@pytest.mark.asyncio
async def test_startup_latches_an_empty_test_postgres(async_db, monkeypatch):
    monkeypatch.setattr(harness.settings, "auth_harness", True)
    monkeypatch.setattr(harness.settings, "database_url", "postgresql+asyncpg://x@h/applire_ci")
    await enforce_at_startup(async_db)
    assert harness.boot_latch() is True


# ---------------------------------------------------------------------------
# (b) per request, on the real app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_harness_serves_the_stub_as_admin_and_says_so(async_client, harness_on):
    me = await async_client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["id"] == str(STUB_USER_ID) and me.json()["role"] == "admin"
    state = await async_client.get("/api/auth/state")
    assert state.json()["harness"] is True


@pytest.mark.asyncio
async def test_harness_transient_stub_carries_the_admin_role():
    """Main-session integration fix (stub role=admin) kept as a property."""
    request = SimpleNamespace(state=SimpleNamespace())
    user = await HarnessAuthProvider().get_current_user(request, MagicMock())
    assert user.id == STUB_USER_ID and user.role == "admin"
    assert request.state.auth_via == "harness"


@pytest.mark.asyncio
async def test_once_a_credential_exists_every_request_is_503(async_client, async_db, harness_on):
    assert (await async_client.get("/api/auth/me")).status_code == 200
    async_db.add(User(id=uuid.uuid4(), email="claimer@example.org", password_hash="scrypt$x"))
    await async_db.commit()
    forget_credential_cache()  # the ≤ 5 s cache is the stated latency; skip it here
    resp = await async_client.get("/api/auth/me")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error_code"] == "harness_disabled"
    assert (await async_client.get("/api/profile")).status_code == 503


@pytest.mark.asyncio
async def test_an_oidc_binding_also_counts_as_a_credential(async_db):
    async_db.add(User(id=uuid.uuid4(), email="sso@example.org", oidc_issuer="https://i",
                      oidc_subject="s"))
    await async_db.commit()
    assert await any_credential(async_db, use_cache=False) is True


@pytest.mark.asyncio
async def test_the_harness_refuses_per_request_on_a_non_test_database(async_client, harness_on,
                                                                      monkeypatch):
    monkeypatch.setattr(harness.settings, "database_url", "postgresql+asyncpg://x@h/applire")
    resp = await async_client.get("/api/auth/me")
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_the_credential_cache_is_bounded_to_five_seconds(async_db, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(harness.time, "monotonic", lambda: clock[0])
    assert await any_credential(async_db) is False
    async_db.add(User(id=uuid.uuid4(), email="later@example.org", password_hash="scrypt$x"))
    await async_db.commit()
    clock[0] += 4.9
    assert await any_credential(async_db) is False  # cached
    clock[0] += 0.2
    assert await any_credential(async_db) is True  # > 5 s: re-read


@pytest.mark.asyncio
async def test_no_users_table_or_no_database_reads_as_no_credential():
    """In-process suites that build partial schemas or never connect: no table,
    no reachable DB ⇒ nothing to protect and nothing to serve (stated in harness.py)."""
    from sqlalchemy.exc import OperationalError

    class _Db(AsyncSession):
        def __init__(self, exc):
            self._exc = exc
            self.rolled_back = False

        def get_bind(self, *a, **k):
            return object.__new__(type("Bind", (), {}))

        async def execute(self, *a, **k):
            raise self._exc

        async def rollback(self):
            self.rolled_back = True

    for exc in (OperationalError("x", {}, Exception("no such table: users")),
                OSError("Connect call failed")):
        db = _Db(exc)
        assert await any_credential(db, use_cache=False) is False
        assert db.rolled_back
