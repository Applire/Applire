# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""C9 (UX audit round 3, Emma) — a failed import appears in "Letzte Uploads".

A failed import job rolls its transaction back, so no ``UploadRecord`` exists and
the history listed only the files that were read: the failed file was invisible
and nothing named it. The history now carries the failed job (file name, stable
error code) until the same file name is read later.
"""
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from applire.auth import get_auth_provider
from applire.db.session import get_db
from applire.models.import_job import CVImportJob
from applire.routers.profile import router
from applire.services.profile.import_jobs import unrecovered_failed_imports

_T0 = datetime(2026, 11, 7, 7, 0, tzinfo=timezone.utc)
_USER_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _job(name: str, status: str, minutes: int, code: str | None = None) -> CVImportJob:
    return CVImportJob(
        id=uuid.uuid4(), user_id=_USER_ID, filename=name, status=status,
        error_code=code, created_at=_T0 + timedelta(minutes=minutes),
        expires_at=_T0 + timedelta(days=7),
    )


def _record(name: str, minutes: int):
    return SimpleNamespace(original_filename=name, created_at=_T0 + timedelta(minutes=minutes))


def test_a_failed_import_with_no_later_read_is_listed():
    failed = _job("linkedin-export.pdf", "failed", 5, "llm_timeout")
    out = unrecovered_failed_imports(
        [failed, _job("cv-2019.pdf", "ready", 1)], [_record("cv-2019.pdf", 1)]
    )
    assert out == [failed]


def test_a_failed_import_read_later_by_the_same_name_is_not_listed():
    failed = _job("linkedin-export.pdf", "failed", 5, "llm_timeout")
    later_upload = _record("LinkedIn-Export.pdf", 20)  # case-insensitive name match
    assert unrecovered_failed_imports([failed], [later_upload]) == []


def test_a_later_running_or_ready_job_by_the_same_name_hides_it():
    failed = _job("cv.pdf", "failed", 5)
    for status in ("pending", "processing", "ready"):
        assert unrecovered_failed_imports([failed, _job("cv.pdf", status, 9)], []) == []


def test_an_earlier_read_does_not_hide_a_later_failure():
    failed = _job("cv.pdf", "failed", 5)
    assert unrecovered_failed_imports([failed], [_record("cv.pdf", 1)]) == [failed]


def test_one_row_per_file_name_newest_failure_first():
    old = _job("cv.pdf", "failed", 1)
    new = _job("cv.pdf", "failed", 3)
    other = _job("other.pdf", "failed", 2)
    assert unrecovered_failed_imports([old, other, new], []) == [new, other]


@pytest.fixture()
def client():
    class _Auth:
        async def get_current_user(self, request, db=None):
            user = MagicMock()
            user.id = _USER_ID
            return user

    app = FastAPI()
    app.dependency_overrides[get_auth_provider] = lambda: _Auth()
    app.include_router(router)
    return app


def test_the_uploads_endpoint_lists_the_failed_file_with_its_code(client):
    read = MagicMock()
    read.id = uuid.uuid4()
    read.original_filename = "cv-2024.pdf"
    read.mime_type = "application/pdf"
    read.byte_size = 1000
    read.created_at = _T0 + timedelta(minutes=1)
    read.gate_status = None
    read.staged_extraction = None
    failed = _job("linkedin-export.pdf", "failed", 5, "llm_timeout")

    def _result(rows):
        res = MagicMock()
        res.scalars.return_value.all.return_value = rows
        return res

    db = AsyncMock()
    db.execute.side_effect = [_result([read]), _result([failed])]

    async def _db():
        yield db

    client.dependency_overrides[get_db] = _db
    body = TestClient(client).get("/api/profile/uploads").json()

    assert [(i["original_filename"], i["status"], i["error_code"]) for i in body] == [
        ("linkedin-export.pdf", "failed", "llm_timeout"),
        ("cv-2024.pdf", "read", None),
    ]
