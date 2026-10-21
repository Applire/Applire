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

"""Tesseract OCR backend — opt-in via OCR_BACKEND=tesseract (ADR 014).

Requires:
  - pytesseract installed in the Python environment
  - Tesseract system binary (see docker-compose.override.yml for self-hosters)
"""

import asyncio
from io import BytesIO

from applire.exceptions import OCRUnavailableError
from applire.ocr.base import CVImageExtractor


class TesseractExtractor(CVImageExtractor):
    async def extract(self, image_bytes: bytes, mime_type: str) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            raise OCRUnavailableError(
                "OCR_BACKEND=tesseract requires 'pytesseract' and 'Pillow'. "
                "Install them or switch to OCR_BACKEND=mistral_vision."
            ) from exc

        image = Image.open(BytesIO(image_bytes))

        def _run() -> str:
            return pytesseract.image_to_string(image, lang="deu+eng")

        return await asyncio.get_event_loop().run_in_executor(None, _run)
