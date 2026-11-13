# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-096 phase 1 (Sorbet WP-K) — every door that starts model work, over HTTP/MCP.

One named seam test per door (the seven router wrappers, the MCP model-work
tools, the background hand-off), driving the real auth dependency chain
(``require_user`` → ``_resolve`` → ``load_facts`` → ``admit``). Plus the
``/api/me/provider-key`` contract, the admin grant and the panel's refusal.
"""

from __future__ import annotations

import ast
import importlib
import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import BackgroundTasks, Depends, FastAPI
from sqlalchemy import select

from applire.auth import get_auth_provider, links
from applire.config import settings
from applire.db.session import get_db
from applire.models.allowance import UserProviderKey
from applire.models.audit import AuditEvent
from applire.models.user import User
from applire.providers.llm import get_provider, unwrap_provider
from applire.services import allowance, provider_keys
from tests.support.owners_1b import add_user, client_for

APPLIRE = Path(__file__).resolve().parents[2] / "applire"
SECRET = "provider-key-secret-for-door-tests-0123456789"
USER_KEY = "sk-door-user-KEY-ABCDEFGHIJKL-0123456789"
ORIGIN = {"Origin": "http://applire.test"}

#: The seven router modules whose ``_get_provider`` wrapper is the REST door.
DOOR_MODULES = ("cv", "cover_letter", "job", "profile_enrich", "session", "profile", "review")


class Acting:
    def __init__(self) -> None:
        self.user_id: uuid.UUID | None = None
        self.via = "session"

    async def get_current_user(self, request, db):
        if self.user_id is None:
            return None
        request.state.auth_via = self.via
        return await db.get(User, self.user_id)


@pytest.fixture(autouse=True)
def _clean():
    previous = links._instance_secret
    links.set_instance_secret("door-tests-instance-secret-0123456789")
    allowance.reset_context_for_tests()
    allowance._RECENT_KEYS.clear()
    yield
    allowance.reset_context_for_tests()
    allowance._RECENT_KEYS.clear()
    links._instance_secret = previous


@pytest.fixture
def user_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_key_mode", "user")
    monkeypatch.setattr(settings, "provider_key_secret", SECRET)
    monkeypatch.setattr(settings, "llm_provider", "mock")


def _door_app(async_db) -> tuple[FastAPI, Acting]:
    from applire.routers import me_provider_key
    from applire.routers.admin import users as admin_users

    app = FastAPI()
    app.include_router(me_provider_key.router)
    app.include_router(admin_users.router)
    for name in DOOR_MODULES:
        module = importlib.import_module(f"applire.routers.{name}")

        def _route(module=module):
            async def door(provider=Depends(module._get_provider)):
                inner = unwrap_provider(provider)
                return {"payer": inner._payer, "model": getattr(inner, "_model", None)}

            return door

        app.add_api_route(f"/door/{name}", _route(), methods=["POST"])

    async def background_door(tasks: BackgroundTasks, _=Depends(module._get_provider)):
        seen: dict = {}

        def later():
            with patch("applire.providers.llm.mistral.Mistral"):
                seen["payer"] = unwrap_provider(get_provider())._payer

        tasks.add_task(later)
        app.state.seen = seen
        return {}

    app.add_api_route("/door/background", background_door, methods=["POST"])
    acting = Acting()

    async def _db():
        yield async_db

    async def _provider():
        return acting

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _provider
    return app, acting


@pytest_asyncio.fixture
async def env(async_db):
    app, acting = _door_app(async_db)
    person = await add_user(async_db, email="tester@example.org")
    acting.user_id = person.id
    async with client_for(app) as client:
        yield async_db, client, acting, person, app


async def _store_key(db, person, family="mistral", model="mistral-small-latest"):
    from applire.ownership import owner_context

    with owner_context(person.id):
        await provider_keys.store(db, user_id=person.id, family=family, model=model, key=USER_KEY)
        await db.commit()


# ── 1. one seam test per REST door ─────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOOR_MODULES)
async def test_rest_door_refuses_a_person_with_neither_key_nor_grant(env, user_mode, door):
    _, client, *_ = env
    r = await client.post(f"/door/{door}", headers=ORIGIN)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["error_code"] == "provider_key_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOOR_MODULES)
async def test_rest_door_pays_with_the_persons_key(env, user_mode, door):
    db, client, _, person, _ = env
    await _store_key(db, person)
    with patch("applire.providers.llm.mistral.Mistral"):
        r = await client.post(f"/door/{door}", headers=ORIGIN)
    assert r.status_code == 200, r.text
    assert r.json() == {"payer": "user", "model": "mistral-small-latest"}
    assert USER_KEY not in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOOR_MODULES)
async def test_rest_door_pays_instance_for_a_granted_person(env, user_mode, door):
    db, client, _, person, _ = env
    person.instance_key_granted_at = person.created_at or __import__("datetime").datetime.now()
    await db.commit()
    r = await client.post(f"/door/{door}", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["payer"] == "instance"


@pytest.mark.asyncio
@pytest.mark.parametrize("door", DOOR_MODULES)
async def test_rest_door_on_the_default_is_unchanged(env, monkeypatch, door):
    """Community default: instance mode, no secret, no key table read."""
    _, client, *_ = env
    monkeypatch.setattr(settings, "llm_provider", "mock")
    assert settings.llm_key_mode == "instance"
    with patch.object(provider_keys, "load_row", AsyncMock(side_effect=AssertionError("read"))):
        r = await client.post(f"/door/{door}", headers=ORIGIN)
    assert r.status_code == 200 and r.json()["payer"] == "instance"


def test_every_router_wrapper_depends_on_admission():
    """Static half: every ``_get_provider`` in routers/ declares admit_model_work."""
    from applire.auth.deps import admit_model_work

    found = []
    for path in sorted((APPLIRE / "routers").rglob("*.py")):
        if "def _get_provider" not in path.read_text("utf-8"):
            continue
        rel = path.relative_to(APPLIRE / "routers").with_suffix("")
        module = importlib.import_module("applire.routers." + ".".join(rel.parts))
        default = module._get_provider.__defaults__ or ()
        assert any(getattr(d, "dependency", None) is admit_model_work for d in default), rel
        found.append(rel.stem)
    assert sorted(found) == sorted(DOOR_MODULES)


@pytest.mark.asyncio
async def test_background_work_keeps_the_doors_decision(env, user_mode):
    db, client, _, person, app = env
    await _store_key(db, person)
    with patch("applire.providers.llm.mistral.Mistral"):
        r = await client.post("/door/background", headers=ORIGIN)
    assert r.status_code == 200
    assert app.state.seen == {"payer": "user"}


# ── 2. the MCP door ────────────────────────────────────────────────────────────


def _admitting_tools() -> dict[str, str | None]:
    tree = ast.parse((APPLIRE / "mcp" / "server.py").read_text("utf-8"))
    out: dict[str, str | None] = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and getattr(deco.func, "id", None) == "agent_tool":
                admits = next((k.value.value for k in deco.keywords if k.arg == "admits"), None)
                out[node.name] = admits
    return out


def _calls_get_provider(name: str) -> bool:
    tree = ast.parse((APPLIRE / "mcp" / "server.py").read_text("utf-8"))
    fn = next(n for n in tree.body if getattr(n, "name", None) == name)
    return any(
        isinstance(n, ast.Call) and getattr(n.func, "id", None) == "get_provider" for n in ast.walk(fn)
    )


def test_every_mcp_tool_that_builds_a_provider_is_admitted():
    tools = _admitting_tools()
    assert tools, "no agent tools found"
    for name, admits in tools.items():
        if _calls_get_provider(name):
            assert admits == "model_work", name
    assert sum(1 for a in tools.values() if a) == 13


@pytest.mark.asyncio
async def test_mcp_model_work_tool_refuses_before_its_body_runs(monkeypatch, user_mode):
    from mcp.shared.exceptions import McpError

    from applire.mcp import server

    person = MagicMock(spec=User)
    person.id = uuid.uuid4()

    async def facts(db, user):
        f = allowance.PayerFacts(owner_id=user.id, mode="user")
        allowance._facts.set(f)
        return f

    body = AsyncMock()
    with patch.object(server.mcp_identity, "bound", return_value=None), patch.object(
        server.mcp_identity, "revalidate", AsyncMock(return_value=person)
    ), patch.object(server.allowance, "load_facts", facts), patch.object(
        server.job_svc, "analyze_jd", body
    ), patch.object(server, "get_db", MagicMock()), patch.object(
        server, "get_provider", MagicMock(name="factory")
    ) as factory:
        with pytest.raises(McpError) as exc:
            await server.analyze_jd(text="Backend Engineer at Example GmbH")
    assert exc.value.error.code == -32004
    assert exc.value.error.message.startswith("provider_key_required")
    # Refused BEFORE the body: neither the factory nor the service ran. (The
    # factory's safety net would refuse too, but only after a tool's earlier
    # writes — import_cv records its upload first.)
    factory.assert_not_called()
    body.assert_not_awaited()
    assert allowance.current_decision() is None and allowance.current_facts() is None


# ── 3. /api/me/provider-key ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_says_who_pays_and_never_the_key(env, user_mode):
    db, client, _, person, _ = env
    r = await client.get("/api/me/provider-key")
    assert r.json()["payer"] == "none" and r.json()["is_set"] is False
    await _store_key(db, person)
    r = await client.get("/api/me/provider-key")
    body = r.json()
    assert body["payer"] == "user" and body["is_set"] is True and body["family"] == "mistral"
    assert USER_KEY not in r.text and USER_KEY[-4:] not in r.text


@pytest.mark.asyncio
async def test_put_stores_ciphertext_audits_and_never_echoes(env, user_mode, caplog):
    db, client, _, person, _ = env
    caplog.set_level(logging.DEBUG)
    r = await client.put(
        "/api/me/provider-key",
        json={"family": "OpenRouter", "model": "z-ai/glm", "key": USER_KEY},
        headers=ORIGIN,
    )
    assert r.status_code == 200, r.text
    assert USER_KEY not in r.text
    row = (await db.execute(select(UserProviderKey))).scalar_one()
    assert row.family == "openrouter" and USER_KEY not in row.secret_ciphertext
    assert provider_keys.decrypt(row.secret_ciphertext) == USER_KEY
    (ev,) = (await db.execute(select(AuditEvent).where(AuditEvent.action == "provider_key.stored"))).scalars()
    assert ev.detail == {"family": "openrouter", "tested": False}
    assert USER_KEY not in caplog.text


@pytest.mark.asyncio
async def test_a_failed_key_test_stores_nothing_and_never_quotes_the_provider(env, user_mode):
    db, client, *_ = env

    class Echo:
        async def acomplete(self, *a, **k):
            raise RuntimeError(f"401 Unauthorized: Incorrect API key provided: {USER_KEY}")

    with patch("applire.providers.llm.build_user_provider", return_value=Echo()):
        r = await client.put(
            "/api/me/provider-key",
            json={"family": "openai", "model": "gpt-x", "key": USER_KEY, "test": True},
            headers=ORIGIN,
        )
    assert r.status_code == 422
    assert r.json()["detail"]["error_code"] == "provider_key_test_failed"
    assert r.json()["detail"]["reason"] == "key_rejected"
    assert USER_KEY not in r.text
    assert (await db.execute(select(UserProviderKey))).first() is None


@pytest.mark.asyncio
async def test_put_without_the_secret_is_503_and_stores_nothing(env, monkeypatch):
    db, client, *_ = env
    monkeypatch.setattr(settings, "provider_key_secret", "")
    r = await client.put(
        "/api/me/provider-key", json={"family": "mistral", "model": "m", "key": USER_KEY}, headers=ORIGIN
    )
    assert r.status_code == 503 and r.json()["detail"]["error_code"] == "provider_key_secret_missing"
    assert (await db.execute(select(UserProviderKey))).first() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,code",
    [
        ({"family": "ollama", "model": "m", "key": "k"}, "invalid_provider_family"),
        ({"family": "mistral", "model": "", "key": "k"}, "invalid_model"),
        ({"family": "mistral", "model": "m\nx", "key": "k"}, "invalid_model"),
        ({"family": "mistral", "model": "m", "key": "has space"}, "invalid_provider_key"),
        ({"family": "mistral", "model": "m", "key": "x" * 513}, "invalid_provider_key"),
        (["not", "a", "dict"], "invalid_body"),
    ],
)
async def test_put_validation_never_quotes_the_key(env, user_mode, body, code):
    _, client, *_ = env
    r = await client.put("/api/me/provider-key", json=body, headers=ORIGIN)
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == code
    if isinstance(body, dict) and len(str(body.get("key"))) > 3:
        assert str(body["key"]) not in r.text


@pytest.mark.asyncio
async def test_an_api_token_cannot_write_or_remove_a_key(env, user_mode):
    _, client, acting, *_ = env
    acting.via = "bearer"
    r = await client.put(
        "/api/me/provider-key", json={"family": "mistral", "model": "m", "key": USER_KEY}, headers=ORIGIN
    )
    assert r.status_code == 403
    assert (await client.delete("/api/me/provider-key", headers=ORIGIN)).status_code == 403
    assert (await client.get("/api/me/provider-key")).status_code == 200


@pytest.mark.asyncio
async def test_delete_removes_the_key_and_the_person_is_refused_again(env, user_mode):
    db, client, _, person, _ = env
    await _store_key(db, person)
    assert (await client.delete("/api/me/provider-key", headers=ORIGIN)).status_code == 204
    assert (await client.delete("/api/me/provider-key", headers=ORIGIN)).status_code == 204
    assert (await client.post("/door/cv", headers=ORIGIN)).status_code == 409


# ── 4. the admin grant ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_grant_and_revoke_take_effect_on_the_next_action_and_are_audited(env, user_mode):
    db, client, acting, person, _ = env
    admin = await add_user(db, email="admin@example.org", role="admin")

    async def as_admin(method, url, **kw):
        acting.user_id = admin.id
        try:
            return await client.request(method, url, headers=ORIGIN, **kw)
        finally:
            acting.user_id = person.id

    assert (await client.post("/door/job", headers=ORIGIN)).status_code == 409
    r = await as_admin("PATCH", f"/api/admin/users/{person.id}", json={"instance_key_granted": True})
    assert r.status_code == 200 and r.json()["instance_key_granted_at"] is not None
    first = r.json()["instance_key_granted_at"]
    r = await as_admin("PATCH", f"/api/admin/users/{person.id}", json={"instance_key_granted": True})
    assert r.json()["instance_key_granted_at"] == first  # idempotent
    assert (await client.post("/door/job", headers=ORIGIN)).json()["payer"] == "instance"
    r = await as_admin("PATCH", f"/api/admin/users/{person.id}", json={"instance_key_granted": False})
    assert r.json()["instance_key_granted_at"] is None
    assert (await client.post("/door/job", headers=ORIGIN)).status_code == 409
    actions = [
        e.action
        for e in (await db.execute(select(AuditEvent).order_by(AuditEvent.at))).scalars()
        if e.action.startswith("user.instance_key")
    ]
    assert actions == ["user.instance_key_granted", "user.instance_key_revoked"]


@pytest.mark.asyncio
async def test_a_person_cannot_grant_themselves(env, user_mode):
    _, client, _, person, _ = env
    r = await client.patch(
        f"/api/admin/users/{person.id}", json={"instance_key_granted": True}, headers=ORIGIN
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_list_shows_a_key_flag_never_the_key(env, user_mode):
    db, client, acting, person, _ = env
    await _store_key(db, person)
    admin = await add_user(db, email="admin2@example.org", role="admin")
    acting.user_id = admin.id
    r = await client.get("/api/admin/users")
    me = next(u for u in r.json()["users"] if u["id"] == str(person.id))
    assert me["has_provider_key"] is True and me["instance_key_granted_at"] is None
    assert USER_KEY not in r.text and "mistral-small" not in r.text


# ── 5. erasure ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_account_erasure_removes_the_key_and_detaches_the_ledger(env, user_mode):
    from applire.models.allowance import AllowanceLedger
    from applire.ownership import unscoped
    from applire.services import erasure

    db, _, _, person, _ = env
    await _store_key(db, person)
    person.instance_key_granted_at = __import__("datetime").datetime.now()
    person.allowance_subject = "a" * 64
    with unscoped("tooling"):
        await allowance.grant(
            db, subject="a" * 64, action="application", amount=3, source="admin", user_id=person.id
        )
        await db.commit()
    counts = await erasure.erase(db, person.id, "account")
    await db.commit()
    assert counts.get("user_provider_keys") == 1
    assert counts.get("allowance_ledger_detached") == 1
    db.expire_all()
    with unscoped("tooling"):
        assert (await db.execute(select(UserProviderKey))).first() is None
        row = (await db.execute(select(AllowanceLedger))).scalar_one()
    assert row.user_id is None and row.subject_hash == "a" * 64


# ── 6. the two ownerless/indirect seams cl. 12 names ───────────────────────────


def _recording_factory(seen: list):
    import applire.providers.llm as llm

    real = llm.get_provider

    def record():
        provider = real()
        seen.append(unwrap_provider(provider)._payer)
        return provider

    return record


@pytest.mark.asyncio
@pytest.mark.no_owner_context
async def test_ops_probe_pays_as_system_in_user_mode(monkeypatch, user_mode):
    import contextvars

    import applire.providers.llm as llm
    from applire.services.ops import config as ops_config
    from applire.services.ops import probes

    seen: list = []
    monkeypatch.setattr(llm, "get_provider", _recording_factory(seen))
    monkeypatch.setattr(ops_config, "provider_probe_enabled", lambda: True)
    monkeypatch.setattr(ops_config, "reachability_probe_enabled", lambda: True)

    async def no_credit(family):
        return "n/a", {}

    monkeypatch.setattr(probes, "_probe_credit", no_credit)
    # The real kick runs in an EMPTY context (no owner, no decision).
    task = __import__("asyncio").get_running_loop().create_task(
        probes._kicked_provider_probe(), context=contextvars.Context()
    )
    result = await task
    assert seen == ["system"], result


@pytest.mark.asyncio
async def test_colour_fallback_under_a_cv_render_pays_with_the_actions_payer(monkeypatch, user_mode):
    from applire.ownership import owner_context
    from applire.services import color_detection

    import applire.providers as providers_pkg

    seen: list = []
    monkeypatch.setattr(providers_pkg, "get_provider", _recording_factory(seen))
    owner = uuid.uuid4()
    with owner_context(owner):
        # A user-paid render: the fallback runs on the person's key.
        allowance._decision.set(
            allowance.PayerDecision("user", "model_work", owner, family="mistral", model="m", key=USER_KEY)
        )
        with patch("applire.providers.llm.mistral.Mistral"):
            await color_detection._llm_color_fallback("Example GmbH")
        allowance.reset_context_for_tests()
        # No door, no facts, user mode: refused — the fallback answers None and
        # no instance-paid provider is ever built.
        assert await color_detection._llm_color_fallback("Example GmbH") is None
    assert seen == ["user"]


# ── 7. the two job-start doors (cl. 4: create_import_job, the gap job) ─────────


def _dependency_calls(dependant) -> set:
    out = set()
    for sub in dependant.dependencies:
        out.add(sub.call)
        out |= _dependency_calls(sub)
    return out


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/profile/import-jobs"),
        ("POST", "/api/job/{job_id}/gap-jobs"),
        ("POST", "/api/profile/staged/{staged_id}/resolve"),
    ],
)
def test_job_start_routes_admit_before_the_202(method, path):
    from fastapi.routing import APIRoute

    from applire.auth.deps import admit_model_work
    from applire.main import app

    route = next(
        r for r in app.routes if isinstance(r, APIRoute) and r.path == path and method in r.methods
    )
    assert admit_model_work in _dependency_calls(route.dependant), path


@pytest.mark.asyncio
async def test_gap_job_start_is_refused_with_409_not_a_failed_job(async_db, user_mode):
    from applire.routers import job as job_router

    app = FastAPI()
    app.include_router(job_router.router)
    acting = Acting()
    person = await add_user(async_db)
    acting.user_id = person.id

    async def _db():
        yield async_db

    async def _provider():
        return acting

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _provider
    with patch.object(job_router, "create_gap_job", AsyncMock(side_effect=AssertionError("job"))):
        async with client_for(app) as client:
            r = await client.post(f"/api/job/{uuid.uuid4()}/gap-jobs", headers=ORIGIN)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "provider_key_required"


@pytest.mark.parametrize("module", ["applire.services.gap_jobs", "applire.services.profile.import_jobs"])
def test_a_refusal_inside_a_job_keeps_its_code(module):
    mod = importlib.import_module(module)
    classify = getattr(mod, "classify_gap_error", None) or getattr(mod, "classify_import_error")
    assert classify(allowance.AdmissionRefused("provider_key_unreadable")) == "provider_key_unreadable"
