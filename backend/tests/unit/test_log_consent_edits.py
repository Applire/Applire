# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Editing works without the content-log consent (ruling C-1); model work does not.

At LOG_LEVEL=DEBUG a person without a current consent can edit the profile
(REST and MCP), commit an entry merge and discard a held import; the model step
an edit layers on (the skill estimate) is left out. The re-check of a
document, an entry-merge preview and the merge of a held import are model work
and are refused with ``log_consent_required`` before anything is touched.
"""

from __future__ import annotations

import contextlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI

from applire.auth import get_auth_provider
from applire.db.session import get_db
from applire.models.user import User
from applire.services import log_consent
from tests.support.owners_1b import add_user, client_for
from tests.support.profile_factory import make_master_profile
from tests.unit.test_log_consent_doors import H, Acting, _clean, debug  # noqa: F401 — fixtures


def _app(db) -> tuple[FastAPI, Acting]:
    from applire.routers import me_log_consent, profile, review

    app = FastAPI()
    app.include_router(me_log_consent.router)
    app.include_router(profile.router)
    app.include_router(review.router)
    acting = Acting()

    async def _db():
        yield db

    async def _provider():
        return acting

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_auth_provider] = _provider
    return app, acting


@pytest_asyncio.fixture
async def env(async_db):
    app, acting = _app(async_db)
    person = await add_user(async_db, email="tester-11@example.org")
    async_db.add(make_master_profile(user_id=person.id, profile_json={"skills": [], "work_experience": []}))
    await async_db.commit()
    acting.user_id = person.id
    async with client_for(app) as client:
        yield async_db, client, person


async def _consent(client):
    r = await client.post("/api/me/log-consent", json={"version": log_consent.current_version()}, headers=H)
    assert r.status_code == 200


SKILLS = [{"name": "Python", "category": "technical"}]


@pytest.mark.asyncio
async def test_a_profile_edit_works_without_consent_and_leaves_the_estimate_out(env, debug):
    _, client, _ = env
    factory = MagicMock(name="factory")
    with patch("applire.routers.profile.get_provider", factory):
        r = await client.patch("/api/profile/skills", json=SKILLS, headers=H)
        assert r.status_code == 200, r.text
        assert [s["name"] for s in r.json()["profile"]["skills"]] == ["Python"]
        factory.assert_not_called()

        # Withdrawn is the same as never given.
        await _consent(client)
        await client.delete("/api/me/log-consent", headers=H)
        assert (await client.patch("/api/profile/skills", json=SKILLS, headers=H)).status_code == 200
        factory.assert_not_called()


@pytest.mark.asyncio
async def test_with_consent_the_edit_gets_its_estimate_again(env, debug):
    _, client, _ = env
    await _consent(client)
    factory = MagicMock(name="factory")
    with patch("applire.routers.profile.get_provider", factory), patch(
        "applire.services.profile.commit.enrich_skills", AsyncMock(side_effect=lambda p, _llm: p)
    ):
        assert (await client.patch("/api/profile/skills", json=SKILLS, headers=H)).status_code == 200
    factory.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cv", "cover-letter"])
async def test_a_recheck_without_consent_is_refused_before_anything_runs(env, debug, kind):
    _, client, _ = env
    with patch("applire.routers.review.ra.recheck", AsyncMock()) as recheck:
        r = await client.post(f"/api/{kind}/{uuid.uuid4()}/review/recheck", headers=H)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "log_consent_required"
    recheck.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_entry_merge_preview_is_model_work_and_its_commit_is_not(env, debug):
    _, client, _ = env
    body = {"section": "work_experience", "target_id": "a", "source_id": "b"}
    r = await client.post("/api/profile/entries/merge", json={**body, "mode": "preview"}, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "log_consent_required"
    r = await client.post("/api/profile/entries/merge", json={**body, "mode": "commit"}, headers=H)
    assert r.status_code == 404  # past the door: the entries do not exist


@pytest.mark.asyncio
async def test_discarding_a_held_import_needs_no_consent_and_merging_it_does(env, debug):
    _, client, _ = env
    staged = uuid.uuid4()
    r = await client.post(f"/api/profile/staged/{staged}/resolve", json={"action": "merge"}, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "log_consent_required"
    r = await client.post(f"/api/profile/staged/{staged}/resolve", json={"action": "discard"}, headers=H)
    assert r.status_code == 404  # past the door: no such held import


# ── MCP ──────────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _mcp_as(person):
    from applire.mcp import server

    @contextlib.asynccontextmanager
    async def _db():
        yield MagicMock()

    with patch.object(server.mcp_identity, "bound", return_value=None), patch.object(
        server.mcp_identity, "revalidate", AsyncMock(return_value=person)
    ), patch.object(server, "get_db", _db), patch(
        "applire.services.instance_settings.refresh", AsyncMock(return_value={})
    ), patch.object(server, "_current_user_id", AsyncMock(return_value=person.id)), patch.object(
        server, "get_provider", MagicMock(name="factory")
    ) as factory:
        yield server, factory


@pytest.mark.asyncio
async def test_mcp_update_profile_works_without_consent(debug):
    person = User(email="tester-12@example.org")
    person.id = uuid.uuid4()  # never consented
    result = MagicMock()
    result.model_dump.return_value = {"profile_json": {}}
    with _mcp_as(person) as (server, factory), patch.object(
        server.profile_svc, "patch_profile_section", AsyncMock(return_value=result)
    ) as write:
        await server.mcp.call_tool("update_profile", {"section": "skills", "data": SKILLS})
    write.assert_awaited_once()
    assert write.await_args.kwargs["provider"] is None
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_mcp_held_import_discard_works_and_merge_is_refused_without_consent(debug):
    from mcp.shared.exceptions import McpError

    person = User(email="tester-13@example.org")
    person.id = uuid.uuid4()
    staged = str(uuid.uuid4())
    result = MagicMock()
    result.model_dump.return_value = {"action": "discard"}
    with _mcp_as(person) as (server, factory), patch.object(
        server, "_owned", AsyncMock()
    ), patch.object(server.profile_svc, "resolve_staged_extraction", AsyncMock(return_value=result)) as resolve:
        with pytest.raises(McpError) as exc:
            await server.resolve_held_merge(staged_id=staged, decision="merge")
        assert exc.value.error.message.startswith("log_consent_required: ")
        resolve.assert_not_awaited()
        await server.resolve_held_merge(staged_id=staged, decision="discard")
        resolve.assert_awaited_once()
    factory.assert_not_called()
