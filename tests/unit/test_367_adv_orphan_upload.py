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

"""#367 (adversarial) — rewritten for ADR-092 cl. 4 (Strawberry W2, package 3b).

**What #367 pinned (v0.42):** an HELD import ``import_cv`` raised before any
``User`` row existed was stored with ``user_id=NULL``; three doors filtering on
``user_id == :uid`` lost it once a user appeared, so ``list_open_gates``,
``resolve_staged_extraction`` and the GDPR erasure were widened to
``user_id == :uid OR user_id IS NULL`` — sound while Community was single-user.

**Why the widening had to go:** with accounts, an ownerless row is nobody's,
and the ``OR IS NULL`` arm would hand one user's parked CV (its full
``staged_extraction``) to *every* user. ADR-092 cl. 4 removes the premise
instead: migration 0074 gives every NULL-owner upload to the stub user and
makes ``uploads.user_id`` NOT NULL; the MCP door needs a token, hence a user.

**What this file pins now (the same three doors, owner-keyed):**
* an upload without an owner cannot be stored at all (NOT NULL);
* ``list_open_gates`` lists the OWNER's parked CV and never another user's;
* ``resolve_staged_extraction`` resolves the owner's and answers a foreign
  ``staged_id`` exactly like a missing one (S-10);
* the owner's erasure deletes their parked CV (staged personal data included)
  and leaves another user's parked CV alone.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire import ownership

pytestmark = pytest.mark.no_owner_context


@pytest_asyncio.fixture
async def factory():
    from applire.db.session import Base
    import applire.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    with ownership.unscoped("tooling"):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _held(user_id):
    from applire.models.uploads import UploadRecord

    return UploadRecord(
        user_id=user_id,
        original_filename="cv.pdf",
        content_hash=uuid.uuid4().hex * 2,
        mime_type="application/pdf",
        file_path=f"/uploads/{uuid.uuid4().hex}.pdf",
        byte_size=1234,
        gate_status="name_divergence",
        staged_extraction={"personal_info": {"name": "Parked Person"}},
    )


@pytest_asyncio.fixture
async def two_holds(factory):
    from applire.models.user import User

    a = User(id=uuid.uuid4(), email="hold-a@example.org")
    b = User(id=uuid.uuid4(), email="hold-b@example.org")
    with ownership.unscoped("tooling"):
        async with factory() as s:
            s.add_all([a, b])
            await s.flush()
            ha, hb = _held(a.id), _held(b.id)
            s.add_all([ha, hb])
            await s.commit()
    return a.id, b.id, ha.id, hb.id


@pytest.mark.asyncio
async def test_an_upload_without_an_owner_cannot_be_stored(factory):
    with ownership.unscoped("tooling"):
        async with factory() as s:
            s.add(_held(None))
            with pytest.raises(IntegrityError):
                await s.commit()


@pytest.mark.asyncio
async def test_list_open_gates_lists_the_owners_hold_only(factory, two_holds):
    from applire.services.profile import list_open_gates

    a, b, ha, hb = two_holds
    async with factory() as s:
        with ownership.owner_context(a):
            assert [r.id for r in await list_open_gates(s, user_id=a)] == [ha]
            # no user_id → the owner context, never "everyone's"
            assert [r.id for r in await list_open_gates(s)] == [ha]
        assert [r.id for r in await list_open_gates(s, user_id=b)] == [hb]


@pytest.mark.asyncio
async def test_resolve_staged_extraction_refuses_a_foreign_hold_like_a_missing_one(factory, two_holds):
    from applire.services.profile import StagedExtractionNotFound, resolve_staged_extraction

    a, b, ha, hb = two_holds
    async with factory() as s:
        with pytest.raises(StagedExtractionNotFound) as foreign:
            await resolve_staged_extraction(s, hb, action="discard", user_id=a)
        with pytest.raises(StagedExtractionNotFound) as missing:
            await resolve_staged_extraction(s, uuid.uuid4(), action="discard", user_id=a)
        assert type(foreign.value) is type(missing.value)
        res = await resolve_staged_extraction(s, ha, action="discard", user_id=a)
        assert res.action == "discard"


@pytest.mark.asyncio
async def test_erasure_deletes_the_owners_hold_and_spares_the_other(factory, two_holds):
    from applire.models.uploads import UploadRecord
    from applire.services.erasure import erase

    class _Storage:
        deleted: list = []

        async def delete(self, p):
            self.deleted.append(p)

    a, b, ha, hb = two_holds
    async with factory() as s:
        counts = await erase(s, a, "vault", storage=_Storage())
    assert counts["uploads"] == 1
    with ownership.unscoped("tooling"):
        async with factory() as s:
            left = [r.id for r in (await s.execute(select(UploadRecord))).scalars()]
    assert left == [hb]
