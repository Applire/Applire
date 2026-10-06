# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-30 seams — a stored file path is data, never a capability (w4-fix-own).

The adversarial tests (``test_adv_ownership_*``) pin the reported chain; this
file pins the CLASS, one seam test per call site:

* the provider: ``LocalStorageProvider.read``/``delete`` refuse every path that
  does not resolve inside the upload directory (absolute, ``..``, symlink, the
  directory itself);
* every caller that reads or deletes a stored path (photo GET/replace/delete,
  signature GET/render/replace/delete, CV render photo, upload TTL purge) — a
  foreign path is neither read nor unlinked;
* every op-shaped door that could write ``personal_info.photo_url``
  (``ApplyImportMerge`` first import and merge import, ``ReplaceSection``, the
  ``reconcile_import`` model path, ``undo_last_merge``) — the stored value stands;
* erasure unlinks only what no surviving row references, and still unlinks the
  rest.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import applire.models  # noqa: F401
from applire import ownership
from applire.db.session import Base
from applire.models.user import User
from applire.schemas.profile import MasterProfileData, PersonalInfo
from applire.storage.base import PathOutsideStorageError
from applire.storage.local import LocalStorageProvider
from tests.support.profile_factory import make_master_profile

pytestmark = pytest.mark.no_owner_context


@pytest.fixture
def dirs(tmp_path):
    upload = tmp_path / "uploads"
    upload.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.jpg"
    secret.write_bytes(b"NOT-YOURS")
    return upload, secret


# ─── the provider ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provider_read_refuses_every_path_outside_the_upload_dir(dirs):
    upload, secret = dirs
    storage = LocalStorageProvider(str(upload))
    link = upload / "innocent.jpg"
    os.symlink(secret, link)
    for path in (
        str(secret),                                   # absolute foreign path
        str(upload / ".." / "outside" / "secret.jpg"),  # traversal
        str(link),                                      # symlink pointing out
        str(upload),                                    # the directory itself
        "/proc/self/environ",
    ):
        with pytest.raises(PathOutsideStorageError):
            await storage.read(path)


@pytest.mark.asyncio
async def test_provider_read_still_reads_its_own_files_relative_and_absolute(dirs, monkeypatch):
    upload, _secret = dirs
    storage = LocalStorageProvider(str(upload))
    path = await storage.save(b"MINE", "photo.jpg")
    assert await storage.read(path) == b"MINE"
    # A relative UPLOAD_DIR (the default `./data/uploads`) stores relative paths.
    monkeypatch.chdir(upload.parent)
    rel = LocalStorageProvider("uploads")
    rel_path = await rel.save(b"REL", "x.png")
    assert not os.path.isabs(rel_path)
    assert await rel.read(rel_path) == b"REL"


@pytest.mark.asyncio
async def test_provider_delete_refuses_outside_and_deletes_inside(dirs):
    upload, secret = dirs
    storage = LocalStorageProvider(str(upload))
    await storage.delete(str(secret))
    await storage.delete(str(upload / ".." / "outside" / "secret.jpg"))
    assert secret.exists()
    own = await storage.save(b"x", "a.jpg")
    await storage.delete(own)
    assert not os.path.exists(own)


# ─── a two-user world ────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def world():
    eng = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(eng, expire_on_commit=False)
    with ownership.unscoped("tooling"):
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as s:
            a = User(id=uuid.uuid4(), email="seam-a@example.org", role="user")
            b = User(id=uuid.uuid4(), email="seam-b@example.org", role="user")
            s.add_all([a, b])
            await s.commit()
    yield factory, a, b
    await eng.dispose()


async def _seed_profile(factory, user, photo_url):
    with ownership.unscoped("tooling"):
        async with factory() as s:
            p = make_master_profile(
                user_id=user.id,
                profile_json=MasterProfileData(
                    personal_info=PersonalInfo(name="X", photo_url=photo_url)
                ).model_dump(mode="json"),
            )
            s.add(p)
            await s.commit()
            return p.id


async def _seed_signature(factory, user, path):
    from applire.models.user_settings import UserSettings

    with ownership.unscoped("tooling"):
        async with factory() as s:
            s.add(UserSettings(user_id=user.id, signature_path=path, signature_in_letter=True))
            await s.commit()


# ─── photo service (REST GET/POST/DELETE /api/profile/photo) ─────────────────


@pytest.mark.asyncio
async def test_photo_get_bytes_refuses_a_foreign_path_as_no_photo(world, dirs):
    from applire.services.photo import get_photo_bytes

    factory, a, _b = world
    upload, secret = dirs
    await _seed_profile(factory, a, str(secret))
    with ownership.owner_context(a.id):
        async with factory() as s:
            with pytest.raises(LookupError):
                await get_photo_bytes(user_id=a.id, db=s, storage=LocalStorageProvider(str(upload)))


