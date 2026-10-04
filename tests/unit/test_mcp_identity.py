# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The MCP door's identity (US335, US327 MCP half, US328 MCP signing).

ADR-091 cl. 3 (harness fences hold for ``mcp/__main__.py``), cl. 17 (agent token:
refuse to start without a valid one; re-checked per call, MD-3), cl. 18 (every
document URL the door returns is signed), cl. 21 (MCP calls stamp
``last_active_at``); ADR-092 cl. 10 (every tool acts for that user). S-5.
Cross-user id access per tool: ``tests/unit/test_cross_user_isolation.py``.
"""

from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from mcp.shared.exceptions import McpError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire import ownership
from applire.auth import links as links_module
from applire.auth.tokens import create_token, revoke_token
from applire.db.session import Base
from applire.mcp import identity
from applire.models.user import User
from tests.support.mcp_door import (  # noqa: F401 — mcp_signing_secret is autouse
    assert_signed_document_url,
    mcp_signing_secret,
)

pytestmark = pytest.mark.no_owner_context

BASE = "http://applire.test"


@pytest_asyncio.fixture
async def door(monkeypatch):
    """A real in-memory DB wired as the door's ``get_db``; the identity unbound after."""
    import applire.mcp.server as server
    import applire.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @contextlib.asynccontextmanager
    async def _db():
        async with maker() as s:
            yield s

    monkeypatch.setattr(server, "get_db", _db)
    monkeypatch.setattr(server.settings, "applire_base_url", BASE)
    previous = identity.bound()
    identity.bind(None)
    try:
        yield maker
    finally:
        identity.bind(previous)
        await engine.dispose()


async def _person(maker, **cols) -> User:
    async with maker() as s:
        user = User(id=uuid.uuid4(), email=f"p-{uuid.uuid4().hex[:8]}@example.org", role="user")
        user.link_epoch = 0
        for k, v in cols.items():
            setattr(user, k, v)
        s.add(user)
        await s.commit()
        return user


async def _token(maker, user, scope="agent") -> tuple[uuid.UUID, str]:
    async with maker() as s:
        row, raw = await create_token(s, user_id=user.id, scope=scope, name="t")
        await s.commit()
        return row.id, raw


async def _establish(maker, raw):
    async with maker() as s:
        return await identity.establish(s, raw)


# ---------------------------------------------------------------------------
# Start: no valid token → refuse (S-5, cl. 17)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_valid_agent_token_binds_its_owner(door):
    user = await _person(door)
    _id, raw = await _token(door, user)
    bound = await _establish(door, raw)
    assert bound.user_id == user.id and bound.via == "token"
    assert identity.bound() == bound
    assert raw not in repr(bound)  # the secret never reaches a log line via repr


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["missing", "malformed", "unknown", "revoked", "api_scope", "disabled", "deleted"]
)
async def test_the_start_is_refused_without_a_valid_agent_token(door, monkeypatch, case):
    from applire.config import settings

    monkeypatch.setattr(settings, "auth_harness", False)
    now = datetime.now(timezone.utc)
    user = await _person(
        door,
        **({"disabled_at": now} if case == "disabled" else {}),
        **({"deleted_at": now} if case == "deleted" else {}),
    )
    token_id, raw = await _token(door, user, scope="api" if case == "api_scope" else "agent")
    if case == "revoked":
        async with door() as s:
            await revoke_token(s, token_id=token_id, scopes=["agent"], user_id=user.id)
            await s.commit()
    raw = {
        "missing": "",
        "malformed": "not-a-token",
        "unknown": "apl_abcdefgh_" + "A" * 43,
    }.get(case, raw)
    with pytest.raises(identity.AgentStartRefused) as exc:
        await _establish(door, raw)
    assert str(exc.value) == identity.START_REFUSED_MESSAGE
    assert identity.bound() is None


@pytest.mark.asyncio
async def test_without_a_token_the_harness_binds_the_stub_only_when_the_flag_is_on(door, monkeypatch):
    from applire.auth.harness import STUB_USER_ID
    from applire.config import settings

    monkeypatch.setattr(settings, "auth_harness", True)
    assert (await _establish(door, None)).via == "harness"
    assert identity.bound().user_id == STUB_USER_ID
    identity.bind(None)
    # a given token always wins over the harness
    user = await _person(door)
    _id, raw = await _token(door, user)
    assert (await _establish(door, raw)).user_id == user.id


