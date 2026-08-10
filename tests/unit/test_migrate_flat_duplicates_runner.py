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

"""US186 — DB-level tests for the migration runner (snapshot / persist / idempotency).

Exercises ``run_migration`` against an in-memory SQLite DB with an injected stub
provider (hermetic — never ``get_provider()``). Asserts the ADR-042 reversibility
contract (a snapshot precedes every persisted reshape) and idempotency (a clean
profile yields no snapshot and no write).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from applire.models.profile import MasterProfile, ProfileSnapshot  # noqa: E402
from applire.schemas.profile import MasterProfileData, WorkEntry  # noqa: E402

from tests.support.profile_factory import make_master_profile  # noqa: E402

# Import the script module from scripts/ (not a package).
_scripts = Path(__file__).parent.parent.parent / "scripts"
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))
import migrate_flat_duplicates as mig  # noqa: E402


class _StubProvider:
    """Provider stub returning a canned reconcile payload (no real LLM)."""

    def __init__(self, payload: Any) -> None:
        self.payload = payload

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        return self.payload


@pytest_asyncio.fixture
async def sqlite_session():
    from applire.db.session import Base
    import importlib
    import pkgutil

    import applire.models as _models_pkg

    for _m in pkgutil.iter_modules(_models_pkg.__path__):
        importlib.import_module(f"applire.models.{_m.name}")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(session, profile: MasterProfileData) -> MasterProfile:
    record = make_master_profile(profile_json=profile.model_dump(mode="json"))
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def _snapshot_count(session, profile_id) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(ProfileSnapshot)
            .where(ProfileSnapshot.profile_id == profile_id)
        )
    ).scalar_one()


def _fold_payload(keeper_id: str) -> dict:
    return {
        "ops": [
            {
                "op": "upsert_work",
                "ref": "w1",
                "target": keeper_id,
                "company": "applire",
                "role": "Owner",
            }
        ],
        "ambiguities": [],
    }


# ── persist + ADR-042 snapshot ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reshape_persists_and_snapshots(sqlite_session):
    keeper = WorkEntry(company="applire", role="Founder & Lead Developer")
    dup = WorkEntry(company="applire", role="Owner")
    profile = MasterProfileData(work_experience=[keeper, dup])
    record = await _seed(sqlite_session, profile)

    provider = _StubProvider(_fold_payload(keeper.id))
    reports = await mig.run_migration(sqlite_session, provider)

    assert len(reports) == 1
    assert reports[0].changed is True
    assert reports[0].persisted is True
    # ADR-042: a snapshot precedes the persisted reshape.
    assert await _snapshot_count(sqlite_session, record.id) == 1

    # The persisted profile now carries the folded alias.
    await sqlite_session.refresh(record)
    reloaded = MasterProfileData.model_validate(record.profile_json)
    keeper_out = next(w for w in reloaded.work_experience if w.id == keeper.id)
    assert "Owner" in keeper_out.role_aliases


@pytest.mark.asyncio
async def test_idempotent_clean_profile_no_snapshot_no_write(sqlite_session):
    profile = MasterProfileData(
        work_experience=[WorkEntry(company="applire", role="Founder")]
    )
    record = await _seed(sqlite_session, profile)
    before = record.profile_json

    provider = _StubProvider({"ops": [], "ambiguities": []})
    reports = await mig.run_migration(sqlite_session, provider)

    assert reports[0].changed is False
    assert reports[0].persisted is False
    assert await _snapshot_count(sqlite_session, record.id) == 0
    await sqlite_session.refresh(record)
    assert record.profile_json == before


@pytest.mark.asyncio
async def test_dry_run_persists_nothing(sqlite_session):
    keeper = WorkEntry(company="applire", role="Founder & Lead Developer")
    dup = WorkEntry(company="applire", role="Owner")
    profile = MasterProfileData(work_experience=[keeper, dup])
    record = await _seed(sqlite_session, profile)
    before = record.profile_json

    provider = _StubProvider(_fold_payload(keeper.id))
    reports = await mig.run_migration(sqlite_session, provider, dry_run=True)

    assert reports[0].changed is True  # the reshape WOULD change it
    assert reports[0].persisted is False
    assert await _snapshot_count(sqlite_session, record.id) == 0
    await sqlite_session.refresh(record)
    assert record.profile_json == before  # untouched


@pytest.mark.asyncio
async def test_profile_id_targets_one_profile(sqlite_session):
    keeper = WorkEntry(company="applire", role="Founder & Lead Developer")
    a = await _seed(
        sqlite_session,
        MasterProfileData(
            work_experience=[keeper, WorkEntry(company="applire", role="Owner")]
        ),
    )
    b = await _seed(
        sqlite_session,
        MasterProfileData(work_experience=[WorkEntry(company="Other", role="X")]),
    )

    provider = _StubProvider(_fold_payload(keeper.id))
    reports = await mig.run_migration(sqlite_session, provider, profile_id=a.id)

    assert len(reports) == 1
    assert reports[0].profile_id == a.id
    # The non-targeted profile got no snapshot.
    assert await _snapshot_count(sqlite_session, b.id) == 0


@pytest.mark.asyncio
async def test_soft_deleted_profiles_are_skipped(sqlite_session):
    from datetime import datetime, timezone

    keeper = WorkEntry(company="applire", role="Founder & Lead Developer")
    record = await _seed(
        sqlite_session,
        MasterProfileData(
            work_experience=[keeper, WorkEntry(company="applire", role="Owner")]
        ),
    )
    record.deleted_at = datetime.now(timezone.utc)
    await sqlite_session.commit()

    provider = _StubProvider(_fold_payload(keeper.id))
    reports = await mig.run_migration(sqlite_session, provider)

    assert reports == []
