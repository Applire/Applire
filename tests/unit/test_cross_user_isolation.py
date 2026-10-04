# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cross-user isolation suite (ADR-092 cl. 6, 8c; S-10; US332; System-FMEA SF-OWN.1).

Users A and B; A owns one of every resource kind (``tests.support.isolation``).
Every REST route with a resource-id path parameter is called **as B with A's ids**
and must answer exactly like a missing id — 404 — and leave A's rows unchanged.
Every MCP tool id argument and resource-template variable must be mapped, and the
MCP read tools/resources are called as B with A's ids.

**The ratchet (W1 → W3).** Scoping the routes and tools is W2 work (3b/3c/3d/4a/4b),
so in W1 most cross-user calls still reach A's rows. Those routes are listed in
``PENDING_REST`` / ``PENDING_MCP`` and run as **strict xfail**: the day a package
scopes one, its xfail turns into an unexpected pass and the suite goes red until
the entry is deleted here. The lists only shrink; 3e (W3) needs both empty.
An id parameter missing from the registry fails ``test_every_id_is_mapped`` now.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.auth import get_auth_provider
from applire.db.session import Base, get_db
from applire.main import app
from applire.models.cv import GeneratedCV
from applire.models.profile import MasterProfile
from applire.models.user import User
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse; the door signs links
from tests.support.isolation import (
    NOT_RESOURCE_PARAMS,
    RESOURCE_FACTORIES,
    OwnerWorld,
    load_package_factories,
    not_resource,
    resolve,
)

load_package_factories()

pytestmark = pytest.mark.no_owner_context

#: Placeholder values for sub-key parameters (NOT_RESOURCE_PARAMS).
SUB_KEY_VALUES = {
    "section": "skills",
    "section_id": "introduction",
    "cluster_id": "cluster-1",
    "gap_id": "cluster-1",
    "conflict_id": "conflict-1",
    "pin_id": "pin-1",
    "scheme_id": str(uuid.UUID(int=7)),
}


_FK = {"finding_key": "ats:python"}

#: A minimal VALID body per write route — a 422 proves nothing about ownership,
#: the request must reach the lookup. A new write route without a body here fails
#: with 422 and names itself.
REST_BODIES: dict[tuple[str, str], dict] = {
    ("POST", "/api/job/{job_id}/gaps/liabilities/downgrade"): {"concept": "Python"},
    ("POST", "/api/job/{job_id}/gaps/{cluster_id}/left-open"): {"left_open": True},
    ("POST", "/api/profile/staged/{staged_id}/resolve"): {"action": "discard"},
    ("POST", "/api/profile/enrich/{session_id}/respond"): {"answer": "Yes."},
    ("POST", "/api/session/{session_id}/message"): {"message": "Hello."},
    ("POST", "/api/flow/{flow_id}/advance"): {"step": "gap_analysis"},
    ("POST", "/api/cv/{cv_id}/sections/{section_id}/assist"): {"gap_id": "cluster-1"},
    ("PATCH", "/api/cv/{cv_id}/sections/{section_id}/assist"): {"session_id": str(uuid.UUID(int=9)), "answer": "Yes."},
    ("PATCH", "/api/cv/{cv_id}/sections/{section_id:path}"): {"content": "Changed by B."},
    ("PATCH", "/api/cover-letter/{cl_id}/section"): {"section": "body", "content": "Changed by B."},
    ("POST", "/api/cv/{doc_id}/review/add-evidence"): {**_FK, "text": "Evidence."},
    ("POST", "/api/cover-letter/{doc_id}/review/add-evidence"): {**_FK, "text": "Evidence."},
    **{
        ("POST", f"/api/{kind}/{{doc_id}}/review/{action}"): dict(_FK)
        for kind in ("cv", "cover-letter")
        for action in ("take-out", "undo", "edited")
    },
    ("PATCH", "/api/cv/{cv_id}/color"): {"accent_hex": "#12233E"},
    ("PATCH", "/api/applications/{application_id}"): {"notes": "Changed by B."},
    ("POST", "/api/applications/{application_id}/pins"): {
        "entry_type": "skill", "entry_id": "s1", "quote": "Python",
    },
}


def _rest_routes() -> list[APIRoute]:
    return [r for r in app.routes if isinstance(r, APIRoute) and r.param_convertors]


