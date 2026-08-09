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

"""ADR-063 clause 6 (amended 2026-08-09) — the attribute-level write guard,
WARN MODE (#480 PR 1).

`master_profiles.profile_json` may only be assigned by `commit_ops` (which holds
a contextvar token) or by two named module exceptions: `services/photo.py`
(`Binary` under GDPR Art. 9(2)(a)) and `services/profile/snapshots.py` (the undo
restore). Everything else is an unrouted writer, scheduled into PRs 2–8.

**PR 1 warns; it never raises.** Strict mode lands in PR 9 and hard-depends on
PR 8 routing the three first-profile-creation sites
(`services/profile/__init__.py:555`, `:1165`, `services/session.py:1290`) —
raising before those are routed would break profile creation outright.

Two mechanisms, belt and braces:

1. the attribute `set` event — fires on plain assignment AND on keyword
   construction (``MasterProfile(profile_json=…)`` goes through ``setattr``,
   which is the case that defeated the original clause 6 and made the writer
   count 19 rather than 16);
2. a ``before_flush`` listener — catches a dirty ``profile_json`` that never
   passed the setter at all.

(Gate-test note for PR 9: the retention worker's ``update(...).values(...)``
touches only ``deleted_at`` — it is not a ``profile_json`` bypass.)
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import (
    MasterProfile,
    ProfileSnapshot,
    authorized_profile_write,
    reset_unauthorized_profile_writes,
    unauthorized_profile_writes,
)

_SEED = {"personal_info": {"full_name": "Daniel Kovač"}, "metadata": {}}


@pytest.fixture(autouse=True)
def _reset_counter():
    reset_unauthorized_profile_writes()
    yield
    reset_unauthorized_profile_writes()


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.user import User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    MasterProfile.__table__,
                    ProfileSnapshot.__table__,
                    User.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ── The setter fires, and on both write shapes ────────────────────────────────


def test_direct_assignment_from_an_unrouted_module_warns(caplog):
    record = MasterProfile(profile_json=dict(_SEED))
    reset_unauthorized_profile_writes()

    with caplog.at_level("WARNING", logger="applire.models.profile"):
        record.profile_json = {"personal_info": {"full_name": "Someone Else"}}

    assert unauthorized_profile_writes() == 1
    assert any("profile_json" in r.getMessage() for r in caplog.records)
    # WARN mode: the write still lands. PR 9 makes this raise.
    assert record.profile_json["personal_info"]["full_name"] == "Someone Else"


def test_keyword_construction_warns():
    """The shape the original clause-6 grep could not see."""
    MasterProfile(profile_json=dict(_SEED))

    assert unauthorized_profile_writes() == 1


def test_reading_profile_json_never_warns():
    record = MasterProfile(profile_json=dict(_SEED))
    reset_unauthorized_profile_writes()

    _ = record.profile_json
    _ = record.profile_json.get("personal_info")

    assert unauthorized_profile_writes() == 0


def test_the_guard_never_raises_in_warn_mode():
    """PR 1 must not break the unrouted writers that still exist by design."""
    record = MasterProfile(profile_json=dict(_SEED))
    record.profile_json = {"metadata": {}}  # no exception


# ── The token authorises ──────────────────────────────────────────────────────


def test_a_tokened_write_is_silent(caplog):
    record = MasterProfile(profile_json=dict(_SEED))
    reset_unauthorized_profile_writes()
    caplog.clear()

    with caplog.at_level("WARNING", logger="applire.models.profile"):
        with authorized_profile_write():
            record.profile_json = {"metadata": {"completeness_score": 0.5}}

    assert unauthorized_profile_writes() == 0
    assert [r for r in caplog.records if "profile_json" in r.getMessage()] == []


def test_the_token_is_released_after_the_block():
    record = MasterProfile(profile_json=dict(_SEED))
    with authorized_profile_write():
        record.profile_json = {"metadata": {}}
    reset_unauthorized_profile_writes()

    record.profile_json = {"metadata": {"application_count": 1}}

    assert unauthorized_profile_writes() == 1


# ── The two module exceptions ─────────────────────────────────────────────────


def test_the_exception_set_is_the_three_named_modules():
    """Enumerated here so PR 9's strict gate has one place to assert against —
    the day a twelfth writer grants itself an exception, this fails."""
    from applire.models.profile import AUTHORIZED_PROFILE_WRITE_MODULES

    assert set(AUTHORIZED_PROFILE_WRITE_MODULES) == {
        "applire/services/profile/commit.py",
        "applire/services/profile/snapshots.py",
        "applire/services/photo.py",
    }


@pytest.mark.asyncio
async def test_the_snapshot_undo_restore_does_not_warn(db_session):
    from applire.services.profile.snapshots import undo_last_merge

    with authorized_profile_write():
        record = MasterProfile(profile_json=dict(_SEED))
    db_session.add(record)
    await db_session.flush()
    db_session.add(
        ProfileSnapshot(
            profile_id=record.id,
            enrichment_record_id=str(uuid.uuid4()),
            profile_json={"personal_info": {"full_name": "Before The Merge"}},
        )
    )
    await db_session.commit()
    reset_unauthorized_profile_writes()

    result = await undo_last_merge(db_session)

    assert result.restored is True
    assert unauthorized_profile_writes() == 0


@pytest.mark.asyncio
async def test_the_photo_service_does_not_warn(db_session):
    from applire.models.user import User
    from applire.services.photo import delete_photo

    class _Storage:
        def __init__(self):
            self.deleted: list[str] = []

        async def delete(self, path: str) -> None:
            self.deleted.append(path)

    user_id = uuid.uuid4()
    db_session.add(
        User(id=user_id, email="daniel@example.invalid", photo_consent=True)
    )
    with authorized_profile_write():
        db_session.add(
            MasterProfile(
                profile_json={
                    "personal_info": {
                        "full_name": "Daniel Kovač",
                        "photo_url": "photos/photo.jpg",
                    },
                    "metadata": {},
                }
            )
        )
    await db_session.commit()
    reset_unauthorized_profile_writes()

    storage = _Storage()
    await delete_photo(user_id=user_id, db=db_session, storage=storage)

    assert storage.deleted == ["photos/photo.jpg"]
    assert unauthorized_profile_writes() == 0


# ── The before_flush backstop ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_untokened_write_is_also_caught_at_flush(db_session, caplog):
    """Belt and braces: warn-mode PR 1 counts the setter hit; the flush listener
    is the half that survives an ORM-level bypass in PR 9."""
    with authorized_profile_write():
        record = MasterProfile(profile_json=dict(_SEED))
    db_session.add(record)
    await db_session.commit()
    reset_unauthorized_profile_writes()

    record.profile_json = {"metadata": {"application_count": 3}}
    with caplog.at_level("WARNING", logger="applire.models.profile"):
        await db_session.flush()

    # One warning from the setter; the flush listener sees the setter already
    # ruled on this instance and does not double-count.
    assert unauthorized_profile_writes() == 1


@pytest.mark.asyncio
async def test_a_tokened_write_flushes_silently(db_session):
    with authorized_profile_write():
        record = MasterProfile(profile_json=dict(_SEED))
        db_session.add(record)
        await db_session.flush()

    assert unauthorized_profile_writes() == 0
