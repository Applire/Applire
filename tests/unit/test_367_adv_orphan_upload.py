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

"""#367 (adversarial) — an ownerless HELD import becomes permanently invisible
and unresolvable the moment any User row exists.

`import_cv`'s own docstring (`mcp/server.py::_import_user_id`) treats an empty
`users` table as an expected, non-error state: "an empty `users` table is an
ownerless import rather than a failure." The README documents `python -m
applire.mcp` (stdio, no FastAPI lifespan) as a standalone launch — a
self-hoster can run the agent channel WITHOUT ever starting `applire.main:app`
(whose `lifespan` is the only place that seeds the single stub user). So an
agent-door HOLD raised before any `User` row exists is a real, reachable state
on a documented deployment path — not merely theoretical.

Once a `User` row DOES appear (the FastAPI app starts later; a second
self-hosted surface is brought up; a fresh `User` row is created by any
means), three doors that scope `UploadRecord` by exact `user_id == :uid`
equality silently exclude the ownerless (`user_id IS NULL`) row:

  * `list_open_gates`             — the Health hub / agent `held_merges` never
                                     lists it, so the human is never asked to
                                     adjudicate it (ADR-041: "the system
                                     detects difference, the user decides").
  * `resolve_staged_extraction`   — `StagedResolveRequest`/`resolve_held_merge`
                                     answer 404/`StagedExtractionNotFound` for
                                     a `staged_id` that demonstrably exists.
  * `DELETE /api/profile` (GDPR erasure) — the user-scoped DELETE leaves the
                                     row (and its `staged_extraction` JSONB,
                                     containing the parked CV's full personal
                                     data) behind.

The fix mirrors an existing precedent in the SAME package for the sibling
async-import door: `import_jobs.py::list_import_jobs` already scopes with
``or_(CVImportJob.user_id == user_id, CVImportJob.user_id.is_(None))`` for
exactly this reason (Community is single-user — ADR-022 rejected — so an
ownerless row is unambiguously "the" user's row). `list_open_gates` and
`resolve_staged_extraction` widen to the same shape; the GDPR erasure sweep
in `routers/profile.py` does too.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER_ID = uuid.uuid4()


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.application import Application
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.session import InterviewSession
    from applire.models.uploads import UploadRecord
    from applire.models.user import User
    from applire.models.user_settings import UserSettings

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    tables = [
        User.__table__,
        UploadRecord.__table__,
        MasterProfile.__table__,
        GeneratedCV.__table__,
        InterviewSession.__table__,
        GapAnalysis.__table__,
        GeneratedCoverLetter.__table__,
        Application.__table__,
        FlowSession.__table__,
        UserSettings.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def orphan_hold(db_session):
    """An ownerless (``user_id=None``) GATED upload — exactly what `import_cv`
    persists when it runs before any `User` row exists (#367's own
    `_import_user_id` fallback)."""
    from applire.models.uploads import UploadRecord

    rec = UploadRecord(
        user_id=None,
        original_filename="cv.pdf",
        content_hash="a" * 64,
        mime_type="application/pdf",
        file_path="/app/data/uploads/orphan.pdf",
        byte_size=999,
        gate_status="name_divergence",
        staged_extraction={"personal_info": {"name": "Anna Bauer"}},
    )
    db_session.add(rec)
    await db_session.commit()
    await db_session.refresh(rec)
    return rec


@pytest_asyncio.fixture
async def real_user(db_session):
    """The User row that appears LATER — e.g. the FastAPI app's `lifespan`
    finally runs, seeding the single Community stub user."""
    from applire.models.user import User

    user = User(id=USER_ID, email="local@applire.community")
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.mark.asyncio
async def test_list_open_gates_finds_the_ownerless_hold_once_a_user_exists(
    db_session, orphan_hold, real_user
):
    """The Health hub (`held_merges`) must still surface the parked CV — the
    human can only adjudicate a hold they can see."""
    from applire.services.profile import list_open_gates

    held = await list_open_gates(db_session, user_id=USER_ID)
    assert any(r.id == orphan_hold.id for r in held), (
        "an ownerless HOLD vanished from list_open_gates the moment a User "
        "row existed — the human is never asked about a CV the gate parked"
    )


@pytest.mark.asyncio
async def test_resolve_staged_extraction_resolves_the_ownerless_hold(
    db_session, orphan_hold, real_user
):
    """`resolve_held_merge` / `POST /staged/{id}/resolve` must be able to
    discard (or merge) a hold that was parked before any user existed."""
    from applire.services.profile import resolve_staged_extraction

    result = await resolve_staged_extraction(
        db_session, orphan_hold.id, action="discard", user_id=USER_ID
    )
    assert result.staged_id == orphan_hold.id
    assert result.action == "discard"


@pytest.mark.asyncio
async def test_erasure_sweeps_the_ownerless_upload(db_session, orphan_hold, real_user):
    """DELETE /api/profile (Art. 17) must not leave an ownerless staged CV —
    with its full personal_info payload — behind."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from unittest.mock import AsyncMock, MagicMock

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.profile import _get_storage, router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session

    storage = MagicMock()
    storage.delete = AsyncMock(return_value=None)
    app.dependency_overrides[_get_storage] = lambda: storage

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.delete("/api/profile")

    assert resp.status_code == 202
    remaining = (
        await db_session.execute(text("SELECT COUNT(*) FROM uploads"))
    ).scalar_one()
    assert remaining == 0, (
        "an ownerless staged CV (full personal_info in staged_extraction) "
        "survived an Art. 17 erasure request"
    )
