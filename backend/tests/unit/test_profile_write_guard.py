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

"""ADR-063 clause 6 — the attribute-level write guard, STRICT (#480 PR 9).

`master_profiles.profile_json` may only be assigned by `commit_ops` (which holds
the `authorized_profile_write()` contextvar token across its assignment) or from
one of three named modules: `services/profile/commit.py`, `services/photo.py`
(`Binary` under GDPR Art. 9(2)(a)) and `services/profile/snapshots.py` (the undo
restore). Everything else raises :class:`UnauthorizedProfileWriteError`.

Two mechanisms, belt and braces:

1. the attribute `set` event — fires on plain assignment AND on keyword
   construction (``MasterProfile(profile_json=…)`` goes through ``setattr``,
   which is the case that defeated the original clause 6 and made the writer
   count 19 rather than 16). It raises *before* the value reaches the instance,
   so a refused write leaves no trace at all;
2. a ``before_flush`` listener — raises on a dirty ``profile_json`` that never
   passed the setter, i.e. an ORM/instrumentation-level bypass. Raising inside
   the flush aborts the transaction (PO ruling Q1(a), 2026-08-10): the write
   physically cannot reach the database. The poisoned session is recoverable by
   the ordinary ``rollback()`` — pinned below.

Both mechanisms are unconditional. There is no warn mode, no env-var escape and
no test-only bypass: a test that legitimately needs to build a fixture profile
opens the SAME public door production uses (`tests/support/profile_factory.py`).
"""
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import attributes

from applire.models.profile import (
    AUTHORIZED_PROFILE_WRITE_MODULES,
    MasterProfile,
    ProfileSnapshot,
    UnauthorizedProfileWriteError,
    authorized_profile_write,
)


def _seed() -> dict:
    """A fresh nested dict every call — these tests mutate `profile_json` in
    place on purpose, and a shared literal would leak between them."""
    return {"personal_info": {"full_name": "Daniel Kovač"}, "metadata": {}}


def _seeded() -> MasterProfile:
    """A fixture record, built through the public door — never a bypass."""
    with authorized_profile_write():
        return MasterProfile(profile_json=_seed())


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


# ── The setter refuses, on both write shapes ──────────────────────────────────


def test_direct_assignment_without_the_token_raises():
    record = _seeded()

    with pytest.raises(UnauthorizedProfileWriteError) as excinfo:
        record.profile_json = {"personal_info": {"full_name": "Someone Else"}}

    assert "clause 6" in str(excinfo.value)
    assert "commit_ops" in str(excinfo.value)


def test_the_refused_write_carries_the_caller_location():
    """The exception has to say WHERE, or the next unrouted writer is a hunt."""
    record = _seeded()

    with pytest.raises(UnauthorizedProfileWriteError) as excinfo:
        record.profile_json = {"metadata": {}}

    path, _, line = excinfo.value.where.rpartition(":")
    assert path.endswith("backend/tests/unit/test_profile_write_guard.py")
    assert line.isdigit()
    assert excinfo.value.where in str(excinfo.value)


def test_the_refused_write_never_lands_on_the_instance():
    record = _seeded()

    with pytest.raises(UnauthorizedProfileWriteError):
        record.profile_json = {"personal_info": {"full_name": "Someone Else"}}

    assert record.profile_json["personal_info"]["full_name"] == "Daniel Kovač"


def test_keyword_construction_without_the_token_raises():
    """The shape the original clause-6 grep could not see — pinned explicitly,
    because it is why the write-surface inventory came out at 16 writers when
    there were 19."""
    with pytest.raises(UnauthorizedProfileWriteError):
        MasterProfile(profile_json=_seed())


def test_keyword_construction_names_the_calling_file_not_the_declarative_init():
    """SQLAlchemy compiles a mapped class's `__init__` with the filename
    `<string>`, and on this shape it is the frame directly above the setter. If
    the guard reported it, every refused construction would read `<string>:4`
    and the fix would be a hunt."""
    with pytest.raises(UnauthorizedProfileWriteError) as excinfo:
        MasterProfile(profile_json=_seed())

    assert excinfo.value.where.split(":")[0].endswith(
        "backend/tests/unit/test_profile_write_guard.py"
    )


