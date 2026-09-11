# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#359 — POST / GET / DELETE ``/api/profile/signature`` over the real ASGI
stack.

``applire.routers.signature`` is not yet mounted on the app in ``main.py``
(that wiring is a separate report patch — see the final report for this
work package), so this drives the router by mounting it on a throwaway
``FastAPI()`` instance, the same pattern ``tests/unit/test_cv_docx_endpoint.py``
and ``tests/unit/test_profile_health_router.py`` already use for other
routers: ``httpx.ASGITransport`` + ``AsyncClient`` against a real app, with
``get_db``/``get_auth_provider``/the router's own ``_get_storage`` overridden
via ``app.dependency_overrides``.

Chosen over calling the endpoint coroutines directly because the router's
contract is mostly HTTP: the multipart ``UploadFile`` parsing, the exact
status codes (200/204/400/404) the two service exceptions map to, the
``Content-Type``/``Cache-Control: no-store`` headers on the GET response, and
the fact that the router is mounted and its auth dependency resolves at all —
none of which a bare coroutine call would exercise (this repo's own notes
record FastAPI parameter defaults leaking into direct-call tests).

Service-level properties (storage, the render-time toggle, format_place_date,
the settings contract, the DOCX writers, retention, the migration) are
covered in ``backend/tests/unit/test_signature_service.py`` and not repeated
here.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-signature-payload-not-a-real-png"


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def user_id(db):
    from applire.models.user import User

    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"{uid}@example.de"))
    await db.commit()
    return uid


@pytest_asyncio.fixture
async def client(db, user_id, tmp_path):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.signature import _get_storage, router
    from applire.storage.local import LocalStorageProvider

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=user_id))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    storage = LocalStorageProvider(str(tmp_path))
    app.dependency_overrides[_get_storage] = lambda: storage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def test_router_is_mounted_at_the_profile_signature_prefix():
    from applire.routers.signature import router

    assert router.prefix == "/api/profile/signature"


@pytest.mark.asyncio
async def test_post_upload_returns_200_with_signature_url(client):
    r = await client.post(
        "/api/profile/signature",
        files={"file": ("signature.png", _PNG_BYTES, "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"signature_url"}
    assert body["signature_url"]


@pytest.mark.asyncio
async def test_post_upload_rejects_unsupported_content_type_with_400(client):
    r = await client.post(
        "/api/profile/signature",
        files={"file": ("signature.pdf", b"%PDF-1.4 not an image", "application/pdf")},
    )
    assert r.status_code == 400
    assert "detail" in r.json()


@pytest.mark.asyncio
async def test_post_upload_rejects_empty_file_with_400(client):
    r = await client.post(
        "/api/profile/signature",
        files={"file": ("signature.png", b"", "image/png")},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_post_upload_rejects_oversized_file_with_400(client):
    oversized = b"x" * (2 * 1024 * 1024 + 1)
    r = await client.post(
        "/api/profile/signature",
        files={"file": ("signature.png", oversized, "image/png")},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_signature_returns_bytes_with_no_store_header(client):
    upload = await client.post(
        "/api/profile/signature",
        files={"file": ("signature.png", _PNG_BYTES, "image/png")},
    )
    assert upload.status_code == 200

    r = await client.get("/api/profile/signature")
    assert r.status_code == 200
    assert r.content == _PNG_BYTES
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store", (
        "the bytes change in place under one stable URL — a cached response "
        "would show the previous signature after a replace"
    )


@pytest.mark.asyncio
async def test_get_signature_returns_404_when_none_on_file(client):
    r = await client.get("/api/profile/signature")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_signature_returns_204_and_the_signature_is_then_gone(client):
    upload = await client.post(
        "/api/profile/signature",
        files={"file": ("signature.png", _PNG_BYTES, "image/png")},
    )
    assert upload.status_code == 200

    r = await client.delete("/api/profile/signature")
    assert r.status_code == 204
    assert r.content == b""

    follow_up = await client.get("/api/profile/signature")
    assert follow_up.status_code == 404


@pytest.mark.asyncio
async def test_delete_signature_is_idempotent_204_when_nothing_on_file(client):
    r = await client.delete("/api/profile/signature")
    assert r.status_code == 204, "no signature on file is a no-op, not an error"


@pytest.mark.asyncio
async def test_reupload_replaces_the_served_bytes(client):
    first = await client.post(
        "/api/profile/signature",
        files={"file": ("signature.png", _PNG_BYTES, "image/png")},
    )
    assert first.status_code == 200

    second_bytes = _PNG_BYTES + b"-v2"
    second = await client.post(
        "/api/profile/signature",
        files={"file": ("signature.png", second_bytes, "image/png")},
    )
    assert second.status_code == 200
    assert second.json()["signature_url"] != first.json()["signature_url"]

    r = await client.get("/api/profile/signature")
    assert r.content == second_bytes
