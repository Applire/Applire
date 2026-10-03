# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Personal tokens — agent, api, probe (US327 HTTP half, US329 probe, US337;
ADR-091 cl. 12, 17; S-5, RD-10, MD-3).

Scope separation is the property under test: an ``agent`` token never
authenticates HTTP, an ``api`` token is exempt from the origin check only when it
is valid, a ``probe`` token opens ``GET /api/ops/health`` and nothing else. Every
check is re-run per call (revocation and disable act at once). Storage is the
sha256 of the token, compared with ``compare_digest`` after a prefix lookup.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update

from applire.auth import tokens
from applire.models.user import User
from tests.support.owners_1c import (  # noqa: F401 (fixture)
    build_app,
    make_person,
    token_db,
)


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _token(db, user, scope, name="t"):
    row, raw = await tokens.create_token(db, user_id=user.id, scope=scope, name=name)
    await db.commit()
    return row, raw


# ── format and storage ──────────────────────────────────────────────────────


def test_generated_token_has_the_documented_format():
    raw, prefix, digest = tokens.generate_token()
    assert raw.startswith("apl_") and raw[4:12] == prefix and raw[12] == "_"
    assert len(raw) == 4 + 8 + 1 + 43
    assert tokens.parse_token(raw) == prefix
    assert digest == tokens.hash_token(raw) and len(digest) == 64


@pytest.mark.parametrize(
    "raw",
    [None, "", "apl_", "apl_ABCD1234_" + "a" * 43, "apl_abcd1234_" + "a" * 42,
     "xpl_abcd1234_" + "a" * 43, "apl_abcd1234-" + "a" * 43, " apl_abcd1234_" + "a" * 43],
)
def test_malformed_tokens_do_not_parse(raw):
    assert tokens.parse_token(raw) is None


@pytest.mark.asyncio
async def test_only_the_hash_is_stored(token_db):
    from applire.models.auth import PersonalToken

    async with token_db.session() as db:
        me = await make_person(db)
        row, raw = await _token(db, me, "api")
        stored = (await db.execute(select(PersonalToken))).scalar_one()
        assert stored.token_hash == tokens.hash_token(raw)
        assert raw not in {str(v) for v in vars(stored).values()}


@pytest.mark.asyncio
async def test_a_prefix_collision_retries_with_a_new_prefix(token_db, monkeypatch):
    async with token_db.session() as db:
        me = await make_person(db)
        first, _ = await _token(db, me, "api")
        prefixes = iter([first.prefix, first.prefix, "zzzz9999"])
        monkeypatch.setattr(tokens, "_new_prefix", lambda: next(prefixes))
        second, raw = await _token(db, me, "api")
        assert second.prefix == "zzzz9999"
        assert await tokens.resolve_bearer(db, raw, "api") is not None


# ── resolution: scope separation, revocation, status (MD-3) ─────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["agent", "api", "probe"])
async def test_a_token_resolves_only_for_its_own_scope(token_db, scope):
    async with token_db.session() as db:
        me = await make_person(db)
        _row, raw = await _token(db, me, scope)
        for asked in ("agent", "api", "probe"):
            got = await tokens.resolve_bearer(db, raw, asked)
            assert (got is not None) is (asked == scope), (scope, asked)


@pytest.mark.asyncio
async def test_resolve_agent_token_returns_the_owner_and_raises_otherwise(token_db):
    async with token_db.session() as db:
        me = await make_person(db)
        _row, agent = await _token(db, me, "agent")
        _row, api = await _token(db, me, "api")
        assert (await tokens.resolve_agent_token(db, agent)).id == me.id
        for bad in (api, "", "garbage", agent[:-1] + ("A" if agent[-1] != "A" else "B")):
            with pytest.raises(tokens.InvalidToken):
                await tokens.resolve_agent_token(db, bad)


@pytest.mark.asyncio
async def test_a_tampered_secret_with_a_real_prefix_is_refused(token_db):
    async with token_db.session() as db:
        me = await make_person(db)
        _row, raw = await _token(db, me, "api")
        forged = raw[:13] + ("A" if raw[13] != "A" else "B") + raw[14:]
        assert tokens.parse_token(forged) is not None
        assert await tokens.resolve_bearer(db, forged, "api") is None


