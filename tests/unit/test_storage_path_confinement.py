# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""On the Strawberry branch, photo-delete and erasure confinement are pinned by
tests/unit/test_file_path_confinement_seams.py and the adv-ownership tests; the two
v0.42-shaped (owner-less) cases of the 0.42.1 backport were dropped here.

Storage hardening — file access stays inside the upload directory.

A stored path (``personal_info.photo_url``, ``user_settings.signature_path``,
``uploads.file_path``) is a value in a row. The local storage provider acts only
on paths that resolve inside its upload directory, and the vault applier keeps
``photo_url`` for the photo endpoints, its one writer.
"""

from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.profile import MasterProfileData, PersonalInfo
from applire.storage.local import LocalStorageProvider

USER_ID = uuid.uuid4()


@pytest.fixture
def dirs(tmp_path):
    upload = tmp_path / "uploads"
    upload.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.jpg"
    secret.write_bytes(b"NOT-A-STORED-FILE")
    return upload, secret


# ─── the provider ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_refuses_paths_outside_the_upload_dir(dirs):
    from applire.storage.base import PathOutsideStorageError

    upload, secret = dirs
    storage = LocalStorageProvider(str(upload))
    link = upload / "innocent.jpg"
    os.symlink(secret, link)
    for path in (
        str(secret),
        str(upload / ".." / "outside" / "secret.jpg"),
        str(link),
        str(upload),
    ):
        with pytest.raises(PathOutsideStorageError):
            await storage.read(path)


@pytest.mark.asyncio
async def test_read_still_reads_stored_files_absolute_and_relative(dirs, monkeypatch):
    upload, _secret = dirs
    storage = LocalStorageProvider(str(upload))
    path = await storage.save(b"MINE", "photo.jpg")
    assert await storage.read(path) == b"MINE"
    monkeypatch.chdir(upload.parent)
    rel = LocalStorageProvider("uploads")
    rel_path = await rel.save(b"REL", "x.png")
    assert await rel.read(rel_path) == b"REL"


@pytest.mark.asyncio
async def test_delete_refuses_outside_and_deletes_inside(dirs):
    upload, secret = dirs
    storage = LocalStorageProvider(str(upload))
    await storage.delete(str(secret))
    await storage.delete(str(upload / ".." / "outside" / "secret.jpg"))
    assert secret.exists()
    own = await storage.save(b"x", "a.jpg")
    await storage.delete(own)
    assert not os.path.exists(own)


# ─── callers ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cv_render_photo_outside_the_upload_dir_is_omitted(dirs):
    from applire.schemas.cv import TailoredContact, TailoredCVData
    from applire.services.cv import _resolve_photo_data_uri, _with_resolved_contact_photo

    upload, secret = dirs
    storage = LocalStorageProvider(str(upload))
    assert await _resolve_photo_data_uri(str(secret), storage) is None
    tailored = TailoredCVData(contact=TailoredContact(name="X", photo_url=str(secret)), show_photo=True)
    out = await _with_resolved_contact_photo(tailored, storage)
    assert out.contact.photo_url is None


@pytest_asyncio.fixture
async def db(dirs):
    from applire.db.session import Base
    from applire.models.application import Application
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.session import InterviewSession
    from applire.models.uploads import UploadRecord
    from applire.models.user import User
    from applire.models.user_settings import UserSettings
    from tests.support.profile_factory import make_master_profile

    _upload, secret = dirs
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    tables = [t.__table__ for t in (
        User, UploadRecord, MasterProfile, GeneratedCV, InterviewSession, GapAnalysis,
        GeneratedCoverLetter, Application, FlowSession, UserSettings,
    )]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(User(id=USER_ID, email="emma@example.com"))
        s.add(make_master_profile(profile_json=MasterProfileData(
            personal_info=PersonalInfo(name="Emma", photo_url=str(secret))
        ).model_dump(mode="json")))
        s.add(UserSettings(user_id=USER_ID, signature_path=str(secret)))
        await s.commit()
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_photo_and_signature_bytes_outside_the_upload_dir_are_not_on_file(db, dirs):
    from applire.services.photo import get_photo_bytes
    from applire.services.signature import get_signature_bytes

    upload, _secret = dirs
    storage = LocalStorageProvider(str(upload))
    with pytest.raises(LookupError):
        await get_photo_bytes(user_id=USER_ID, db=db, storage=storage)
    with pytest.raises(LookupError):
        await get_signature_bytes(user_id=USER_ID, db=db, storage=storage)


@pytest.mark.parametrize("source", ["cv_upload", "testimony", "agent_interview"])
def test_set_personal_info_does_not_fill_an_empty_photo_slot(source):
    from applire.services.profile.reconcile.apply import _apply_set_personal_info, apply_ops
    from applire.services.profile.reconcile.ops import SetPersonalInfo

    op = SetPersonalInfo(field="photo_url", value="/data/uploads/0f3c1d2e.jpg")
    result = apply_ops(MasterProfileData(personal_info=PersonalInfo(name="B")), [op], source)
    assert result.profile.personal_info.photo_url is None
    profile = MasterProfileData(personal_info=PersonalInfo(name="B"))
    _apply_set_personal_info(op, profile, source, [], [])
    assert profile.personal_info.photo_url is None


def test_import_merge_cannot_carry_a_photo_url():
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import ApplyImportMerge

    incoming = MasterProfileData(personal_info=PersonalInfo(name="M", photo_url="/etc/hostname"))
    result = apply_ops(MasterProfileData(), [ApplyImportMerge(merged=incoming)], "cv_upload")
    assert result.profile.personal_info.photo_url is None
    assert result.profile.personal_info.name == "M"
    stored = MasterProfileData(personal_info=PersonalInfo(name="M", photo_url="/data/uploads/mine.jpg"))
    result = apply_ops(stored, [ApplyImportMerge(merged=incoming)], "cv_upload")
    assert result.profile.personal_info.photo_url == "/data/uploads/mine.jpg"


def test_replace_section_op_cannot_write_photo_url():
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import ReplaceSection

    op = ReplaceSection(section="personal_info", value={"name": "M", "photo_url": "/etc/hostname"})
    result = apply_ops(MasterProfileData(personal_info=PersonalInfo(name="M")), [op], "manual_edit")
    assert result.profile.personal_info.photo_url is None
