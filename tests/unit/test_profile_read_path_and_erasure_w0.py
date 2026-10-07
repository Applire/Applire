# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""F6 + F9 — the profile read path and the erasure service (W0 contract, W2 bodies).

F6: ``get_profile_for_user(db, user_id)`` is the OWNER's live row (3b, W2 —
was "the newest live row of anyone" in W0); ``create_profile_record`` sets the
owner. F9: ``erase(db, user_id, scope)`` is the one erasure implementation
(``services/erasure.py``) — per-table counts; ``"account"`` built in W2.
Deeper coverage: ``test_erasure_per_user.py``, ``test_vault_owner_scoping.py``.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from applire.ownership import owner_context
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

USER_ID = uuid.uuid4()
UPLOAD_PATH = "/app/data/uploads/w0a2-upload.pdf"


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.main  # noqa: F401 — registers every model on Base.metadata

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# --- F6 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_profile_for_user_returns_the_owners_live_row(db):
    from applire.services.profile import _get_latest, get_profile_for_user
    from tests.support.profile_factory import make_master_profile

    now = datetime.now(timezone.utc)
    mine = make_master_profile(profile_json={"personal_info": {"name": "Mine"}}, user_id=USER_ID)
    mine.created_at = now - timedelta(days=2)
    # Another owner's NEWER live row — the W0 body returned it to everyone.
    theirs = make_master_profile(profile_json={"personal_info": {"name": "Theirs"}}, user_id=uuid.uuid4())
    theirs.created_at = now - timedelta(days=1)
    gone = make_master_profile(profile_json={"personal_info": {"name": "Deleted"}}, user_id=USER_ID)
    gone.created_at = now
    gone.deleted_at = now
    db.add_all([mine, theirs, gone])
    await db.commit()

    # Acts for the user it names, as its request would (ADR-092 cl. 8).
    with owner_context(USER_ID):
        row = await get_profile_for_user(db, USER_ID)
        assert row is not None and row.id == mine.id
        assert (await _get_latest(db, USER_ID)).id == mine.id


@pytest.mark.asyncio
async def test_get_profile_for_user_none_when_no_live_row(db):
    from applire.services.profile import get_profile_for_user

    assert await get_profile_for_user(db, USER_ID) is None


@pytest.mark.asyncio
async def test_create_profile_record_accepts_user_id(db):
    from applire.services.profile.commit import create_profile_record

    record = await create_profile_record(db, user_id=USER_ID)
    await db.commit()
    assert record.id is not None
    assert record.profile_json == {}
    assert record.user_id == USER_ID  # the constructor sets the owner (3b, W2)
    other = uuid.uuid4()
    positional = await create_profile_record(db, other)
    assert positional.id != record.id and positional.user_id == other


# --- F9 -------------------------------------------------------------------


class _RecordingStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def save(self, file_bytes: bytes, filename: str) -> str:  # pragma: no cover
        raise NotImplementedError

    async def delete(self, file_path: str) -> None:
        self.deleted.append(file_path)

    async def read(self, file_path: str) -> bytes:  # pragma: no cover
        raise NotImplementedError


async def _seed_vault(db):
    from applire.models.application import Application
    from applire.models.job import JobAnalysis
    from applire.models.uploads import UploadRecord
    from applire.models.user import User
    from tests.support.profile_factory import make_master_profile

    db.add(User(id=USER_ID, email="w0a2@example.com"))
    db.add(
        UploadRecord(
            user_id=USER_ID,
            original_filename="cv.pdf",
            content_hash="y" * 64,
            mime_type="application/pdf",
            file_path=UPLOAD_PATH,
            byte_size=10,
        )
    )
    db.add(make_master_profile(profile_json={"personal_info": {"name": "Emma"}}, user_id=USER_ID))
    job = JobAnalysis(
        raw_text_hash="w0a2-hash",
        raw_text="x",
        role_title="Engineer",
        seniority_level="mid",
        language_requirement="English",
    )
    db.add(job)
    await db.flush()
    db.add(Application(user_id=USER_ID, job_analysis_id=job.id))
    await db.commit()


@pytest.mark.asyncio
async def test_erase_vault_deletes_the_owners_vault(db, monkeypatch):
    from applire.services.erasure import erase

    storage = _RecordingStorage()
    monkeypatch.setattr("applire.storage.get_storage", lambda: storage)
    await _seed_vault(db)

    counts = await erase(db, USER_ID, "vault")

    assert counts["uploads"] == 1
    assert counts["applications"] == 1
    assert counts["master_profiles"] == 1
    assert counts["users"] == 0  # the vault scope keeps the user row
    for table in ("uploads", "applications", "master_profiles"):
        left = (await db.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
        assert left == 0, table
    # ADR-092 cl. 11: a posting nobody else references goes with the vault
    assert counts["job_analyses"] == 1
    assert (await db.execute(text("SELECT COUNT(*) FROM job_analyses"))).scalar_one() == 0
    assert (await db.execute(text("SELECT COUNT(*) FROM users"))).scalar_one() == 1
    assert storage.deleted == [UPLOAD_PATH]


@pytest.mark.asyncio
async def test_erase_failure_surfaces_as_erasure_failed(db, monkeypatch):
    from applire.services.erasure import ErasureFailed, erase

    monkeypatch.setattr("applire.storage.get_storage", lambda: _RecordingStorage())

    await _seed_vault(db)
    real_execute = db.execute

    async def _fail_on_delete(statement, *args, **kwargs):
        from sqlalchemy.sql.dml import Delete

        if isinstance(statement, Delete):
            raise RuntimeError("db gone mid-cascade")
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", _fail_on_delete)
    with pytest.raises(ErasureFailed):
        await erase(db, USER_ID, "vault")


@pytest.mark.asyncio
async def test_erase_account_scope_deletes_the_settings_row_and_keeps_the_user_row(db, monkeypatch):
    """W2 (3b): the account scope is built; the user row's tombstone is the
    account door's step 3 (1b), not the erasure's."""
    from applire.models.user_settings import UserSettings
    from applire.services.erasure import erase

    monkeypatch.setattr("applire.storage.get_storage", lambda: _RecordingStorage())
    await _seed_vault(db)
    db.add(UserSettings(user_id=USER_ID))
    await db.commit()
    counts = await erase(db, USER_ID, "account")
    assert counts["user_settings"] == 1 and counts["master_profiles"] == 1
    assert (await db.execute(text("SELECT COUNT(*) FROM users WHERE deleted_at IS NULL"))).scalar_one() == 1


@pytest.mark.asyncio
async def test_erase_unknown_scope_refused(db):
    from applire.services.erasure import erase

    with pytest.raises(ValueError):
        await erase(db, USER_ID, "everything")  # type: ignore[arg-type]