@pytest.mark.asyncio
async def test_revocation_acts_on_the_next_call(token_db):
    async with token_db.session() as db:
        me = await make_person(db)
        row, raw = await _token(db, me, "api")
        assert await tokens.resolve_bearer(db, raw, "api") is not None
        assert await tokens.revoke_token(db, token_id=row.id, scopes=("api",), user_id=me.id)
        await db.commit()
        assert await tokens.resolve_bearer(db, raw, "api") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["disabled_at", "deleted_at"])
async def test_an_inactive_owner_invalidates_every_scope(token_db, state):
    async with token_db.session() as db:
        me = await make_person(db)
        raws = [(s, (await _token(db, me, s))[1]) for s in ("agent", "api", "probe")]
        await db.execute(update(User).where(User.id == me.id).values({state: datetime.now(timezone.utc)}))
        await db.commit()
        for scope, raw in raws:
            assert await tokens.resolve_bearer(db, raw, scope) is None


@pytest.mark.asyncio
async def test_last_used_at_is_written_at_most_once_a_minute(token_db):
    from applire.models.auth import PersonalToken

    async with token_db.session() as db:
        me = await make_person(db)
        row, raw = await _token(db, me, "api")
        await tokens.resolve_bearer(db, raw, "api")
        first = (await db.get(PersonalToken, row.id, populate_existing=True)).last_used_at
        assert first is not None
        await tokens.resolve_bearer(db, raw, "api")
        again = (await db.get(PersonalToken, row.id, populate_existing=True)).last_used_at
        assert again == first
        old = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db.execute(update(PersonalToken).where(PersonalToken.id == row.id).values(last_used_at=old))
        await db.commit()
        await tokens.resolve_bearer(db, raw, "api")
        later = (await db.get(PersonalToken, row.id, populate_existing=True)).last_used_at
        assert tokens._aware(later) > old


# ── revocation side effects ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_revoking_an_agent_token_bumps_link_epoch_api_does_not(token_db):
    async with token_db.session() as db:
        me = await make_person(db)
        agent, _ = await _token(db, me, "agent")
        api, _ = await _token(db, me, "api")
        await tokens.revoke_token(db, token_id=api.id, scopes=("agent", "api"), user_id=me.id)
        await db.commit()
        assert (await db.get(User, me.id, populate_existing=True)).link_epoch == 0
        await tokens.revoke_token(db, token_id=agent.id, scopes=("agent", "api"), user_id=me.id)
        await db.commit()
        assert (await db.get(User, me.id, populate_existing=True)).link_epoch == 1


@pytest.mark.asyncio
async def test_revoke_refuses_foreign_wrong_scope_and_already_revoked(token_db):
    async with token_db.session() as db:
        me, other = await make_person(db), await make_person(db)
        mine, _ = await _token(db, me, "api")
        probe, _ = await _token(db, me, "probe")
        assert await tokens.revoke_token(db, token_id=mine.id, scopes=("api",), user_id=other.id) is None
        assert await tokens.revoke_token(db, token_id=probe.id, scopes=("agent", "api"), user_id=me.id) is None
        assert await tokens.revoke_token(db, token_id=uuid.uuid4(), scopes=("api",), user_id=me.id) is None
        assert await tokens.revoke_token(db, token_id=mine.id, scopes=("api",), user_id=me.id) is not None
        await db.commit()
        assert await tokens.revoke_token(db, token_id=mine.id, scopes=("api",), user_id=me.id) is None


