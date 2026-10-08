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

"""Mistral embedding provider — text-embedding-mistral-embed, 1024-dim (ADR 009)."""

from mistralai import Mistral

from applire.config import settings
from applire.providers.embedding.base import EmbeddingProvider
from applire.providers.llm.base import unclassified_provider_error

DEFAULT_MODEL = "mistral-embed"
EMBEDDING_DIM = 1024


class MistralEmbeddingProvider(EmbeddingProvider):
    """Mistral AI embedding provider (EU-hosted default, ADR 009)."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._client = Mistral(api_key=api_key or settings.mistral_api_key)
        self._model = model or settings.embedding_model or DEFAULT_MODEL
        self._dim = EMBEDDING_DIM

    async def embed(self, text: str) -> list[float]:
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                inputs=[text],
            )
        except Exception as exc:
            # Error detail hardening: an SDK / HTTP-client error leaves as our own type.
            wrapped = unclassified_provider_error(exc, "Mistral embeddings")
            if wrapped is None:
                raise
            raise wrapped from None
        return response.data[0].embedding