@pytest.mark.asyncio
async def test_photo_upload_does_not_unlink_a_foreign_old_path(world, dirs):
    from applire.services.photo import upload_photo

    factory, a, _b = world
    upload, secret = dirs
    await _seed_profile(factory, a, str(secret))
    with ownership.owner_context(a.id):
        async with factory() as s:
            await upload_photo(
                user_id=a.id, file_bytes=b"jpeg", content_type="image/jpeg", db=s,
                storage=LocalStorageProvider(str(upload)),
            )
    assert secret.exists()


@pytest.mark.asyncio
async def test_photo_delete_does_not_unlink_a_foreign_path(world, dirs):
    from applire.services.photo import delete_photo

    factory, a, _b = world
    upload, secret = dirs
    await _seed_profile(factory, a, str(secret))
    with ownership.owner_context(a.id):
        async with factory() as s:
            await delete_photo(user_id=a.id, db=s, storage=LocalStorageProvider(str(upload)))
    assert secret.exists()


# ─── signature service ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signature_get_bytes_refuses_a_foreign_path_as_no_signature(world, dirs):
    from applire.services.signature import get_signature_bytes

    factory, a, _b = world
    upload, secret = dirs
    await _seed_signature(factory, a, str(secret))
    with ownership.owner_context(a.id):
        async with factory() as s:
            with pytest.raises(LookupError):
                await get_signature_bytes(user_id=a.id, db=s, storage=LocalStorageProvider(str(upload)))


@pytest.mark.asyncio
async def test_signature_render_seam_omits_a_foreign_path(world, dirs, monkeypatch):
    from applire.config import settings
    from applire.services.signature import resolve_signature_data_uri

    factory, a, _b = world
    upload, secret = dirs
    monkeypatch.setattr(settings, "upload_dir", str(upload))
    monkeypatch.setattr(settings, "storage_backend", "local")
    await _seed_signature(factory, a, str(secret))
    with ownership.owner_context(a.id):
        async with factory() as s:
            uri = await resolve_signature_data_uri(s, document="letter", user_id=a.id)
    assert uri is None


@pytest.mark.asyncio
async def test_signature_upload_and_delete_do_not_unlink_a_foreign_path(world, dirs):
    from applire.services.signature import delete_signature, upload_signature

    factory, a, b = world
    upload, secret = dirs
    storage = LocalStorageProvider(str(upload))
    await _seed_signature(factory, a, str(secret))
    await _seed_signature(factory, b, str(secret))
    with ownership.owner_context(a.id):
        async with factory() as s:
            await upload_signature(
                user_id=a.id, file_bytes=b"png", content_type="image/png", db=s, storage=storage,
            )
    with ownership.owner_context(b.id):
        async with factory() as s:
            await delete_signature(user_id=b.id, db=s, storage=storage)
    assert secret.exists()


# ─── CV render (HTML/PDF and DOCX both resolve through this seam) ────────────


@pytest.mark.asyncio
async def test_cv_render_contact_photo_from_a_foreign_path_is_omitted(dirs):
    from applire.schemas.cv import TailoredContact, TailoredCVData
    from applire.services.cv import _with_resolved_contact_photo

    upload, secret = dirs
    tailored = TailoredCVData(contact=TailoredContact(name="X", photo_url=str(secret)), show_photo=True)
    out = await _with_resolved_contact_photo(tailored, LocalStorageProvider(str(upload)))
    assert out.contact.photo_url is None


# ─── retention upload TTL purge ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retention_upload_purge_does_not_unlink_a_foreign_file_path(world, dirs, monkeypatch):
    from applire.config import settings
    from applire.models.uploads import UploadRecord
    from applire.retention.worker import _purge_uploads

    factory, a, _b = world
    upload, secret = dirs
    monkeypatch.setattr(settings, "upload_dir", str(upload))
    monkeypatch.setattr(settings, "storage_backend", "local")
    old = datetime.now(timezone.utc) - timedelta(days=30)
    with ownership.unscoped("tooling"):
        async with factory() as s:
            s.add(UploadRecord(
                user_id=a.id, original_filename="cv.pdf", content_hash="h", mime_type="application/pdf",
                file_path=str(secret), byte_size=1, created_at=old,
                expires_at=old + timedelta(days=7),
            ))
            await s.commit()
    with ownership.unscoped("retention"):
        async with factory() as s:
            await _purge_uploads(s)
    assert secret.exists()


# ─── erasure: refcount keeps shared, still deletes own ───────────────────────


@pytest.mark.asyncio
async def test_erasure_still_unlinks_an_unshared_own_photo(world, dirs):
    from applire.services import erasure

    factory, a, b = world
    upload, _secret = dirs
    storage = LocalStorageProvider(str(upload))
    own = await storage.save(b"a-photo", "photo.jpg")
    other = await storage.save(b"b-photo", "photo.jpg")
    await _seed_profile(factory, a, own)
    await _seed_profile(factory, b, other)
    async with factory() as s:
        await erasure.erase(s, a.id, "vault", storage=storage)
    assert not os.path.exists(own), "the refcount kept a file nobody references any more"
    assert os.path.exists(other)