def _resource_routes() -> list[tuple[str, str]]:
    """(method, path) for every route with at least one resource-id parameter."""
    out = []
    for r in _rest_routes():
        if any(resolve(p, r.path) for p in r.param_convertors):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, r.path))
    return sorted(out, key=lambda mp: (mp[1], mp[0]))


def _mcp_surface() -> tuple[dict[str, list[str]], list[str]]:
    import applire.mcp.server as server

    tools = asyncio.run(server.mcp.list_tools())
    id_args = {
        t.name: [k for k in t.inputSchema.get("properties", {}) if k.endswith("_id")]
        for t in tools
    }
    templates = [t.uriTemplate for t in asyncio.run(server.mcp.list_resource_templates())]
    return {k: v for k, v in id_args.items() if v}, templates


# ---------------------------------------------------------------------------
# Ratchet lists — W2 packages delete entries as they scope their doors.
# ---------------------------------------------------------------------------

#: REST routes that, at W1, do not yet answer 404 to a foreign id — measured by
#: this suite on the W1 tree (2026-10-03: 40 of 59; the other 19 already answer 404, the GETs among them
#: backed by the owner positive control).
#: Owner package (W2) in the comment.
PENDING_REST: set[tuple[str, str]] = set()  # empty since the W2 integration

#: MCP calls not yet scoped — empty since 4b (W2): every owned id and every
#: shared-posting ``job_id`` is resolved at the door (ADR-092 cl. 5c/10, MD-23).
PENDING_MCP: set[str] = set()


# ---------------------------------------------------------------------------
# Mapping (green from W1 on)
# ---------------------------------------------------------------------------


def test_every_id_is_mapped():
    """A door taking an id that is neither a factory nor a declared sub-key fails here."""
    unmapped = []
    for r in _rest_routes():
        for p in r.param_convertors:
            if not resolve(p, r.path) and not not_resource(p, r.path):
                unmapped.append(f"REST {r.path} :: {p}")
    tools, templates = _mcp_surface()
    for tool, args in tools.items():
        for a in args:
            if not resolve(a) and a not in NOT_RESOURCE_PARAMS:
                unmapped.append(f"MCP {tool} :: {a}")
    for t in templates:
        var = t.split("{", 1)[1].rstrip("}")
        if not resolve(var):
            unmapped.append(f"MCP resource {t} :: {var}")
    assert unmapped == [], "add a factory (tests/support/owners_<pkg>.py) or a sub-key reason"


def test_the_suite_sees_the_surface():
    """Guards the enumeration itself (an empty parametrisation proves nothing)."""
    routes = _resource_routes()
    tools, templates = _mcp_surface()
    assert len(routes) >= 55
    assert len(tools) >= 15
    assert set(templates) >= {"job://{job_id}", "flow://{flow_id}", "cv://{cv_id}"}


def test_pending_lists_name_real_doors():
    """A stale ratchet entry (route renamed/removed) is an error, not silence."""
    assert PENDING_REST <= set(_resource_routes())
    tools, templates = _mcp_surface()
    assert PENDING_MCP <= set(MCP_READ_CALLS) | set(MCP_WRITE_CALLS)
    assert set(REST_BODIES) <= set(routes_ := _resource_routes()), set(REST_BODIES) - set(routes_)


# ---------------------------------------------------------------------------
# The world
# ---------------------------------------------------------------------------


class _AsUser:
    def __init__(self, user: User) -> None:
        self.user = user

    async def get_current_user(self, request, db=None):  # noqa: ANN001
        return self.user


@pytest_asyncio.fixture
async def world():
    eng = create_async_engine("sqlite+aiosqlite://")
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    with ownership.unscoped("tooling"):
        async with factory() as s:
            a = User(id=uuid.uuid4(), email="iso-a@example.org", role="user")
            b = User(id=uuid.uuid4(), email="iso-b@example.org", role="user")
            s.add_all([a, b])
            await s.flush()
            wa, wb = OwnerWorld(s, a), OwnerWorld(s, b)
            ids = {key: await f(wa) for key, f in RESOURCE_FACTORIES.items()}
            for key, f in RESOURCE_FACTORIES.items():  # B owns the same kinds
                await f(wb)
            await s.commit()
    yield factory, a, b, ids
    await eng.dispose()


