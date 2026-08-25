# Copyright (C) 2026 Tobias Rosenbaum
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""ADR-063 amended 2026-08-25 (E055, JF-F-H1.6) — the committer REFUSES a
stale section edit when the edit carries the basis it was composed against.

`ReplaceSection.basis_updated_at` is the profile's `updated_at` as the GET
returned it. `None` keeps last-write-wins for every existing caller; a value
that no longer matches `record.updated_at` is refused BEFORE any op is
applied — no receipt, no sweep, no change.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, ProfileSnapshot
from applire.services.profile.commit import (
    CommitProvenance,
    StaleEditError,
    commit_ops,
)
from applire.services.profile.field_edit import build_replace_section_op

_SEED = {
    "personal_info": {"full_name": "Daniel Kovač", "email": "daniel@example.invalid"},
    "languages": [{"language": "German", "level": "C2"}],
    "metadata": {
        "completeness_score": 0.0,
        "created_via": "cv_upload",
        "created_at": "2020-01-01T00:00:00Z",
        "last_updated": "2020-01-01T00:00:00Z",
    },
}


def _provenance() -> CommitProvenance:
    return CommitProvenance(
        source="manual_edit", intake="field_edit", session_id=str(uuid.uuid4()), actor="candidate"
    )


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db_session):
    from applire.models.profile import authorized_profile_write

    with authorized_profile_write():
        record = MasterProfile(profile_json=dict(_SEED))
    db_session.add(record)
    await db_session.commit()
    return record


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_matching_basis_commits(db_session, seeded):
    op = build_replace_section_op(
        "languages",
        [{"language": "French", "level": "B1"}],
        basis_updated_at=_as_utc(seeded.updated_at),
    )
    result = await commit_ops(db_session, [op], _provenance(), record=seeded)
    assert [lang["language"] for lang in result.record.profile_json["languages"]] == ["French"]


@pytest.mark.asyncio
async def test_a_stale_basis_is_refused_before_anything_is_applied(db_session, seeded):
    before = dict(seeded.profile_json)
    stale = _as_utc(seeded.updated_at) - timedelta(seconds=1)
    op = build_replace_section_op(
        "languages", [{"language": "French", "level": "B1"}], basis_updated_at=stale
    )

    with pytest.raises(StaleEditError) as excinfo:
        await commit_ops(db_session, [op], _provenance(), record=seeded)

    # The error carries the instant the caller must re-read against.
    assert _as_utc(excinfo.value.current_updated_at) == _as_utc(seeded.updated_at)
    # Nothing moved: no section change, no receipt (refused BEFORE apply).
    assert seeded.profile_json == before
    assert not seeded.profile_json["metadata"].get("enrichment_history")


@pytest.mark.asyncio
async def test_no_basis_keeps_last_write_wins(db_session, seeded):
    """Every existing caller passes no basis and must be unaffected."""
    op = build_replace_section_op("languages", [{"language": "French", "level": "B1"}])
    assert op.basis_updated_at is None
    result = await commit_ops(db_session, [op], _provenance(), record=seeded)
    assert [lang["language"] for lang in result.record.profile_json["languages"]] == ["French"]


@pytest.mark.asyncio
async def test_a_naive_basis_matches_the_same_instant(db_session, seeded):
    """SQLite hands back naive datetimes for a timezone=True column while a
    JSON basis is tz-aware. Same instant must match either way — otherwise
    every UI save after the first would 409. (The aware form is
    test_a_matching_basis_commits.)"""
    naive = _as_utc(seeded.updated_at).replace(tzinfo=None)
    op = build_replace_section_op(
        "languages", [{"language": "French", "level": "B1"}], basis_updated_at=naive
    )
    result = await commit_ops(db_session, [op], _provenance(), record=seeded)
    assert [lang["language"] for lang in result.record.profile_json["languages"]] == ["French"]