@pytest.mark.asyncio
async def test_revoke_all_for_user_kills_agent_and_api_and_bumps_epoch(token_db):
    async with token_db.session() as db:
        me, other = await make_person(db), await make_person(db)
        _a, agent = await _token(db, me, "agent")
        _b, api = await _token(db, me, "api")
        _c, theirs = await _token(db, other, "api")
        assert await tokens.revoke_all_for_user(db, me.id) == 2
        await db.commit()
        assert await tokens.resolve_bearer(db, agent, "agent") is None
        assert await tokens.resolve_bearer(db, api, "api") is None
        assert await tokens.resolve_bearer(db, theirs, "api") is not None
        assert (await db.get(User, me.id, populate_existing=True)).link_epoch == 1
        assert await tokens.revoke_all_for_user(db, me.id) == 0


@pytest.mark.asyncio
async def test_revoke_all_kills_the_signed_links_too(token_db):
    from urllib.parse import parse_qs, urlsplit

    from applire.auth import links
    from tests.support.owners_1c import make_document

    async with token_db.session() as db:
        me = await make_person(db)
        doc = await make_document(db, "cv", me)
        url = links.sign_document_url("cv", doc.id, me, f"http://t/api/cv/{doc.id}/pdf")
        p = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert await links.verify_document_link("cv", doc.id, p["exp"], p["sig"], db, p["uid"])
        await tokens.revoke_all_for_user(db, me.id)
        await db.commit()
        assert await links.verify_document_link("cv", doc.id, p["exp"], p["sig"], db, p["uid"]) is None


# ── request helpers: one bearer rule for deps + CSRF ────────────────────────


def _probe_app(tdb):
    app = FastAPI()

    @app.api_route("/probe", methods=["GET", "POST"])
    async def _probe(request: Request):
        async with tdb.session() as db:
            return {
                "raw": tokens.bearer_from_request(request),
                "exempt": await tokens.is_csrf_exempt(request, db),
            }

    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header,expected",
    [(None, None), ("Bearer abc", "abc"), ("bearer  abc ", "abc"), ("Basic abc", ""), ("Bearer", ""), ("Bearer   ", "")],
)
async def test_bearer_from_request(token_db, header, expected):
    async with _client(_probe_app(token_db)) as c:
        r = await c.get("/probe", headers={"Authorization": header} if header is not None else {})
    assert r.json()["raw"] == expected


@pytest.mark.asyncio
async def test_csrf_exemption_needs_a_valid_api_bearer_of_an_active_user(token_db):
    """adversarial-security §3: presence of a header is not enough."""
    async with token_db.session() as db:
        me = await make_person(db)
        api_row, api = await _token(db, me, "api")
        _r, agent = await _token(db, me, "agent")
        _r, probe = await _token(db, me, "probe")
        revoked_row, revoked = await _token(db, me, "api")
        await tokens.revoke_token(db, token_id=revoked_row.id, scopes=("api",), user_id=me.id)
        await db.commit()
    cases = {
        api: True,
        agent: False,
        probe: False,
        revoked: False,
        "apl_abcd1234_" + "A" * 43: False,
        "x": False,
    }
    async with _client(_probe_app(token_db)) as c:
        assert (await c.post("/probe")).json()["exempt"] is False
        for raw, exempt in cases.items():
            r = await c.post("/probe", headers={"Authorization": f"Bearer {raw}"})
            assert r.json()["exempt"] is exempt, raw


@pytest.mark.asyncio
async def test_the_bearer_result_is_cached_per_request_and_scope(token_db, monkeypatch):
    calls = []
    real = tokens.resolve_bearer

    async def counting(db, raw, scope):
        calls.append(scope)
        return await real(db, raw, scope)

    monkeypatch.setattr(tokens, "resolve_bearer", counting)
    app = FastAPI()

    @app.get("/twice")
    async def _twice(request: Request):
        async with token_db.session() as db:
            await tokens.request_bearer_user(request, db, "api")
            await tokens.request_bearer_user(request, db, "api")
            await tokens.request_bearer_user(request, db, "probe")
        return {}

    async with _client(app) as c:
        await c.get("/twice", headers={"Authorization": "Bearer x"})
    assert calls == ["api", "probe"]


# ── /api/me/tokens (session only) ───────────────────────────────────────────


def _me_app(tdb, user):
    from applire.routers import me_tokens

    return build_app(tdb, [me_tokens.router], as_user=user)


