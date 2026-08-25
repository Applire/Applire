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

"""ADR-077 clause 1 — the one-time entry-id backfill, through the committer.

The five previously id-less vault types get their minted ids WRITTEN BACK
(SF-PIN.8: an unpersisted default_factory id regenerates on every parse and is
not an identity). The backfill lives in services/profile/commit.py — the
single authorized vault write path — is idempotent, and never touches a
profile whose entries already carry ids.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.profile import (
    Certification,
    EducationEntry,
    Language,
    MasterProfileData,
    Publication,
    Skill,
    WorkEntry,
)
from applire.services.profile.commit import backfill_entry_ids

_BACKFILL_SECTIONS = (
    "skills",
    "certifications",
    "education",
    "languages",
    "publications",
)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user        # noqa: F401
    import applire.models.job         # noqa: F401
    import applire.models.profile     # noqa: F401
    import applire.models.gap         # noqa: F401
    import applire.models.cv          # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session     # noqa: F401
    import applire.models.flow        # noqa: F401
    import applire.models.uploads     # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


def _legacy_blob() -> dict:
    """A profile as persisted BEFORE the schema change: no ids on the five."""
    profile = MasterProfileData(
        work_experience=[WorkEntry(role="Dev", company="Acme")],
        skills=[Skill(name="Python"), Skill(name="Kubernetes")],
        certifications=[Certification(name="CKA")],
        education=[EducationEntry(institution="TU München", degree="M.Sc.")],
        languages=[Language(language="Deutsch")],
        publications=[Publication(title="A paper")],
    )
    blob = profile.model_dump(mode="json")
    for section in _BACKFILL_SECTIONS:
        for entry in blob[section]:
            entry.pop("id", None)
    return blob


async def _persist_raw(db, blob: dict):
    from applire.models.profile import MasterProfile, authorized_profile_write

    with authorized_profile_write():
        record = MasterProfile(profile_json=blob)
        db.add(record)
    await db.flush()
    return record


@pytest.mark.asyncio
async def test_backfill_writes_ids_into_every_id_less_entry(db):
    record = await _persist_raw(db, _legacy_blob())
    changed = await backfill_entry_ids(db)
    assert changed == 1  # one profile rewritten
    await db.refresh(record)
    for section in _BACKFILL_SECTIONS:
        for entry in record.profile_json[section]:
            assert entry.get("id"), section
            uuid.UUID(entry["id"])


@pytest.mark.asyncio
async def test_backfill_is_idempotent_and_keeps_ids_stable(db):
    record = await _persist_raw(db, _legacy_blob())
    await backfill_entry_ids(db)
    await db.refresh(record)
    first_ids = {
        section: [e["id"] for e in record.profile_json[section]]
        for section in _BACKFILL_SECTIONS
    }
    changed = await backfill_entry_ids(db)
    assert changed == 0  # nothing left to do
    await db.refresh(record)
    for section in _BACKFILL_SECTIONS:
        assert [e["id"] for e in record.profile_json[section]] == first_ids[section]


@pytest.mark.asyncio
async def test_backfill_preserves_existing_ids_verbatim(db):
    blob = _legacy_blob()
    keep = str(uuid.uuid4())
    blob["skills"][0]["id"] = keep  # one entry already migrated
    record = await _persist_raw(db, blob)
    await backfill_entry_ids(db)
    await db.refresh(record)
    assert record.profile_json["skills"][0]["id"] == keep
    assert record.profile_json["skills"][1]["id"] != keep


@pytest.mark.asyncio
async def test_backfill_skips_a_fully_migrated_profile(db):
    profile = MasterProfileData(skills=[Skill(name="Python")])
    record = await _persist_raw(db, profile.model_dump(mode="json"))
    before = record.profile_json
    changed = await backfill_entry_ids(db)
    assert changed == 0
    await db.refresh(record)
    assert record.profile_json == before
