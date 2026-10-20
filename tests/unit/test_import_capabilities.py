# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#789 L12 — the upload surfaces only advertise images when the OCR backend can run.

``OCR_BACKEND`` defaults to ``mistral_vision``, which needs ``MISTRAL_API_KEY``. An
instance on OpenRouter/Anthropic without that key used to invite an image the backend
then rejected with a generic failure. Now:

* ``GET /api/profile/import-capabilities`` reports ``image_import`` (a boolean — never
  the key, never its presence beyond that one bit);
* an image/scan upload on an instance that cannot OCR fails with a typed
  ``OCRUnavailableError`` that every door maps to a stable code (``ocr_unavailable``),
  which the frontend translates.

No Docker, no real LLM, no network.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.config import settings
from applire.exceptions import OCRUnavailableError
from applire.ocr import image_import_available

UID = uuid.uuid4()
SECRET = "sk-test-SECRET-key-value-0123456789"


@pytest.fixture(autouse=True)
def _act_as_uid():
    from applire.ownership import owner_context

    with owner_context(UID):
        yield


@pytest.fixture
def mistral_ocr(monkeypatch):
    monkeypatch.setattr(settings, "ocr_backend", "mistral_vision")
    return monkeypatch


def test_mistral_vision_needs_a_mistral_key(mistral_ocr):
    mistral_ocr.setattr(settings, "mistral_api_key", "")
    assert image_import_available() is False
    mistral_ocr.setattr(settings, "mistral_api_key", "   ")
    assert image_import_available() is False
    mistral_ocr.setattr(settings, "mistral_api_key", SECRET)
    assert image_import_available() is True


def test_mistral_vision_ignores_the_llm_provider_choice(mistral_ocr):
    """OCR talks to Mistral directly — an OpenRouter/Anthropic LLM provider with a
    key for THAT provider does not make OCR runnable."""
    mistral_ocr.setattr(settings, "mistral_api_key", "")
    mistral_ocr.setattr(settings, "llm_provider", "openrouter")
    mistral_ocr.setattr(settings, "openrouter_api_key", SECRET)
    assert image_import_available() is False


def test_tesseract_needs_its_python_module_and_binary(monkeypatch):
    monkeypatch.setattr(settings, "ocr_backend", "tesseract")
    with patch("applire.ocr.capability._tesseract_runnable", return_value=True):
        assert image_import_available() is True
    with patch("applire.ocr.capability._tesseract_runnable", return_value=False):
        assert image_import_available() is False


def test_unknown_backend_is_not_available(monkeypatch):
    monkeypatch.setattr(settings, "ocr_backend", "carrier-pigeon")
    assert image_import_available() is False


@pytest.mark.asyncio
async def test_mistral_extractor_without_a_key_raises_the_typed_error_without_calling_the_sdk():
    from applire.ocr.mistral_vision import MistralVisionExtractor

    with pytest.raises(OCRUnavailableError):
        await MistralVisionExtractor(api_key="").extract(b"\x89PNG", "image/png")


def test_import_job_classifies_the_typed_error_as_a_stable_code():
    from applire.services.profile.import_jobs import classify_import_error

    assert classify_import_error(OCRUnavailableError("no key")) == "ocr_unavailable"


@pytest_asyncio.fixture
async def client():
    from applire.auth import get_auth_provider
    from applire.routers import profile as profile_router

    app = FastAPI()
    app.include_router(profile_router.router)
    app.dependency_overrides[profile_router._get_provider] = lambda: MagicMock()
    app.dependency_overrides[profile_router._get_storage] = lambda: MagicMock()
    app.dependency_overrides[profile_router._get_ocr] = lambda: MagicMock()
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=UID))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_capabilities_endpoint_reports_a_boolean_and_never_the_key(client, mistral_ocr):
    mistral_ocr.setattr(settings, "mistral_api_key", "")
    r = await client.get("/api/profile/import-capabilities")
    assert r.status_code == 200
    assert r.json() == {"image_import": False}

    mistral_ocr.setattr(settings, "mistral_api_key", SECRET)
    r = await client.get("/api/profile/import-capabilities")
    assert r.json() == {"image_import": True}
    assert SECRET not in r.text


@pytest.mark.asyncio
async def test_sync_upload_names_the_cause_when_ocr_is_unavailable(client):
    with patch(
        "applire.routers.profile.upload_cv",
        new=AsyncMock(side_effect=OCRUnavailableError("MISTRAL_API_KEY is not set")),
    ):
        r = await client.post(
            "/api/profile/upload", files={"file": ("scan.png", b"\x89PNG", "image/png")}
        )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "ocr_unavailable"
    assert "MISTRAL_API_KEY" not in r.text  # the cause is named by code, not by env detail