async def _snapshot(factory, owner_id) -> dict[str, list[tuple]]:
    """Every owned row of ``owner_id``, all columns — compared before/after."""
    out = {}
    with ownership.unscoped("tooling"):
        async with factory() as s:
            for name in sorted(ownership.owned_tables() - {"profile_snapshots"}):
                t = Base.metadata.tables[name]
                rows = (await s.execute(select(t).where(t.c.user_id == owner_id))).all()
                out[name] = sorted(tuple(map(repr, r)) for r in rows)
    return out


def _fill_path(path: str, ids: dict[str, str]) -> str:
    route = next(r for r in _rest_routes() if r.path == path)
    url = path
    for p in route.param_convertors:
        key = resolve(p, path)
        value = ids[key] if key else SUB_KEY_VALUES[p]
        url = url.replace("{" + p + ":path}", value).replace("{" + p + "}", value)
    return url


@pytest.mark.asyncio
async def test_factories_build_rows_owned_by_their_world(world):
    factory, a, b, ids = world
    snap = await _snapshot(factory, a.id)
    kinds = {name for name, rows in snap.items() if rows}
    assert kinds >= {
        "master_profiles", "applications", "flow_sessions", "generated_cvs",
        "generated_cover_letters", "interview_sessions", "gap_analysis_jobs",
        "cv_import_jobs", "uploads",
    }


# ---------------------------------------------------------------------------
# REST — B calls every resource route with A's ids
# ---------------------------------------------------------------------------


def _rest_params():
    out = []
    for method, path in _resource_routes():
        marks = []
        if (method, path) in PENDING_REST:
            marks.append(pytest.mark.xfail(strict=True, reason="W2: route not yet owner-scoped"))
        out.append(pytest.param(method, path, marks=marks, id=f"{method} {path}"))
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", _rest_params())
async def test_rest_foreign_id_is_404_and_changes_nothing(world, method, path, monkeypatch):
    factory, a, b, ids = world
    _neutralise_side_effects(monkeypatch)
    before = await _snapshot(factory, a.id)

    async def _db():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = lambda: _AsUser(b)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await asyncio.wait_for(
                c.request(
                    method,
                    _fill_path(path, ids),
                    json=REST_BODIES.get((method, path), {}) if method in ("POST", "PATCH", "PUT") else None,
                ),
                timeout=20,
            )
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth_provider, None)
    assert resp.status_code == 404, f"{method} {path} → {resp.status_code}: {resp.text[:200]}"
    assert await _snapshot(factory, a.id) == before, "A's rows changed under B's call"


def _positive_control_params():
    return [
        pytest.param(m, p, id=f"{m} {p}")
        for m, p in _resource_routes()
        if (m, p) not in PENDING_REST
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", _positive_control_params())
async def test_rest_owner_reaches_their_own_resource(world, method, path, monkeypatch):
    """Positive control: a 404 for B only means something if A's own id is NOT 404
    — otherwise the factory built the wrong row and every B-call "passes"."""
    factory, a, b, ids = world
    _neutralise_side_effects(monkeypatch)

    async def _db():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = lambda: _AsUser(a)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            try:
                resp = await asyncio.wait_for(
                    c.request(
                        method,
                        _fill_path(path, ids),
                        json=REST_BODIES.get((method, path), {}) if method in ("POST", "PATCH", "PUT") else None,
                    ),
                    timeout=20,
                )
            except Exception:  # noqa: BLE001 — the owner's call got past the lookup
                return
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth_provider, None)
    assert resp.status_code != 404, f"{method} {path} as the OWNER → 404: {resp.text[:200]}"


def _neutralise_side_effects(monkeypatch) -> None:
    """No provider, no Chromium: a leaking route fails fast instead of rendering."""

    def _no_provider(*_a: Any, **_k: Any):
        raise RuntimeError("isolation suite: no LLM provider")

    for target in (
        "applire.providers.llm.get_provider",
        "applire.services.cv.get_provider",
        "applire.services.cover_letter.get_provider",
        "applire.routers.job.get_provider",
    ):
        try:
            monkeypatch.setattr(target, _no_provider)
        except (AttributeError, ImportError):
            pass

    async def _no_pdf(*_a: Any, **_k: Any) -> bytes:
        raise RuntimeError("isolation suite: no PDF rendering")

    for target in ("applire.services.cv._html_to_pdf", "applire.services.cover_letter._html_to_pdf"):
        try:
            monkeypatch.setattr(target, _no_pdf)
        except (AttributeError, ImportError):
            pass