def test_reading_profile_json_is_never_a_write():
    record = _seeded()

    assert record.profile_json["personal_info"]["full_name"] == "Daniel Kovač"
    assert record.profile_json.get("metadata") == {}


# ── The token authorises ──────────────────────────────────────────────────────


def test_a_tokened_write_succeeds():
    record = _seeded()

    with authorized_profile_write():
        record.profile_json = {"metadata": {"completeness_score": 0.5}}

    assert record.profile_json == {"metadata": {"completeness_score": 0.5}}


def test_the_token_is_released_after_the_block():
    record = _seeded()
    with authorized_profile_write():
        record.profile_json = {"metadata": {}}

    with pytest.raises(UnauthorizedProfileWriteError):
        record.profile_json = {"metadata": {"application_count": 1}}


def test_the_token_is_released_even_when_the_block_raises():
    record = _seeded()
    with pytest.raises(ValueError):
        with authorized_profile_write():
            raise ValueError("boom")

    with pytest.raises(UnauthorizedProfileWriteError):
        record.profile_json = {"metadata": {}}


# ── The three module exceptions ───────────────────────────────────────────────


def test_the_exception_set_is_exactly_the_three_named_modules():
    """The gate that replaces the grep: the day a twelfth writer grants itself
    an exception, this test fails.

    Re-verified for this flip (ADR-063 amendment 5): the retention worker's
    `update(MasterProfile).…values(deleted_at=now)`
    (`applire/retention/worker.py:299`) touches `deleted_at` ONLY — it is not a
    `profile_json` write and needs no exception.
    """
    assert set(AUTHORIZED_PROFILE_WRITE_MODULES) == {
        "applire/services/profile/commit.py",
        "applire/services/profile/snapshots.py",
        "applire/services/photo.py",
    }


def test_a_write_from_an_authorized_module_succeeds_without_the_token():
    """The module exception is a filename check over the calling frames — the
    belt-and-braces half that survives a refactor moving an assignment out of
    the committer's token block."""
    record = _seeded()
    namespace: dict = {}
    exec(  # noqa: S102 — exercising the frame-filename mechanism directly
        compile(
            "def write(record, value):\n    record.profile_json = value\n",
            "/opt/applire/backend/applire/services/profile/commit.py",
            "exec",
        ),
        namespace,
    )

    namespace["write"](record, {"metadata": {"from_the_committer": True}})

    assert record.profile_json == {"metadata": {"from_the_committer": True}}


def test_a_write_from_an_unlisted_module_still_raises():
    """Same mechanism, a filename that is not in the set."""
    record = _seeded()
    namespace: dict = {}
    exec(  # noqa: S102
        compile(
            "def write(record, value):\n    record.profile_json = value\n",
            "/opt/applire/backend/applire/services/profile/rogue.py",
            "exec",
        ),
        namespace,
    )

    with pytest.raises(UnauthorizedProfileWriteError):
        namespace["write"](record, {"metadata": {}})


@pytest.mark.asyncio
async def test_the_snapshot_undo_restore_is_authorised(db_session):
    from applire.services.profile.snapshots import undo_last_merge

    record = _seeded()
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

    result = await undo_last_merge(db_session)

    assert result.restored is True
    assert record.profile_json["personal_info"]["full_name"] == "Before The Merge"


@pytest.mark.asyncio
async def test_the_photo_service_is_authorised(db_session):
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

    storage = _Storage()
    await delete_photo(user_id=user_id, db=db_session, storage=storage)

    assert storage.deleted == ["photos/photo.jpg"]


@pytest.mark.asyncio
async def test_the_committers_own_row_constructor_is_authorised(db_session):
    from applire.services.profile.commit import create_profile_record

    record = await create_profile_record(db_session)

    assert record.profile_json == {}


# ── The before_flush backstop ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_orm_level_bypass_raises_at_flush(db_session):
    """The realistic bypass the setter cannot see: the JSON column is mutated
    in place and the instance is flagged modified by hand. No `set` event ever
    fires, so only the flush listener stands between it and the vault."""
    record = _seeded()
    db_session.add(record)
    await db_session.commit()

    record.profile_json["personal_info"]["full_name"] = "Smuggled In"
    attributes.flag_modified(record, "profile_json")

    with pytest.raises(UnauthorizedProfileWriteError) as excinfo:
        await db_session.flush()

    assert "flush" in str(excinfo.value)


