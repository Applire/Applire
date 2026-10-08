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

"""OpenAI embedding provider — text-embedding-3-small, 1536-dim (ADR 009)."""

import openai

from applire.config import settings
from applire.providers.embedding.base import EmbeddingProvider
from applire.providers.llm.base import unclassified_provider_error

DEFAULT_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """OpenAI or OpenAI-compatible embedding provider (ADR 009)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        resolved_base_url = base_url or settings.openai_base_url or None
        self._client = openai.AsyncOpenAI(
            api_key=api_key or settings.openai_api_key or "local",
            base_url=resolved_base_url,
        )
        self._model = model or settings.embedding_model or DEFAULT_MODEL
        self._dim = EMBEDDING_DIM

    async def embed(self, text: str) -> list[float]:
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=text,
            )
        except Exception as exc:
            # Error detail hardening: an SDK / HTTP-client error leaves as our own type.
            wrapped = unclassified_provider_error(exc, "OpenAI embeddings")
            if wrapped is None:
                raise
            raise wrapped from None
        return response.data[0].embedding