# ---------------------------------------------------------------------------
# MCP — read tools and resources as B with A's ids
# ---------------------------------------------------------------------------

#: name → (tool/resource, kwargs builder).
MCP_READ_CALLS: dict[str, Any] = {
    "get_cv_status": lambda ids: {"cv_id": ids["cv_id"]},
    "get_cv_ats_report": lambda ids: {"cv_id": ids["cv_id"]},
    "get_cover_letter_status": lambda ids: {"cover_letter_id": ids["cover_letter_id"]},
    "get_cover_letter_ats_report": lambda ids: {"cover_letter_id": ids["cover_letter_id"]},
    "get_flow_state": lambda ids: {"flow_id": ids["flow_id"]},
    "get_application": lambda ids: {"application_id": ids["application_id"]},
    "audit_document cv": lambda ids: {"document_id": ids["cv_id"]},
    "audit_document letter": lambda ids: {"document_id": ids["cover_letter_id"]},
    "resource job://": lambda ids: f"job://{ids['job_id']}",
    "resource flow://": lambda ids: f"flow://{ids['flow_id']}",
    "resource cv://": lambda ids: f"cv://{ids['cv_id']}",
}

#: Write tools with an id argument — as B with A's ids (``own`` = B's own ids, for
#: the second id of a two-id call). A's rows must be unchanged afterwards.
MCP_WRITE_CALLS: dict[str, Any] = {
    "send_message": lambda ids, own: {"session_id": ids["session_id"], "message": "Hello."},
    "advance_flow": lambda ids, own: {"flow_id": ids["flow_id"], "step": "gap_analysis"},
    "advance_flow artifact_id": lambda ids, own: {
        "flow_id": own["flow_id"], "step": "gap_analysis", "artifact_id": ids["gap_analysis_id"],
    },
    "update_application": lambda ids, own: {"application_id": ids["application_id"], "notes": "B"},
    "update_application submitted_cv_id": lambda ids, own: {
        "application_id": own["application_id"], "submitted_cv_id": ids["cv_id"],
    },
    "update_application submitted_cover_letter_id": lambda ids, own: {
        "application_id": own["application_id"],
        "submitted_cover_letter_id": ids["cover_letter_id"],
    },
    "resolve_held_merge": lambda ids, own: {"staged_id": ids["staged_id"], "decision": "discard"},
    # job_id tools — the shared posting through the caller's own link (cl. 5c)
    "analyze_gaps": lambda ids, own: {"job_id": ids["job_id"]},
    "run_interview": lambda ids, own: {"job_id": ids["job_id"]},
    "resolve_gap": lambda ids, own: {"job_id": ids["job_id"], "gap_id": "cluster-1", "answer": "I ran Kubernetes."},
    "generate_cv": lambda ids, own: {"job_id": ids["job_id"]},
    "generate_cover_letter": lambda ids, own: {"job_id": ids["job_id"]},
    "render_document": lambda ids, own: {
        "document_kind": "cv", "job_id": ids["job_id"],
        "content": {
            "contact": {"name": "B", "email": "b@example.org", "location": "Berlin"},
            "summary": "Engineer.", "work_history": [], "skills": ["Python"],
            "show_photo": False,
        },
    },
    "submit_claims": lambda ids, own: {
        "claims": [{"statement": "I ran Kubernetes clusters."}], "job_id": ids["job_id"],
    },
    "create_application": lambda ids, own: {"job_id": ids["job_id"]},
    "start_flow": lambda ids, own: {"job_id": ids["job_id"]},
}


#: Calls whose miss is not ``not_found`` but the service's own refusal of a
#: missing id — the foreign id must produce the identical answer.
MCP_ANSWERS_LIKE_MISSING: dict[str, Any] = {
    "advance_flow artifact_id": lambda ids, call: (
        call["artifact_id"], {**call, "artifact_id": str(uuid.uuid4())},
    ),
}