@pytest.mark.asyncio
async def test_the_flush_raise_keeps_the_bypass_out_of_the_database(db_session):
    record = _seeded()
    db_session.add(record)
    await db_session.commit()
    record_id = record.id

    record.profile_json["personal_info"]["full_name"] = "Smuggled In"
    attributes.flag_modified(record, "profile_json")
    with pytest.raises(UnauthorizedProfileWriteError):
        await db_session.commit()
    await db_session.rollback()

    stored = (
        await db_session.execute(
            sa.select(MasterProfile.profile_json).where(MasterProfile.id == record_id)
        )
    ).scalar_one()
    assert stored["personal_info"]["full_name"] == "Daniel Kovač"


@pytest.mark.asyncio
async def test_the_session_is_reusable_after_the_flush_raise(db_session):
    """PO ruling Q1(a) leaves a poisoned session behind. Pin the recovery: an
    ordinary rollback clears it and the session keeps working."""
    record = _seeded()
    db_session.add(record)
    await db_session.commit()
    record_id = record.id

    record.profile_json["personal_info"]["full_name"] = "Smuggled In"
    attributes.flag_modified(record, "profile_json")
    with pytest.raises(UnauthorizedProfileWriteError):
        await db_session.flush()

    await db_session.rollback()

    with authorized_profile_write():
        record.profile_json = {"personal_info": {"full_name": "Properly Routed"}}
    await db_session.commit()
    stored = (
        await db_session.execute(
            sa.select(MasterProfile.profile_json).where(MasterProfile.id == record_id)
        )
    ).scalar_one()
    assert stored["personal_info"]["full_name"] == "Properly Routed"


@pytest.mark.asyncio
async def test_a_tokened_write_flushes_silently(db_session):
    with authorized_profile_write():
        record = MasterProfile(profile_json=_seed())
        db_session.add(record)
        await db_session.flush()

    await db_session.commit()
    assert record.profile_json == _seed()


@pytest.mark.asyncio
async def test_the_setters_verdict_is_consumed_per_flush(db_session):
    """One authorised write authorises ONE flush. If the decision leaked, a
    later bypass on the same instance would ride in on it."""
    record = _seeded()
    db_session.add(record)
    await db_session.commit()

    with authorized_profile_write():
        record.profile_json = {"metadata": {"round": 1}}
    await db_session.commit()

    record.profile_json["round"] = 2
    attributes.flag_modified(record, "profile_json")
    with pytest.raises(UnauthorizedProfileWriteError):
        await db_session.flush()


# ── The bulk-UPDATE shape: a documented, verified GAP ─────────────────────────


@pytest.mark.asyncio
async def test_the_orm_bulk_update_shape_is_not_reached_by_either_mechanism(
    db_session,
):
    """#480 §5 claims the `before_flush` listener closes the
    `update(...).values(...)` ORM bypass. Measured against SQLAlchemy 2.0.36 it
    does not, and this test records the real behaviour rather than a comforting
    one.

    An ORM-enabled UPDATE is emitted directly by ``Session.execute``; it never
    enters the unit of work, the matched instance is not placed in
    ``session.dirty``, and its ``profile_json`` history reports no change — so
    the flush listener has nothing to look at, and the `set` event does not fire
    either. The only interception point for this shape is the
    ``do_orm_execute`` session event, which is a NEW mechanism and needs a PO
    ruling (raised by #480 PR 9; the STOP is recorded on the issue).

    When that ruling lands and the mechanism is built, this test goes red — that
    is exactly what it is for. It asserts the gap, not a guarantee.
    """
    record = _seeded()
    db_session.add(record)
    await db_session.commit()
    record_id = record.id

    await db_session.execute(
        update(MasterProfile)
        .where(MasterProfile.id == record_id)
        .values(profile_json={"personal_info": {"full_name": "Bulk Bypass"}})
    )
    await db_session.commit()

    stored = (
        await db_session.execute(
            sa.select(MasterProfile.profile_json).where(MasterProfile.id == record_id)
        )
    ).scalar_one()
    assert stored["personal_info"]["full_name"] == "Bulk Bypass", (
        "The bulk-UPDATE bypass is still open — see the docstring. If this "
        "assertion failed because the write was refused, the gap has been "
        "closed: delete this test and pin the refusal instead."
    )
