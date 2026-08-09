# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""#480 PR 1 / ADR-063 amended clause 6 — the door-level write tests.

`commit_ops` **flushes and never commits**: the transaction stays with the
caller so `services/session.py` can write `session.state` and the vault in one
unit. §7.6 names the cost honestly — *a forgotten `db.commit()` is a silent
no-write* — and the binding mitigation is one door-level integration test per
migrated writer, landed with the migration.

That is what these tests are. They drive the real door (HTTP for testimony, the
MCP tool for agent claims) against a **file-backed** SQLite database and then
re-read the row over a SEPARATE connection. An uncommitted write is invisible
to that second connection, so deleting the `await db.commit()` from either
bridge turns these red — which no in-session assertion would.

PR 1 migrates two writers: the testimony bridge and the agent-claims bridge.
"""
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


class _Provider:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls = 0

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        return self.payload


@pytest_asyncio.fixture
async def durable_db(tmp_path):
    """A file-backed database, so "did it survive the request?" is a real
    question and not an identity-map artefact."""
    import applire.models.profile  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    from applire.db.session import Base
    from applire.models.profile import MasterProfile
    from applire.models.user import User
    from applire.models.user_settings import UserSettings

    url = f"sqlite+aiosqlite:///{tmp_path / 'vault.sqlite'}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    MasterProfile.__table__,
                    User.__table__,
                    UserSettings.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


async def _seed(factory) -> uuid.UUID:
    from applire.models.profile import MasterProfile, authorized_profile_write

    async with factory() as session:
        with authorized_profile_write():
            record = MasterProfile(
                profile_json={
                    "personal_info": {"full_name": "Daniel Kovač"},
                    "metadata": {},
                }
            )
        session.add(record)
        await session.commit()
        return record.id


async def _read_back_over_a_fresh_connection(engine, profile_id: uuid.UUID) -> dict:
    """A brand-new session on a brand-new connection: only COMMITTED state."""
    from applire.models.profile import MasterProfile

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        return dict(row.profile_json)


# ── Door 1: POST /api/profile/testimony (UI) ──────────────────────────────────


@pytest.mark.asyncio
async def test_testimony_door_write_survives_the_request(durable_db):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.profile import _get_provider, router

    engine, factory = durable_db
    profile_id = await _seed(factory)

    app = FastAPI()
    app.include_router(router)
    async with factory() as request_session:
        app.dependency_overrides[get_db] = lambda: request_session
        auth = MagicMock()
        auth.get_current_user = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
        app.dependency_overrides[get_auth_provider] = lambda: auth
        app.dependency_overrides[_get_provider] = lambda: _Provider(
            {
                "ops": [{"op": "upsert_skill", "name": "Kafka", "category": "technical"}],
                "ambiguities": [],
                "denials": [],
            }
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/profile/testimony",
                json={"text": "I ran Kafka in production for three years."},
            )
    assert resp.status_code == 200

    stored = await _read_back_over_a_fresh_connection(engine, profile_id)
    assert [s["name"] for s in stored["skills"]] == ["Kafka"]
    # The unconditional trail is durable too, not just in the response body.
    assert len(stored["metadata"]["enrichment_history"]) == 1


@pytest.mark.asyncio
async def test_testimony_door_persists_a_no_op_turns_receipt(durable_db):
    """The invariant-3 half of the same door: even a turn that changed nothing
    must leave a durable record that it happened."""
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.profile import _get_provider, router

    engine, factory = durable_db
    profile_id = await _seed(factory)

    app = FastAPI()
    app.include_router(router)
    async with factory() as request_session:
        app.dependency_overrides[get_db] = lambda: request_session
        auth = MagicMock()
        auth.get_current_user = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
        app.dependency_overrides[get_auth_provider] = lambda: auth
        app.dependency_overrides[_get_provider] = lambda: _Provider(
            {"ops": [], "ambiguities": [], "denials": []}
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/profile/testimony", json={"text": "Nothing new, really."}
            )
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_change"

    stored = await _read_back_over_a_fresh_connection(engine, profile_id)
    history = stored["metadata"]["enrichment_history"]
    assert len(history) == 1
    assert history[0]["source"] == "testimony"
    assert history[0]["changes"] == []


# ── Door 2: the agent-claims bridge (MCP submit_claims) ───────────────────────


@pytest.mark.asyncio
async def test_agent_claims_door_write_survives_the_request(durable_db):
    from applire.schemas.claims import ClaimItem, ClaimsSubmission
    from applire.services.profile.reconcile.agent_bridge import submit_agent_claims

    engine, factory = durable_db
    profile_id = await _seed(factory)

    provider = _Provider(
        {
            "ops": [{"op": "upsert_skill", "name": "Terraform", "category": "technical"}],
            "ambiguities": [],
            "denials": [],
        }
    )
    async with factory() as request_session:
        result = await submit_agent_claims(
            ClaimsSubmission(
                claims=[ClaimItem(statement="I wrote Terraform modules for two years.")]
            ),
            None,
            request_session,
            provider,
        )
    assert result.results[0].status == "applied"

    stored = await _read_back_over_a_fresh_connection(engine, profile_id)
    assert [s["name"] for s in stored["skills"]] == ["Terraform"]
    assert len(stored["metadata"]["enrichment_history"]) == 1


@pytest.mark.asyncio
async def test_agent_claims_door_persists_every_claim_in_one_transaction(durable_db):
    """The batch is still ONE transaction: `commit_ops` flushes per claim, the
    door commits once at the end, and all claims land together."""
    from applire.schemas.claims import ClaimItem, ClaimsSubmission
    from applire.services.profile.reconcile.agent_bridge import submit_agent_claims

    engine, factory = durable_db
    profile_id = await _seed(factory)

    class _Queue:
        def __init__(self, payloads):
            self.payloads = list(payloads)

        async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
            return self.payloads.pop(0)

    provider = _Queue(
        [
            {
                "ops": [{"op": "upsert_skill", "name": "Kafka", "category": "technical"}],
                "ambiguities": [],
                "denials": [],
            },
            {
                "ops": [{"op": "upsert_skill", "name": "Podman", "category": "technical"}],
                "ambiguities": [],
                "denials": [],
            },
        ]
    )
    async with factory() as request_session:
        await submit_agent_claims(
            ClaimsSubmission(
                claims=[
                    ClaimItem(statement="Kafka in production for three years."),
                    ClaimItem(statement="Podman for local container builds."),
                ]
            ),
            None,
            request_session,
            provider,
        )

    stored = await _read_back_over_a_fresh_connection(engine, profile_id)
    assert sorted(s["name"] for s in stored["skills"]) == ["Kafka", "Podman"]
    assert len(stored["metadata"]["enrichment_history"]) == 2