def _mcp_params(calls):
    return [
        pytest.param(
            name,
            marks=[pytest.mark.xfail(strict=True, reason=f"W2: {PENDING_MCP_OWNER.get(name, '4b')} not yet owner-scoped")]
            if name in PENDING_MCP
            else [],
            id=name,
        )
        for name in sorted(calls)
    ]


#: Owning package of each PENDING_MCP entry (the service the tool calls).
PENDING_MCP_OWNER: dict[str, str] = {}


async def _bind_agent(factory, user):
    """Bind the MCP process identity to ``user`` through a REAL agent token, the
    way ``python -m applire.mcp`` does at start (ADR-091 cl. 17) — every call then
    re-checks it (MD-3). Returns the previous binding."""
    from applire.auth.tokens import create_token
    from applire.mcp import identity

    previous = identity.bound()
    async with factory() as s:
        _row, raw = await create_token(s, user_id=user.id, scope="agent", name="iso")
        await s.commit()
        await identity.establish(s, raw)
    return previous


async def _own_ids(factory, user) -> dict[str, str]:
    """A user's flow / application / gap-analysis ids (two-id calls pair B's own
    container with A's id)."""
    from applire.models.application import Application
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis

    out = {}
    with ownership.owner_context(user.id):
        async with factory() as s:
            for key, model in (
                ("flow_id", FlowSession), ("application_id", Application),
                ("gap_analysis_id", GapAnalysis),
            ):
                row = (await s.execute(select(model).where(model.user_id == user.id))).scalars().first()
                out[key] = str(row.id)
    return out


#: A provider any use of which fails — a door reaching it got past the lookup.
_NO_PROVIDER = object()


def _is_not_found(result) -> bool:
    """The door's miss, identical for a missing and a foreign id (S-10): a tool's
    ``McpError`` with the not-found code (-32001), or a resource read FastMCP
    wraps into ``ValueError`` around the door's not-found message."""
    from mcp.shared.exceptions import McpError

    if isinstance(result, McpError):
        return result.error.code == -32001
    return isinstance(result, ValueError) and "not found" in str(result).lower()


async def _call_mcp(server, name: str, call):
    tool = name.split(" ", 1)[0]
    if name.startswith("resource "):
        return await server.mcp.read_resource(call)
    return await getattr(server, tool)(**call)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _mcp_params(MCP_READ_CALLS) + _mcp_params(MCP_WRITE_CALLS))
async def test_mcp_foreign_id_is_not_found(world, name, monkeypatch, mcp_signing_secret):
    import contextlib

    import applire.mcp.server as server
    from applire.mcp import identity

    factory, a, b, ids = world

    @contextlib.asynccontextmanager
    async def _db():
        async with factory() as s:
            yield s

    monkeypatch.setattr(server, "get_db", _db)
    _neutralise_side_effects(monkeypatch)
    monkeypatch.setattr(server, "get_provider", lambda *a, **k: _NO_PROVIDER)
    if name in MCP_READ_CALLS:
        call = MCP_READ_CALLS[name](ids)
    else:
        call = MCP_WRITE_CALLS[name](
            {**ids, "gap_analysis_id": (await _own_ids(factory, a))["gap_analysis_id"]},
            await _own_ids(factory, b),
        )
    before = await _snapshot(factory, a.id)
    previous = await _bind_agent(factory, b)
    outcome = "returned"
    try:
        result = await _call_mcp(server, name, call)
    except Exception as exc:  # noqa: BLE001 — classify below
        result = exc
        outcome = type(exc).__name__
    finally:
        identity.bind(previous)
    text = str(result)
    if name in MCP_ANSWERS_LIKE_MISSING:
        # The door answers a foreign id EXACTLY like a missing one (S-10) —
        # here the orchestrator's own "no matching record" refusal.
        foreign_id, call_missing = MCP_ANSWERS_LIKE_MISSING[name](ids, call)
        previous = await _bind_agent(factory, b)
        try:
            missing = await _call_mcp(server, name, call_missing)
        except Exception as exc:  # noqa: BLE001
            missing = exc
        finally:
            identity.bind(previous)
        assert type(result) is type(missing) and outcome != "returned", (text, missing)
        assert result.error.code == missing.error.code
        assert text.replace(foreign_id, "<id>") == str(missing).replace(call_missing["artifact_id"], "<id>")
    else:
        assert _is_not_found(result), f"{name} as B with A's id → {outcome}: {text[:200]}"
    assert await _snapshot(factory, a.id) == before, "A's rows changed under B's MCP call"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(MCP_READ_CALLS))
