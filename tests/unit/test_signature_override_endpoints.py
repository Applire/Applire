# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""F-4b (founder ruling, 2026-09-11) — PATCH /api/cv/{cv_id}/signature and
PATCH /api/cover-letter/{cl_id}/signature over the real ASGI stack.

Service-level precedence and render-seam properties are covered in
``backend/tests/unit/test_signature_document_override.py``; this file stays
at the HTTP surface — the multipart-free JSON body, the exact status codes
(200/404/422) FastAPI's own schema validation and the two service
``LookupError``s map to. Follows the ``routers/cv.py``/``routers/cover_letter.py``
mounting pattern ``tests/unit/test_signature_endpoints.py`` already uses for
``routers/signature.py``: ``httpx.ASGITransport`` + ``AsyncClient`` against a
throwaway ``FastAPI()`` with ``get_db``/``get_auth_provider`` overridden.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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
async def client(db):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.cover_letter import router as cl_router
    from applire.routers.cv import router as cv_router

    app = FastAPI()
    app.include_router(cv_router)
    app.include_router(cl_router)
    app.dependency_overrides[get_db] = lambda: db

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _ready_cv(job_id, profile_id, signature_override=None):
    from applire.models.cv import GeneratedCV

    minimal = {
        "contact": {"name": "T", "email": "t@t.com", "phone": "", "location": "", "linkedin": "", "photo_url": None},
        "summary": "", "work_history": [], "education": [],
        "skills": [], "languages": [], "show_photo": False,
    }
    return GeneratedCV(
        id=uuid.uuid4(), job_analysis_id=job_id, profile_id=profile_id,
        tailored_data=minimal, template="classic_german", status="ready",
        signature_override=signature_override,
    )


def _ready_letter(job_id, profile_id, signature_override=None):
    from applire.models.cover_letter import GeneratedCoverLetter

    return GeneratedCoverLetter(
        id=uuid.uuid4(), job_analysis_id=job_id, profile_id=profile_id,
        template="classic_german", letter_data={}, status="ready",
        signature_override=signature_override,
    )


# ---------------------------------------------------------------------------
# PATCH /api/cv/{cv_id}/signature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_cv_signature_sets_true_and_returns_effective(client, db):
    cv = _ready_cv(uuid.uuid4(), uuid.uuid4())
    db.add(cv)
    await db.commit()

    r = await client.patch(f"/api/cv/{cv.id}/signature", json={"signature_override": True})
    assert r.status_code == 200
    body = r.json()
    assert body["signature_override"] is True
    # No settings row exists (no signature was ever uploaded) — not even an
    # override can render an image that was never stored (the same "no
    # settings row" short-circuit _signature_path_if_enabled documents).
    assert body["signature_effective"] is False
    assert body["cv_id"] == str(cv.id)

    await db.refresh(cv)
    assert cv.signature_override is True


@pytest.mark.asyncio
async def test_patch_cv_signature_clears_with_null(client, db):
    cv = _ready_cv(uuid.uuid4(), uuid.uuid4(), signature_override=True)
    db.add(cv)
    await db.commit()

    r = await client.patch(f"/api/cv/{cv.id}/signature", json={"signature_override": None})
    assert r.status_code == 200
    body = r.json()
    assert body["signature_override"] is None
    assert body["signature_effective"] is False  # CV kind default is off (F-0)

    await db.refresh(cv)
    assert cv.signature_override is None


@pytest.mark.asyncio
async def test_patch_cv_signature_omitted_body_field_also_clears(client, db):
    """`signature_override` defaults to None in the request schema — an
    empty JSON object is a valid "reset" request, not a 422."""
    cv = _ready_cv(uuid.uuid4(), uuid.uuid4(), signature_override=False)
    db.add(cv)
    await db.commit()

    r = await client.patch(f"/api/cv/{cv.id}/signature", json={})
    assert r.status_code == 200
    assert r.json()["signature_override"] is None


@pytest.mark.asyncio
async def test_patch_cv_signature_invalid_value_422(client, db):
    cv = _ready_cv(uuid.uuid4(), uuid.uuid4())
    db.add(cv)
    await db.commit()

    r = await client.patch(f"/api/cv/{cv.id}/signature", json={"signature_override": "yes-please"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_cv_signature_unknown_cv_404(client):
    r = await client.patch(f"/api/cv/{uuid.uuid4()}/signature", json={"signature_override": True})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_cv_signature_not_ready_404(client, db):
    from applire.models.cv import GeneratedCV

    cv = GeneratedCV(
        id=uuid.uuid4(), job_analysis_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        tailored_data={}, template="classic_german", status="pending",
    )
    db.add(cv)
    await db.commit()

    r = await client.patch(f"/api/cv/{cv.id}/signature", json={"signature_override": True})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/cover-letter/{cl_id}/signature
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_cover_letter_signature_sets_false_and_returns_effective(client, db):
    letter = _ready_letter(uuid.uuid4(), uuid.uuid4())
    db.add(letter)
    await db.commit()

    r = await client.patch(
        f"/api/cover-letter/{letter.id}/signature", json={"signature_override": False}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["signature_override"] is False
    assert body["signature_effective"] is False
    assert body["cover_letter_id"] == str(letter.id)

    await db.refresh(letter)
    assert letter.signature_override is False


@pytest.mark.asyncio
async def test_patch_cover_letter_signature_clears_with_null(client, db):
    letter = _ready_letter(uuid.uuid4(), uuid.uuid4(), signature_override=False)
    db.add(letter)
    await db.commit()

    r = await client.patch(
        f"/api/cover-letter/{letter.id}/signature", json={"signature_override": None}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["signature_override"] is None
    # letter kind default is on (F-0), but no signature is on file, so the
    # resolved effective state is still False — this asserts the endpoint
    # goes through the real precedence seam rather than echoing the kind
    # default blindly.
    assert body["signature_effective"] is False

    await db.refresh(letter)
    assert letter.signature_override is None


@pytest.mark.asyncio
async def test_patch_cover_letter_signature_invalid_value_422(client, db):
    letter = _ready_letter(uuid.uuid4(), uuid.uuid4())
    db.add(letter)
    await db.commit()

    r = await client.patch(
        f"/api/cover-letter/{letter.id}/signature", json={"signature_override": 42}
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_cover_letter_signature_unknown_letter_404(client):
    r = await client.patch(
        f"/api/cover-letter/{uuid.uuid4()}/signature", json={"signature_override": True}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patch_cover_letter_signature_not_ready_404(client, db):
    from applire.models.cover_letter import GeneratedCoverLetter

    letter = GeneratedCoverLetter(
        id=uuid.uuid4(), job_analysis_id=uuid.uuid4(), profile_id=uuid.uuid4(),
        template="classic_german", letter_data={}, status="pending",
    )
    db.add(letter)
    await db.commit()

    r = await client.patch(
        f"/api/cover-letter/{letter.id}/signature", json={"signature_override": True}
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Service-level LookupError path (test_cv_color_endpoint.py precedent) — the
# router's own exception mapping is covered above; these pin the service
# function's contract independent of HTTP.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_cv_signature_override_service_raises_for_unknown_cv(db):
    from applire.services.cv import set_cv_signature_override

    with pytest.raises(LookupError):
        await set_cv_signature_override(uuid.uuid4(), True, db)


@pytest.mark.asyncio
async def test_set_cover_letter_signature_override_service_raises_for_unknown_letter(db):
    from applire.services.cover_letter import set_cover_letter_signature_override

    with pytest.raises(LookupError):
        await set_cover_letter_signature_override(uuid.uuid4(), True, db)
