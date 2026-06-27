# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""CV-upload / import truncation mapping (fix: truncation integrity).

A reconcile that hits the token budget raises ``LLMTruncatedError`` up the
upload/import path. The router must surface that as a clean, user-appropriate
failure ("couldn't fully merge this CV — please try again") with NO raw
Pydantic/internal text, so the frontend marks the file failed rather than
persisting a silent half-merge.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from applire.exceptions import LLMTruncatedError

# The internal sentence the engine/provider would carry — it must NOT leak.
_RAW_INTERNAL = "Model google/gemini-3.5-flash hit the token budget (stop_reason='length')"


@pytest_asyncio.fixture
async def client():
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.profile import (
        router,
        _get_ocr,
        _get_provider,
        _get_storage,
    )

    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_db] = lambda: AsyncMock()
    app.dependency_overrides[_get_provider] = lambda: AsyncMock()
    app.dependency_overrides[_get_storage] = lambda: AsyncMock()
    app.dependency_overrides[_get_ocr] = lambda: AsyncMock()

    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    app.dependency_overrides[get_auth_provider] = lambda: auth

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_upload_reconcile_truncation_maps_to_clean_message(client):
    truncated = AsyncMock(side_effect=LLMTruncatedError(_RAW_INTERNAL))
    with patch("applire.routers.profile.upload_cv", new=truncated):
        resp = await client.post(
            "/api/profile/upload",
            files={"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

    # A dedicated, non-500 status (not the generic crash bucket).
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    # Clean, user-appropriate guidance...
    assert "again" in detail.lower()
    # ...and absolutely no raw provider/internal text.
    assert "stop_reason" not in detail
    assert "token budget" not in detail
    assert _RAW_INTERNAL not in detail
    assert "gemini" not in detail.lower()


@pytest.mark.asyncio
async def test_import_reconcile_truncation_maps_to_clean_message(client):
    truncated = AsyncMock(side_effect=LLMTruncatedError(_RAW_INTERNAL))
    with patch("applire.routers.profile.import_from_linkedin_pdf", new=truncated):
        resp = await client.post(
            "/api/profile/import",
            files={"file": ("cv.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert "again" in detail.lower()
    assert "stop_reason" not in detail
    assert "token budget" not in detail
    assert _RAW_INTERNAL not in detail