async def test_mcp_owner_reaches_their_own_resource(world, name, monkeypatch, mcp_signing_secret):
    """Positive control: the same read as A, through A's own agent token, is not a
    miss — so B's ``not_found`` above is ownership, not a broken factory."""
    import contextlib

    import applire.mcp.server as server
    from applire.mcp import identity

    factory, a, b, ids = world

    @contextlib.asynccontextmanager
    async def _db():
        async with factory() as s:
            yield s

    monkeypatch.setattr(server, "get_db", _db)
    _neutralise_side_effects(monkeypatch)
    monkeypatch.setattr(server, "get_provider", lambda *a, **k: _NO_PROVIDER)
    previous = await _bind_agent(factory, a)
    try:
        try:
            result = await _call_mcp(server, name, MCP_READ_CALLS[name](ids))
        except Exception as exc:  # noqa: BLE001
            result = exc
    finally:
        identity.bind(previous)
    assert not _is_not_found(result), f"{name} as the OWNER → {str(result)[:200]}"


#: Id-less MCP reads: the caller gets their OWN rows, never another user's (the old door read "the
#: first user"/"the latest profile"). Pending: the profile read path (3b, F6).
MCP_SELF_READS: dict[str, Any] = {
    "get_profile": lambda r: r["id"],
    "resource profile://current": lambda r: r["id"],
    "list_applications": lambda r: sorted(item["id"] for item in r),
}
PENDING_MCP_SELF: dict[str, str] = {}  # empty since the W2 integration (3b's owner-keyed read path)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        pytest.param(
            n,
            marks=[pytest.mark.xfail(strict=True, reason=f"W2: {PENDING_MCP_SELF[n]} profile read path not yet owner-keyed")]
            if n in PENDING_MCP_SELF else [],
            id=n,
        )
        for n in sorted(MCP_SELF_READS)
    ],
)
async def test_mcp_id_less_reads_return_the_callers_own_rows(world, name, monkeypatch, mcp_signing_secret):
    import contextlib
    import json

    import applire.mcp.server as server
    from applire.mcp import identity
    from applire.models.application import Application
    from applire.models.profile import MasterProfile

    factory, a, b, ids = world
    # The caller is A, whose rows are the OLDER ones (the world builds A first):
    # a door that reads "the newest profile" hands A B's vault — B as the caller
    # would pass by accident of insertion order.
    caller = a

    @contextlib.asynccontextmanager
    async def _db():
        async with factory() as s:
            yield s

    monkeypatch.setattr(server, "get_db", _db)
    with ownership.unscoped("tooling"):
        async with factory() as s:
            b_profile = (await s.execute(select(MasterProfile.id).where(MasterProfile.user_id == caller.id))).scalar_one()
            b_apps = sorted(str(x) for x in (await s.execute(select(Application.id).where(Application.user_id == caller.id))).scalars())
    expected = {"get_profile": str(b_profile), "resource profile://current": str(b_profile), "list_applications": b_apps}[name]
    previous = await _bind_agent(factory, caller)
    try:
        if name.startswith("resource "):
            contents = await server.mcp.read_resource(name.split(" ", 1)[1])
            result = json.loads(list(contents)[0].content)
        else:
            result = await getattr(server, name)()
    finally:
        identity.bind(previous)
    assert MCP_SELF_READS[name](result) == expected, f"{name} as A did not return A's own rows"


