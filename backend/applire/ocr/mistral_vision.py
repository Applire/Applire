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

"""Mistral Vision OCR backend — Community Edition default (ADR 014).

Uses pixtral-12b directly via the mistralai SDK.
The CVImageExtractor abstraction is vendor-independent; this implementation
calls the Mistral SDK directly because vision calls require a different model
and multimodal message format that the LLMProvider interface (ADR 009) does not
expose — that interface is intentionally minimal for text completion.
"""

import base64

from applire.ocr.base import CVImageExtractor
from applire.providers.llm.base import unclassified_provider_error

_VISION_MODEL = "pixtral-12b-2409"
_SYSTEM_PROMPT = (
    "You are a CV text extractor. Extract all text from the provided CV image "
    "exactly as it appears, preserving section structure. "
    "Return only the raw extracted text — no commentary."
)


class MistralVisionExtractor(CVImageExtractor):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def extract(self, image_bytes: bytes, mime_type: str) -> str:
        try:
            from mistralai import Mistral
        except ImportError as exc:
            raise RuntimeError(
                "Mistral Vision requires the 'mistralai' package."
            ) from exc

        b64 = base64.standard_b64encode(image_bytes).decode()
        data_uri = f"data:{mime_type};base64,{b64}"

        try:
            client = Mistral(api_key=self._api_key)
            response = await client.chat.complete_async(
                model=_VISION_MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_uri}},
                            {"type": "text", "text": "Extract all text from this CV image."},
                        ],
                    },
                ],
                temperature=0.0,
            )
        except Exception as exc:
            # Error detail hardening: an SDK / HTTP-client error leaves as our own type.
            wrapped = unclassified_provider_error(exc, "Mistral OCR")
            if wrapped is None:
                raise
            raise wrapped from None
        return response.choices[0].message.content or ""
