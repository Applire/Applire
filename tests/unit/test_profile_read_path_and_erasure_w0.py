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

"""F6 + F9 — the profile read path and the erasure service, Strawberry W0 form.

F6: ``get_profile_for_user`` delegates to ``_get_latest`` (newest live row),
``create_profile_record`` accepts ``user_id``. F9: ``erase(db, user_id,
"vault")`` delegates to today's ``DELETE /api/profile`` handler body (no
router code moved) and returns its per-table counts; ``"account"`` is 3b's.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
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
async def test_get_profile_for_user_returns_the_newest_live_row(db):
    from applire.services.profile import _get_latest, get_profile_for_user
    from tests.support.profile_factory import make_master_profile

    now = datetime.now(timezone.utc)
    # One live vault per owner (ADR-092 cl. 2): the older live row is another
    # owner's — `_get_latest` (W0 body) still picks the newest live row overall.
    older = make_master_profile(
        profile_json={"personal_info": {"name": "Older"}}, user_id=uuid.uuid4()
    )
    older.created_at = now - timedelta(days=2)
    newer = make_master_profile(profile_json={"personal_info": {"name": "Newer"}})
    newer.created_at = now - timedelta(days=1)
    gone = make_master_profile(profile_json={"personal_info": {"name": "Deleted"}})
    gone.created_at = now
    gone.deleted_at = now
    db.add_all([older, newer, gone])
    await db.commit()

    row = await get_profile_for_user(db, USER_ID)
    assert row is not None and row.id == newer.id
    assert (await get_profile_for_user(db)).id == (await _get_latest(db)).id


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
    # a second owner: the row's owner comes from the acting user's context
    # until 3b makes `create_profile_record` set it (ADR-092 cl. 2)
    from applire.ownership import owner_context

    other = uuid.uuid4()
    with owner_context(other):
        positional = await create_profile_record(db, other)
    assert positional.id != record.id


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
    db.add(make_master_profile(profile_json={"personal_info": {"name": "Emma"}}))
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
async def test_erase_vault_delegates_to_todays_erasure_path(db, monkeypatch):
    from applire.routers import profile as profile_router
    from applire.services.erasure import erase

    storage = _RecordingStorage()
    monkeypatch.setattr(profile_router, "_get_storage", lambda: storage)
    await _seed_vault(db)

    counts = await erase(db, USER_ID, "vault")

    assert counts["uploads"] == 1
    assert counts["applications"] == 1
    assert counts["master_profiles"] == 1
    assert counts["users"] == 0  # today: the user row is kept
    for table in ("uploads", "applications", "master_profiles"):
        left = (await db.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
        assert left == 0, table
    # today's semantics: the shared posting and the user row stay
    assert (await db.execute(text("SELECT COUNT(*) FROM job_analyses"))).scalar_one() == 1
    assert (await db.execute(text("SELECT COUNT(*) FROM users"))).scalar_one() == 1
    assert storage.deleted == [UPLOAD_PATH]


@pytest.mark.asyncio
async def test_erase_failure_surfaces_as_erasure_failed(db, monkeypatch):
    from applire.routers import profile as profile_router
    from applire.services.erasure import ErasureFailed, erase

    monkeypatch.setattr(profile_router, "_get_storage", lambda: _RecordingStorage())

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
async def test_erase_account_scope_is_not_built_in_w0(db):
    from applire.services.erasure import erase

    with pytest.raises(NotImplementedError):
        await erase(db, USER_ID, "account")


@pytest.mark.asyncio
async def test_erase_unknown_scope_refused(db):
    from applire.services.erasure import erase

    with pytest.raises(ValueError):
        await erase(db, USER_ID, "everything")  # type: ignore[arg-type]