def test_main_prints_the_refusal_to_stderr_and_exits_1(monkeypatch, capsys):
    import applire.mcp.__main__ as entry

    async def _refuse():
        raise identity.AgentStartRefused(identity.START_REFUSED_MESSAGE)

    monkeypatch.setattr(entry, "_startup", _refuse)
    ran = []
    import applire.mcp.server as server

    monkeypatch.setattr(server.mcp, "run", lambda **k: ran.append(k))
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 1
    err = capsys.readouterr()
    assert identity.START_REFUSED_MESSAGE in err.err
    assert err.out == ""  # stdout is the protocol channel
    assert ran == []  # never served


def test_main_exits_1_when_the_harness_fence_fails(monkeypatch, capsys):
    import applire.mcp.__main__ as entry
    from applire.auth.harness import HarnessRefused

    async def _refuse():
        raise HarnessRefused("not a test database")

    monkeypatch.setattr(entry, "_startup", _refuse)
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 1
    assert "not a test database" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_startup_runs_the_fences_then_the_secret_then_the_identity(monkeypatch):
    """INTEGRATION-CHECKLIST W2: ``harness.enforce_at_startup`` and
    ``load_instance_secret`` run in ``mcp/__main__.py`` before the identity."""
    import applire.auth.harness as harness
    import applire.auth.links as links
    import applire.db.session as db_session
    import applire.mcp.__main__ as entry
    import applire.mcp.deps as deps

    calls = []

    @contextlib.asynccontextmanager
    async def _db():
        yield "db"

    async def _fence(db, *, door="http"):
        calls.append(("fence", db))
        assert door == "stdio", "MD-26: the stdio process names its door"

    async def _secret(db):
        calls.append(("secret", db))

    async def _establish_stub(db, raw):
        calls.append(("identity", db))

    class _Engine:
        async def dispose(self):
            calls.append(("dispose", None))

    monkeypatch.setattr(deps, "get_db", _db)
    monkeypatch.setattr(harness, "enforce_at_startup", _fence)
    monkeypatch.setattr(links, "load_instance_secret", _secret)
    monkeypatch.setattr(identity, "establish", _establish_stub)
    monkeypatch.setattr(db_session, "engine", _Engine())
    await entry._startup()
    assert [c[0] for c in calls] == ["fence", "secret", "identity", "dispose"]


# ---------------------------------------------------------------------------
# Per call: re-checked (MD-3), acts as that user (ADR-092 cl. 10)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoking_the_token_refuses_the_next_call(door):
    import applire.mcp.server as server

    user = await _person(door)
    token_id, raw = await _token(door, user)
    await _establish(door, raw)
    assert await server.list_applications() == []  # a live call works
    async with door() as s:
        await revoke_token(s, token_id=token_id, scopes=["agent"], user_id=user.id)
        await s.commit()
    with pytest.raises(McpError) as exc:
        await server.list_applications()
    assert exc.value.error.code == -32003
    assert exc.value.error.message == identity.CALL_REFUSED_MESSAGE


@pytest.mark.asyncio
async def test_disabling_the_account_refuses_the_next_call(door):
    import applire.mcp.server as server

    user = await _person(door)
    _id, raw = await _token(door, user)
    await _establish(door, raw)
    await server.get_guide()
    async with door() as s:
        row = await s.get(User, user.id)
        row.disabled_at = datetime.now(timezone.utc)
        await s.commit()
    with pytest.raises(McpError) as exc:
        await server.get_guide()
    assert exc.value.error.code == -32003


@pytest.mark.asyncio
async def test_a_data_resource_is_refused_after_revocation_too(door):
    import applire.mcp.server as server

    user = await _person(door)
    token_id, raw = await _token(door, user)
    await _establish(door, raw)
    async with door() as s:
        await revoke_token(s, token_id=token_id, scopes=["agent"], user_id=user.id)
        await s.commit()
    with pytest.raises(Exception) as exc:
        await server.mcp.read_resource("profile://current")
    assert "no longer valid" in str(exc.value)