#: The door resolves every OWNED id itself (ADR-092 cl. 10) — independent of the
#: service it then calls. Each case stubs the service out (an unscoped service
#: would hand A's row back) and B must still get ``not_found``: the door check is
#: its own control, not a mirror of the service's.
MCP_DOOR_PRECHECKS: dict[str, tuple[str, Any]] = {
    "get_cv_status": ("cv_svc.get_cv_status", lambda ids: {"cv_id": ids["cv_id"]}),
    "get_cv_ats_report": ("cv_svc.get_cv_ats_report", lambda ids: {"cv_id": ids["cv_id"]}),
    "get_cover_letter_status": ("cover_letter_svc.get_cover_letter_status", lambda ids: {"cover_letter_id": ids["cover_letter_id"]}),
    "get_cover_letter_ats_report": ("cover_letter_svc.get_cover_letter_ats_report", lambda ids: {"cover_letter_id": ids["cover_letter_id"]}),
    "get_flow_state": ("flow_svc.get_flow_state", lambda ids: {"flow_id": ids["flow_id"]}),
    "advance_flow": ("flow_svc.advance_flow", lambda ids: {"flow_id": ids["flow_id"], "step": "gap_analysis"}),
    "get_application": ("app_svc.get_application", lambda ids: {"application_id": ids["application_id"]}),
    "update_application": ("app_svc.patch_application", lambda ids: {"application_id": ids["application_id"], "notes": "B"}),
    "send_message": ("session_svc.send_message", lambda ids: {"session_id": ids["session_id"], "message": "Hi."}),
    "resolve_held_merge": ("profile_svc.resolve_staged_extraction", lambda ids: {"staged_id": ids["staged_id"], "decision": "discard"}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(MCP_DOOR_PRECHECKS))
async def test_mcp_door_resolves_owned_ids_itself(world, name, monkeypatch, mcp_signing_secret):
    import contextlib
    from unittest.mock import AsyncMock

    import applire.mcp.server as server
    from applire.mcp import identity

    factory, a, b, ids = world

    @contextlib.asynccontextmanager
    async def _db():
        async with factory() as s:
            yield s

    monkeypatch.setattr(server, "get_db", _db)
    monkeypatch.setattr(server, "get_provider", lambda *a, **k: _NO_PROVIDER)
    target, build = MCP_DOOR_PRECHECKS[name]
    module, attr = target.split(".")
    leaked = AsyncMock(side_effect=AssertionError("the service ran for a foreign id"))
    monkeypatch.setattr(getattr(server, module), attr, leaked)
    previous = await _bind_agent(factory, b)
    try:
        try:
            result = await getattr(server, name)(**build(ids))
        except Exception as exc:  # noqa: BLE001
            result = exc
    finally:
        identity.bind(previous)
    assert _is_not_found(result), f"{name}: {result!r}"[:300]
    assert leaked.await_count == 0


@pytest.mark.asyncio
async def test_mcp_render_on_own_posting_never_writes_into_another_vault(world, monkeypatch, mcp_signing_secret):
    """Real stdio smoke 2026-10-04: B's ``render_document`` on B's OWN posting link
    produced a CV built from A's profile and owned by A. The caller here is A (the
    older rows), so a "newest profile" read lands on B's vault."""
    import contextlib

    import applire.mcp.server as server
    from applire.mcp import identity

    factory, a, b, ids = world

    @contextlib.asynccontextmanager
    async def _db():
        async with factory() as s:
            yield s

    monkeypatch.setattr(server, "get_db", _db)
    monkeypatch.setattr(server, "get_provider", lambda *a, **k: _NO_PROVIDER)
    _neutralise_side_effects(monkeypatch)
    before_b = await _snapshot(factory, b.id)
    before_a = await _snapshot(factory, a.id)
    previous = await _bind_agent(factory, a)
    try:
        try:
            await server.render_document(**MCP_WRITE_CALLS["render_document"](ids, {}))
        except Exception:  # noqa: BLE001 — the write, not the answer, is under test
            pass
    finally:
        identity.bind(previous)
    assert await _snapshot(factory, b.id) == before_b, "A's render wrote into B's rows"
    # Positive control (W2 integration): the render really ran and wrote A's CV
    # from A's vault — a render that fails before any write would pass the
    # assertion above vacuously.
    after_a = await _snapshot(factory, a.id)
    assert len(after_a["generated_cvs"]) == len(before_a["generated_cvs"]) + 1, "the render wrote no CV"
    with ownership.unscoped("tooling"):
        async with factory() as s:
            new_cv = (
                await s.execute(
                    select(GeneratedCV).where(GeneratedCV.user_id == a.id).order_by(GeneratedCV.created_at.desc())
                )
            ).scalars().first()
            a_profile = (
                await s.execute(select(MasterProfile.id).where(MasterProfile.user_id == a.id))
            ).scalar_one()
    assert new_cv.profile_id == a_profile, "A's CV was not built from A's vault"