@pytest.mark.asyncio
async def test_create_list_revoke_my_tokens(token_db):
    async with token_db.session() as db:
        me = await make_person(db)
    async with _client(_me_app(token_db, me)) as c:
        r = await c.post("/api/me/tokens", json={"name": "laptop agent", "scope": "agent"})
        assert r.status_code == 201
        created = r.json()
        assert created["token"].startswith("apl_") and created["prefix"] == created["token"][4:12]
        assert set(created) == {"id", "name", "scope", "prefix", "created_at", "last_used_at", "token"}
        listed = (await c.get("/api/me/tokens")).json()["tokens"]
        assert [t["id"] for t in listed] == [created["id"]]
        assert "token" not in listed[0]
        assert (await c.delete(f"/api/me/tokens/{created['id']}")).status_code == 204
        assert (await c.get("/api/me/tokens")).json()["tokens"] == []
        assert (await c.delete(f"/api/me/tokens/{created['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_a_person_cannot_create_a_probe_token(token_db):
    async with token_db.session() as db:
        me = await make_person(db, role="admin")
    async with _client(_me_app(token_db, me)) as c:
        r = await c.post("/api/me/tokens", json={"name": "x", "scope": "probe"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_someone_elses_token_is_404_and_stays_alive(token_db):
    async with token_db.session() as db:
        me, other = await make_person(db), await make_person(db)
        theirs, raw = await _token(db, other, "api")
    async with _client(_me_app(token_db, me)) as c:
        assert (await c.delete(f"/api/me/tokens/{theirs.id}")).status_code == 404
        assert (await c.get("/api/me/tokens")).json()["tokens"] == []
    async with token_db.session() as db:
        assert await tokens.resolve_bearer(db, raw, "api") is not None


@pytest.mark.asyncio
async def test_my_token_list_hides_probe_tokens(token_db):
    async with token_db.session() as db:
        me = await make_person(db, role="admin")
        await _token(db, me, "probe")
    async with _client(_me_app(token_db, me)) as c:
        assert (await c.get("/api/me/tokens")).json()["tokens"] == []


@pytest.mark.asyncio
async def test_anonymous_is_401_on_my_tokens(token_db):
    async with _client(_me_app(token_db, None)) as c:
        assert (await c.get("/api/me/tokens")).status_code == 401


@pytest.mark.asyncio
async def test_token_actions_are_audited_without_secrets(token_db, monkeypatch):
    import sys
    import types

    rows = []

    async def record(db, *, actor_id, action, target_type, target_id, details):
        rows.append((actor_id, action, target_type, target_id, details))

    monkeypatch.setitem(sys.modules, "applire.services.audit", types.SimpleNamespace(record=record))
    async with token_db.session() as db:
        me = await make_person(db)
    async with _client(_me_app(token_db, me)) as c:
        created = (await c.post("/api/me/tokens", json={"name": "n", "scope": "api"})).json()
        await c.delete(f"/api/me/tokens/{created['id']}")
    assert [r[1] for r in rows] == ["token.created", "token.revoked"]
    for actor, _action, ttype, target, details in rows:
        assert actor == me.id and ttype == "user" and target == me.id
        assert set(details) <= {"token_id", "scope", "count"}
        assert created["token"] not in str(details) and created["prefix"] not in str(details)


# ── /api/admin/probe-tokens + /api/ops/health ───────────────────────────────


def _ops_app(tdb, user, monkeypatch):
    import applire.providers.llm as llm
    from applire.routers import ops
    from applire.routers.admin import probe_tokens
    from applire.services.ops import aggregate, probes

    class _Stub:
        async def acomplete(self, *_a, **_k):
            return "pong"

    probes.reset_provider_cache()
    aggregate.reset_state()
    monkeypatch.setattr(probes, "_code_head", lambda: None)
    monkeypatch.setattr(llm, "get_provider", lambda: _Stub())
    return build_app(tdb, [ops.router, probe_tokens.router], as_user=user)


@pytest.mark.asyncio
async def test_admin_manages_probe_tokens_and_a_probe_opens_ops_health_only(token_db, monkeypatch):
    from applire.ownership import current_owner

    async with token_db.session() as db:
        admin = await make_person(db, role="admin")
    async with _client(_ops_app(token_db, admin, monkeypatch)) as c:
        r = await c.post("/api/admin/probe-tokens", json={"name": "uptime kuma"})
        assert r.status_code == 201
        probe = r.json()
        listed = (await c.get("/api/admin/probe-tokens")).json()["tokens"]
        assert [t["id"] for t in listed] == [probe["id"]] and "token" not in listed[0]

    # The probe works with no session at all …
    seen = {}
    import applire.routers.ops as ops_module

    real_collect = ops_module.collect

    async def spy(db):
        seen["owner"] = current_owner()
        return await real_collect(db)

    monkeypatch.setattr(ops_module, "collect", spy)
    async with _client(_ops_app(token_db, None, monkeypatch)) as c:
        ok = await c.get("/api/ops/health", headers={"Authorization": f"Bearer {probe['token']}"})
        assert ok.status_code in (200, 503)
        assert seen["owner"].user_id is None and seen["owner"].reason == "ops-aggregate"
        # … but not on the probe-token admin routes, and not as an api token.
        no = await c.get("/api/admin/probe-tokens", headers={"Authorization": f"Bearer {probe['token']}"})
        assert no.status_code == 401
    async with token_db.session() as db:
        assert await tokens.resolve_bearer(db, probe["token"], "api") is None

    # Revoked → refused on the next call.
    async with _client(_ops_app(token_db, admin, monkeypatch)) as c:
        assert (await c.delete(f"/api/admin/probe-tokens/{probe['id']}")).status_code == 204
        assert (await c.delete(f"/api/admin/probe-tokens/{probe['id']}")).status_code == 404
    async with _client(_ops_app(token_db, None, monkeypatch)) as c:
        gone = await c.get("/api/ops/health", headers={"Authorization": f"Bearer {probe['token']}"})
        assert gone.status_code == 401


@pytest.mark.asyncio
async def test_agent_and_api_tokens_of_a_non_admin_do_not_open_ops_health(token_db, monkeypatch):
    async with token_db.session() as db:
        me = await make_person(db)
        _r, agent = await _token(db, me, "agent")
        _r, probe_of_user = await _token(db, me, "probe")  # not creatable via HTTP; defence in depth
    async with _client(_ops_app(token_db, None, monkeypatch)) as c:
        for raw in (agent, probe_of_user, "garbage"):
            r = await c.get("/api/ops/health", headers={"Authorization": f"Bearer {raw}"})
            assert r.status_code == 401, raw


@pytest.mark.asyncio
async def test_an_admins_agent_token_does_not_open_ops_health(token_db, monkeypatch):
    """The probe branch resolves the probe scope only: an admin's agent token (a
    stdio-only credential) must not become an HTTP credential on the ops route."""
    async with token_db.session() as db:
        admin = await make_person(db, role="admin")
        _r, agent = await _token(db, admin, "agent")
    async with _client(_ops_app(token_db, None, monkeypatch)) as c:
        r = await c.get("/api/ops/health", headers={"Authorization": f"Bearer {agent}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_a_probe_dies_when_its_creator_stops_being_an_active_admin(token_db, monkeypatch):
    async with token_db.session() as db:
        admin = await make_person(db, role="admin")
        _r, probe = await _token(db, admin, "probe")
        await db.execute(update(User).where(User.id == admin.id).values(role="user"))
        await db.commit()
    async with _client(_ops_app(token_db, None, monkeypatch)) as c:
        r = await c.get("/api/ops/health", headers={"Authorization": f"Bearer {probe}"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_non_admin_cannot_manage_probe_tokens(token_db, monkeypatch):
    async with token_db.session() as db:
        me = await make_person(db)
    async with _client(_ops_app(token_db, me, monkeypatch)) as c:
        assert (await c.get("/api/admin/probe-tokens")).status_code == 403
        assert (await c.post("/api/admin/probe-tokens", json={"name": "x"})).status_code == 403