@pytest.mark.asyncio
async def test_the_harness_binding_is_refused_once_the_instance_is_claimed(door, monkeypatch):
    import applire.mcp.server as server
    from applire.auth import harness
    from applire.config import settings

    monkeypatch.setattr(settings, "auth_harness", True)
    await _establish(door, None)
    harness.forget_credential_cache()
    await server.get_guide()
    await _person(door, password_hash="scrypt$15$8$1$x$y")  # someone claimed it
    harness.forget_credential_cache()
    with pytest.raises(McpError) as exc:
        await server.get_guide()
    assert exc.value.error.code == -32003
    assert "instance claimed" in exc.value.error.message


@pytest.mark.asyncio
async def test_unbound_in_process_use_is_refused_without_the_harness(monkeypatch):
    import applire.mcp.server as server
    from applire.config import settings

    previous = identity.bound()
    identity.bind(None)
    monkeypatch.setattr(settings, "auth_harness", False)
    try:
        with pytest.raises(McpError) as exc:
            await server.get_guide()
    finally:
        identity.bind(previous)
    assert exc.value.error.code == -32003


@pytest.mark.asyncio
async def test_unbound_in_process_use_is_refused_on_a_non_test_database(monkeypatch):
    import applire.mcp.server as server
    from applire.config import settings

    previous = identity.bound()
    identity.bind(None)
    monkeypatch.setattr(settings, "auth_harness", True)
    monkeypatch.setattr(settings, "database_url", "postgresql+asyncpg://u:p@db/applire")
    try:
        with pytest.raises(McpError) as exc:
            await server.get_guide()
    finally:
        identity.bind(previous)
    assert exc.value.error.code == -32003


@pytest.mark.asyncio
async def test_a_call_runs_in_the_owner_context_of_its_user_and_leaves_none_behind(door):
    import applire.mcp.server as server

    user = await _person(door)
    _id, raw = await _token(door, user)
    await _establish(door, raw)
    seen = {}

    async def _probe():
        seen["owner"] = ownership.current_owner()
        seen["uid"] = await server._current_user_id()
        return {}

    await server._agent_call(_probe)()
    assert seen["owner"].user_id == user.id and seen["uid"] == user.id
    assert identity.current_user() is None


@pytest.mark.asyncio
async def test_an_mcp_call_stamps_last_active_at_at_most_hourly(door):
    import applire.mcp.server as server

    user = await _person(door)
    _id, raw = await _token(door, user)
    await _establish(door, raw)
    await server.get_guide()
    async with door() as s:
        first = (await s.get(User, user.id)).last_active_at
    assert first is not None
    await server.get_guide()
    async with door() as s:
        assert (await s.get(User, user.id)).last_active_at == first
        row = await s.get(User, user.id)
        row.last_active_at = datetime.now(timezone.utc) - timedelta(hours=2)
        await s.commit()
    await server.get_guide()
    async with door() as s:
        later = (await s.get(User, user.id)).last_active_at
    assert later.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) - timedelta(minutes=1)


# ---------------------------------------------------------------------------
# Signed document links (cl. 18)
# ---------------------------------------------------------------------------


def _user(epoch=0):
    u = User(id=uuid.uuid4(), email="s@example.org")
    u.link_epoch = epoch
    return u


def test_every_document_url_in_a_payload_is_signed_and_nothing_else_is_touched():
    from applire.mcp.server import _sign_document_urls

    user = _user()
    cv_id, cl_id = uuid.uuid4(), uuid.uuid4()
    payload = {
        "html_url": f"{BASE}/api/cv/{cv_id}/html",
        "nested": [{"pdf_url": f"{BASE}/api/cover-letter/{cl_id}/pdf"}],
        "docx_url": f"{BASE}/api/cv/{cv_id}/docx",
        "source_url": "https://jobs.example.org/123",
        "text": f"see {BASE}/api/cv/{cv_id}/pdf",  # prose, not a link value
        "n": 3,
    }
    out = _sign_document_urls(payload, user)
    assert_signed_document_url(out["html_url"], payload["html_url"], user_id=user.id)
    assert_signed_document_url(out["nested"][0]["pdf_url"], payload["nested"][0]["pdf_url"], user_id=user.id)
    assert_signed_document_url(out["docx_url"], payload["docx_url"], user_id=user.id)
    assert out["source_url"] == payload["source_url"]
    assert out["text"] == payload["text"] and out["n"] == 3


