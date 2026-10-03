# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The five auth dependencies are async, all the way down (ADR-091 cl. 4; F2).

A sync ``def`` dependency runs in a threadpool copy of the request's context:
the owner it sets (ADR-092) never reaches the endpoint, and with the guard on
every request would raise ``OwnerContextMissing`` (adversarial re-check g2).
These tests walk the dependant tree of each dependency and prove the property
on a live app: the owner context set by ``require_user`` is visible in the
endpoint, and the same body as a sync dependency is NOT.
"""

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.dependencies.utils import get_dependant
from httpx import ASGITransport, AsyncClient

from applire.auth import get_auth_provider
from applire.auth.deps import (
    AUTH_DEPENDENCIES,
    admin_or_probe,
    require_admin,
    require_session_user,
    require_user,
    user_or_signed_link,
)
from applire.db.session import get_db
from applire.ownership import current_owner, set_owner


def _is_async(fn) -> bool:
    # A coroutine function or an async generator (``get_db``): both run on the
    # event loop in the request's own context — never in the threadpool.
    return inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)


def _tree(fn) -> list:
    out = []

    def walk(dep):
        for sub in dep.dependencies:
            out.append(sub.call)
            walk(sub)

    walk(get_dependant(path="/", call=fn))
    return out


def _sync_offenders(fn) -> list[str]:
    return [getattr(c, "__qualname__", repr(c)) for c in [fn, *_tree(fn)] if not _is_async(c)]


def test_the_five_are_exactly_the_contract():
    assert {f.__name__ for f in AUTH_DEPENDENCIES} == {
        "require_user",
        "require_admin",
        "require_session_user",
        "user_or_signed_link",
        "admin_or_probe",
    }


@pytest.mark.parametrize("dep", AUTH_DEPENDENCIES, ids=lambda f: f.__name__)
def test_each_dependency_and_its_whole_tree_is_async(dep):
    assert inspect.iscoroutinefunction(dep), f"{dep.__name__} must be async def"
    assert _sync_offenders(dep) == []


def test_the_tree_includes_the_provider_override_point_and_the_session():
    tree = _tree(require_user)
    assert get_auth_provider in tree  # ADR-008: the override point stays in the tree
    assert get_db in tree


def test_the_walker_flags_a_sync_dependency():
    """Mutation guard for the test itself: a sync sub-dependency is reported."""

    def sync_provider():
        return object()

    async def leaky(p=Depends(sync_provider)):
        return p

    assert _sync_offenders(leaky) == ["test_the_walker_flags_a_sync_dependency.<locals>.sync_provider"]


# ---------------------------------------------------------------------------
# Behaviour: the owner context reaches the endpoint only from an async dependency
# ---------------------------------------------------------------------------


def _provider_for(user_id):
    p = MagicMock()
    p.get_current_user = AsyncMock(return_value=MagicMock(id=user_id, role="admin"))
    return p


def _app(dep) -> FastAPI:
    app = FastAPI()

    @app.get("/who")
    async def who(_u=Depends(dep)):
        ctx = current_owner()
        return {"owner": str(ctx.user_id) if ctx and ctx.user_id else None}

    async def no_db():
        yield None

    app.dependency_overrides[get_db] = no_db
    return app


async def _get(app, path="/who"):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


@pytest.mark.no_owner_context
@pytest.mark.asyncio
@pytest.mark.parametrize("dep", AUTH_DEPENDENCIES, ids=lambda f: f.__name__)
async def test_each_dependency_sets_the_owner_the_endpoint_sees(dep):
    uid = uuid.uuid4()
    app = _app(dep)
    app.dependency_overrides[get_auth_provider] = lambda: _provider_for(uid)
    resp = await _get(app)
    assert resp.status_code == 200
    assert resp.json() == {"owner": str(uid)}


@pytest.mark.no_owner_context
@pytest.mark.asyncio
async def test_a_sync_dependency_doing_the_same_loses_the_owner():
    """Why ``async def`` is binding: the same ``set_owner`` from a sync dependency
    runs in a threadpool context copy and never reaches the endpoint (g2)."""
    uid = uuid.uuid4()

    def sync_require_user(request: Request):
        set_owner(uid)
        return MagicMock(id=uid)

    resp = await _get(_app(sync_require_user))
    assert resp.json() == {"owner": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dep", [require_user, require_session_user, user_or_signed_link, require_admin, admin_or_probe],
    ids=lambda f: f.__name__,
)
async def test_no_user_is_401_unauthenticated(dep):
    app = _app(dep)
    p = MagicMock()
    p.get_current_user = AsyncMock(return_value=None)
    app.dependency_overrides[get_auth_provider] = lambda: p
    resp = await _get(app)
    assert resp.status_code == 401
    assert resp.json()["detail"]["error_code"] == "unauthenticated"


@pytest.mark.asyncio
async def test_require_admin_refuses_a_user_role_with_403():
    app = _app(require_admin)
    p = MagicMock()
    p.get_current_user = AsyncMock(return_value=MagicMock(id=uuid.uuid4(), role="user"))
    app.dependency_overrides[get_auth_provider] = lambda: p
    resp = await _get(app)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "forbidden"


@pytest.mark.asyncio
async def test_default_provider_is_the_harness_stub_and_passes():
    """W0: no override — the NoAuth stub resolves, the app works as before."""
    resp = await _get(_app(require_user))
    assert resp.status_code == 200
    assert resp.json()["owner"] == "00000000-0000-0000-0000-000000000001"
