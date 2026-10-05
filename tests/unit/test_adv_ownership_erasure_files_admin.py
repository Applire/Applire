# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial ownership review (w4): erasure, retention, files on disk, admin metadata.

Each test asserts the CORRECT isolated behaviour and fails on the current tree.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base
from applire.models.user import User
from applire.services import erasure
from tests.support.isolation import OwnerWorld
from tests.support.profile_factory import set_profile_json

pytestmark = pytest.mark.no_owner_context


def test_adv_ownership_201_import_door_must_not_set_photo_url_on_empty_slot():
    """The import door (reconcile SetPersonalInfo, fed by LLM output of an
    attacker-controlled CV/testimony) must never write ``photo_url``: it is a
    file path that erasure later deletes and the CV renderer reads. The guard
    only fires when the slot is already populated."""
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.apply import _apply_set_personal_info
    from applire.services.profile.reconcile.ops import SetPersonalInfo

    profile = MasterProfileData.model_validate({"personal_info": {"name": "Mallory"}})
    changes: list = []
    conflicts: list = []
    _apply_set_personal_info(
        SetPersonalInfo(field="photo_url", value="../../../etc/hostname"),
        profile, "cv_upload", changes, conflicts,
    )
    assert profile.personal_info.photo_url is None, (
        f"import door wrote photo_url={profile.personal_info.photo_url!r} into an empty slot"
    )


class _Recorder:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, path: str) -> None:
        self.deleted.append(path)


@pytest_asyncio.fixture
async def two_users():
    eng = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(eng, expire_on_commit=False)
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            a = User(id=uuid.uuid4(), email="adv-a@example.org", role="user")
            b = User(id=uuid.uuid4(), email="adv-b@example.org", role="user")
            s.add_all([a, b])
            await s.flush()
            wa, wb = OwnerWorld(s, a), OwnerWorld(s, b)
            pa, pb = await wa.profile(), await wb.profile()
            await s.commit()
    yield factory, a, b, pa.id, pb.id
    await eng.dispose()


async def _set_photo(factory, profile_id, path: str) -> None:
    from applire.models.profile import MasterProfile

    with ownership.unscoped("tooling"):
        async with factory() as s:
            p = await s.get(MasterProfile, profile_id)
            set_profile_json(p, {"personal_info": {"name": "x", "photo_url": path}})
            await s.commit()


@pytest.mark.asyncio
async def test_adv_ownership_202_erasure_must_not_delete_files_outside_the_upload_dir(
    two_users, tmp_path
):
    """Erasure deletes whatever path sits in profile_json.personal_info.photo_url
    through LocalStorageProvider.delete (no base-dir confinement)."""
    from applire.storage.local import LocalStorageProvider

    factory, a, _b, pa, _pb = two_users
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    victim = tmp_path / "outside-secret.txt"
    victim.write_text("keep me")
    await _set_photo(factory, pa, str(upload_dir / ".." / "outside-secret.txt"))

    async with factory() as s:
        await erasure.erase(s, a.id, "vault", storage=LocalStorageProvider(str(upload_dir)))

    assert victim.exists(), "erasure unlinked a file outside the upload dir via photo_url traversal"


@pytest.mark.asyncio
async def test_adv_ownership_203_erasure_must_not_delete_a_file_another_user_still_references(
    two_users,
):
    """A's erasure deletes a photo path B's profile also points at (path equality
    is the only key; there is no reference count on files like there is on postings)."""
    factory, a, _b, pa, pb = two_users
    shared = "/data/uploads/b-photo-0001.jpg"
    await _set_photo(factory, pa, shared)
    await _set_photo(factory, pb, shared)

    rec = _Recorder()
    async with factory() as s:
        await erasure.erase(s, a.id, "vault", storage=rec)

    assert shared not in rec.deleted, "A's erasure deleted a file B's profile still references"