@pytest.mark.asyncio
async def test_a_door_signed_link_verifies_for_its_owner_and_kind(door):
    """Round trip through 1c's verifier: the door's link opens the document."""
    from urllib.parse import parse_qs, urlsplit

    from applire.mcp.server import _sign_document_urls
    from tests.support.owners_1c import make_document

    user = await _person(door)
    async with door() as s:
        doc = await make_document(s, "cover_letter", user)
    url = _sign_document_urls({"u": f"{BASE}/api/cover-letter/{doc.id}/pdf"}, user)["u"]
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    async with door() as s:
        ok = await links_module.verify_document_link("cover_letter", doc.id, q["exp"], q["sig"], s, q["uid"])
        wrong_kind = await links_module.verify_document_link("cv", doc.id, q["exp"], q["sig"], s, q["uid"])
    assert ok is not None and ok.id == user.id
    assert wrong_kind is None


def test_without_the_instance_secret_the_door_fails_closed():
    from applire.mcp.server import _sign_document_urls

    links_module.set_instance_secret(None)
    with pytest.raises(McpError) as exc:
        _sign_document_urls({"pdf_url": f"{BASE}/api/cv/{uuid.uuid4()}/pdf"}, _user())
    assert "could not sign" in exc.value.error.message
    # a payload without a document link needs no secret
    assert _sign_document_urls({"a": "b"}, _user()) == {"a": "b"}


@pytest.mark.asyncio
async def test_get_cv_status_re_signs_the_rest_services_urls(door):
    """ADR-091 cl. 18 'explicit re-sign step': the service's unsigned REST URLs leave
    the door signed for the acting user."""
    import applire.mcp.server as server
    from tests.support.isolation import OwnerWorld

    user = await _person(door)
    async with door() as s:
        with ownership.owner_context(user.id):
            cv = await OwnerWorld(s, await s.get(User, user.id)).cv()
            await s.commit()
    _id, raw = await _token(door, user)
    await _establish(door, raw)
    result = await server.get_cv_status(str(cv.id))
    for key, fmt in (("html_url", "html"), ("pdf_url", "pdf")):
        assert_signed_document_url(result[key], f"{BASE}/api/cv/{cv.id}/{fmt}", user_id=user.id)


@pytest.mark.asyncio
async def test_the_flow_resource_signs_its_cv_link(door):
    import json

    import applire.mcp.server as server
    from tests.support.isolation import OwnerWorld

    user = await _person(door)
    async with door() as s:
        with ownership.owner_context(user.id):
            world = OwnerWorld(s, await s.get(User, user.id))
            flow = await world.flow()
            flow.generated_cv_id = (await world.cv()).id
            await s.commit()
            cv_id = flow.generated_cv_id
    _id, raw = await _token(door, user)
    await _establish(door, raw)
    contents = await server.mcp.read_resource(f"flow://{flow.id}")
    # (a JSON body must come back intact: a loose URL match once re-signed the
    # whole serialised string and broke the JSON)
    body = json.loads(list(contents)[0].content)
    assert_signed_document_url(body["cv_summary"]["pdf_url"], f"{BASE}/api/cv/{cv_id}/pdf", user_id=user.id)


def test_every_url_returning_tool_goes_through_the_signing_wrapper():
    """Structural pin: every registered tool is the identity wrapper (which signs),
    so no tool can return an unsigned document URL by being registered bare."""
    import asyncio

    import applire.mcp.server as server

    tools = asyncio.run(server.mcp.list_tools())
    unwrapped = [
        t.name for t in tools
        if getattr(server.mcp._tool_manager.get_tool(t.name).fn, "__wrapped__", None) is None
    ]
    assert unwrapped == []
    assert len(tools) >= 30