@pytest.mark.asyncio
async def test_erasure_keeps_a_file_another_user_references_via_signature(world, dirs):
    from applire.services import erasure

    factory, a, b = world
    upload, _secret = dirs
    storage = LocalStorageProvider(str(upload))
    shared = await storage.save(b"shared", "photo.png")
    await _seed_profile(factory, a, shared)
    await _seed_signature(factory, b, shared)
    async with factory() as s:
        await erasure.erase(s, a.id, "account", storage=storage)
    assert os.path.exists(shared)


# ─── op-shaped writers of personal_info.photo_url ────────────────────────────


def test_apply_import_merge_first_import_cannot_carry_a_photo_url():
    """The FIRST import installs the parsed CV wholesale (`merged=incoming`)."""
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import ApplyImportMerge

    incoming = MasterProfileData(personal_info=PersonalInfo(name="M", photo_url="/etc/passwd"))
    result = apply_ops(MasterProfileData(), [ApplyImportMerge(merged=incoming)], "cv_upload")
    assert result.profile.personal_info.photo_url is None
    assert result.profile.personal_info.name == "M"  # the rest of the import landed
    assert not [c for c in result.changes if c.field == "photo_url"]


def test_apply_import_merge_keeps_the_stored_photo_url():
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import ApplyImportMerge

    stored = MasterProfileData(personal_info=PersonalInfo(name="M", photo_url="/data/uploads/mine.jpg"))
    merged = MasterProfileData(personal_info=PersonalInfo(name="M", photo_url="/data/uploads/theirs.jpg"))
    result = apply_ops(stored, [ApplyImportMerge(merged=merged)], "cv_upload")
    assert result.profile.personal_info.photo_url == "/data/uploads/mine.jpg"


def test_replace_section_op_cannot_write_photo_url():
    """The section door (REST PATCH + MCP update_profile) refuses in field_edit;
    the op itself is held by the applier too — no door-only control."""
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import ReplaceSection

    stored = MasterProfileData(personal_info=PersonalInfo(name="M"))
    op = ReplaceSection(section="personal_info", value={"name": "M", "photo_url": "/etc/hostname"})
    result = apply_ops(stored, [op], "manual_edit")
    assert result.profile.personal_info.photo_url is None


def test_section_door_refuses_photo_url_for_rest_and_mcp():
    """REST PATCH /api/profile/personal_info and MCP update_profile share this adapter."""
    from applire.services.profile.field_edit import build_replace_section_op

    with pytest.raises(ValueError, match="photo"):
        build_replace_section_op("personal_info", {"photo_url": "/etc/hostname"})


@pytest.mark.asyncio
async def test_reconcile_import_model_op_cannot_fill_an_empty_photo_slot():
    """The import's model path: a `set_personal_info photo_url` op from the
    reconcile LLM (fed by the uploaded CV's text) on an EMPTY slot."""
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    class _Stub:
        async def aparse_json(self, prompt, **kwargs):
            return {"ops": [{"op": "set_personal_info", "field": "photo_url",
                             "value": "/data/uploads/someone-else.jpg"}], "ambiguities": []}

    existing = MasterProfileData(personal_info=PersonalInfo(name="Anna"))
    incoming = MasterProfileData(personal_info=PersonalInfo(name="Anna", photo_url="/data/uploads/someone-else.jpg"))
    result = await reconcile_import(existing, incoming, "test", _Stub())
    assert result.merged_profile.personal_info.photo_url is None


@pytest.mark.asyncio
async def test_undo_last_merge_keeps_the_current_photo_url(world):
    """Undo restores the pre-merge vault, but the photo is the photo endpoints',
    not the merge's: a photo replaced after the merge must survive the undo."""
    from sqlalchemy import select

    from applire.models.profile import MasterProfile
    from applire.services.profile.snapshots import capture_pre_merge_snapshot, undo_last_merge
    from tests.support.profile_factory import set_profile_json

    factory, a, _b = world
    pid = await _seed_profile(factory, a, "/data/uploads/old.jpg")
    with ownership.owner_context(a.id):
        async with factory() as s:
            p = await s.get(MasterProfile, pid)
            await capture_pre_merge_snapshot(
                s, profile_id=pid, profile_json=dict(p.profile_json),
                enrichment_record_id=str(uuid.uuid4()),
            )
            new = dict(p.profile_json)
            new["personal_info"] = {**new["personal_info"], "photo_url": "/data/uploads/new.jpg"}
            set_profile_json(p, new)
            await s.commit()
        async with factory() as s:
            result = await undo_last_merge(s, user_id=a.id)
            assert result.restored
        async with factory() as s:
            p = (await s.execute(select(MasterProfile).where(MasterProfile.id == pid))).scalar_one()
            assert p.profile_json["personal_info"]["photo_url"] == "/data/uploads/new.jpg"
