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

"""Issue #152 (dFMEA SF-PROFILE.5) — GDPR erasure vs. failing file deletes.

DELETE /api/profile deletes DB rows in one transaction, commits, THEN deletes
the physical files best-effort. When storage.delete raises:
  * erasure must still complete (202, rows gone) — Art. 17 beats file I/O
  * the failure must be logged at ERROR (PII outlives the erasure request
    until the retention worker's orphan scan reclaims the file)

Unit-level equivalent of a tests/test_gdpr.py scenario — that file needs the
Docker stack; this one runs the same route on in-memory SQLite with a mocked
storage provider.
"""
import logging
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PHOTO_PATH = "/app/data/uploads/deadbeef.jpg"
UPLOAD_PATH = "/app/data/uploads/cafebabe.pdf"

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
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db_session):
    """User + upload row + master profile with a photo_url."""
    from applire.models.profile import MasterProfile
    from applire.models.uploads import UploadRecord
    from applire.models.user import User

    db_session.add(User(id=USER_ID, email="emma@example.com"))
    db_session.add(
        UploadRecord(
            user_id=USER_ID,
            original_filename="cv.pdf",
            content_hash="x" * 64,
            mime_type="application/pdf",
            file_path=UPLOAD_PATH,
            byte_size=1234,
        )
    )
    db_session.add(
        MasterProfile(
            profile_json={
                "personal_info": {"name": "Emma Fischer", "photo_url": PHOTO_PATH}
            }
        )
    )
    await db_session.commit()


class _ExplodingStorage:
    """StorageProvider stand-in whose delete always fails (volume offline)."""

    def __init__(self) -> None:
        self.attempted: list[str] = []

    async def save(self, file_bytes: bytes, filename: str) -> str:
        raise NotImplementedError

    async def delete(self, file_path: str) -> None:
        self.attempted.append(file_path)
        raise OSError("uploads volume not reachable")

    async def read(self, file_path: str) -> bytes:
        raise NotImplementedError


@pytest_asyncio.fixture
async def client_and_storage(db_session, seeded):
    from unittest.mock import AsyncMock, MagicMock

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.profile import _get_storage, router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session

    storage = _ExplodingStorage()
    app.dependency_overrides[_get_storage] = lambda: storage

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, storage


@pytest.mark.asyncio
async def test_erasure_completes_despite_file_delete_failure(client_and_storage, db_session):
    """Rows must be gone and 202 returned even when every file delete raises."""
    client, storage = client_and_storage

    resp = await client.delete("/api/profile")

    assert resp.status_code == 202
    # Both the upload file and the profile photo were attempted
    assert UPLOAD_PATH in storage.attempted
    assert PHOTO_PATH in storage.attempted
    # DB rows are gone regardless of the storage failure
    uploads_left = (await db_session.execute(text("SELECT COUNT(*) FROM uploads"))).scalar_one()
    profiles_left = (
        await db_session.execute(text("SELECT COUNT(*) FROM master_profiles"))
    ).scalar_one()
    assert uploads_left == 0
    assert profiles_left == 0


@pytest.mark.asyncio
async def test_file_delete_failure_logged_at_error(client_and_storage, caplog):
    """A failed post-erasure file delete is PII outliving an Art. 17 request —
    it must surface at ERROR, and must not claim a reaper that doesn't exist."""
    client, _storage = client_and_storage

    with caplog.at_level(logging.INFO, logger="applire.routers.profile"):
        resp = await client.delete("/api/profile")

    assert resp.status_code == 202
    failures = [
        rec
        for rec in caplog.records
        if "Failed to delete" in rec.getMessage()
    ]
    assert len(failures) == 2, "expected one failure log per file (upload + photo)"
    assert all(rec.levelname == "ERROR" for rec in failures), (
        "file-delete failures after GDPR erasure must be logged at ERROR"
    )
    assert all("will be reaped" not in rec.getMessage() for rec in failures), (
        "log must not claim a reaper; the orphan scan is the real mechanism"
    )
    assert all("orphan scan" in rec.getMessage() for rec in failures), (
        "log should state the actual recovery mechanism (retention orphan scan)"
    )
