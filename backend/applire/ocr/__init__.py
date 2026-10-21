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

"""CVImageExtractor factory — mirrors the LLMProvider / AuthProvider pattern (ADR 014)."""

from applire.ocr.base import CVImageExtractor
from applire.ocr.capability import image_import_available

__all__ = ["CVImageExtractor", "get_ocr_extractor", "image_import_available"]


def get_ocr_extractor() -> CVImageExtractor:
    """Return the configured CVImageExtractor based on OCR_BACKEND env var."""
    from applire.config import settings

    backend = settings.ocr_backend.lower()
    if backend == "mistral_vision":
        from applire.ocr.mistral_vision import MistralVisionExtractor

        return MistralVisionExtractor(api_key=settings.mistral_api_key)
    if backend == "tesseract":
        from applire.ocr.tesseract import TesseractExtractor

        return TesseractExtractor()
    raise ValueError(
        f"Unknown OCR_BACKEND '{backend}'. "
        "Supported: mistral_vision (default), tesseract."
    )
