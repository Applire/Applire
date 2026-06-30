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

"""US191 (E036, ADR-047 §5) — model capability probe.

A deferred OPTIMISATION on top of the segmentation floor: when a model's output
limit is discoverable (OpenRouter ``/api/v1/models``, auth-free; Ollama
``/api/show`` for context length) we can start segmented mode immediately and
skip the doomed first large call. Correctness NEVER depends on the probe — it is
cached, never a startup dependency, and any failure degrades silently to the
reactive fallback (US189/US190). Opaque OpenAI-compatible endpoints are not
probed.

Tests inject the HTTP fetcher (no network, no httpx mocking) — the same
provider-injection discipline the rest of the suite uses.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.providers.llm.capabilities import (  # noqa: E402
    ModelCapabilities,
    clear_capability_cache,
    probe_model_capabilities,
    resolve_effective_output_cap,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_capability_cache()
    yield
    clear_capability_cache()


def _openrouter_models_payload() -> dict:
    return {
        "data": [
            {
                "id": "mistralai/mistral-medium-3",
                "context_length": 131072,
                "top_provider": {"max_completion_tokens": 8192, "context_length": 131072},
                "supported_parameters": ["temperature", "max_tokens", "reasoning"],
            },
            {"id": "other/model", "top_provider": {"max_completion_tokens": 4096}},
        ]
    }


class TestOpenRouterProbe:
    @pytest.mark.asyncio
    async def test_reads_max_completion_tokens_for_the_active_model(self):
        async def fetch(method, url, *, json=None, headers=None):
            assert method == "GET" and url.endswith("/api/v1/models")
            return _openrouter_models_payload()

        caps = await probe_model_capabilities(
            "openrouter", "mistralai/mistral-medium-3", fetch_json=fetch
        )
        assert isinstance(caps, ModelCapabilities)
        assert caps.max_output_tokens == 8192

    @pytest.mark.asyncio
    async def test_unknown_model_id_yields_none(self):
        async def fetch(method, url, *, json=None, headers=None):
            return _openrouter_models_payload()

        caps = await probe_model_capabilities("openrouter", "no/such-model", fetch_json=fetch)
        assert caps is None


class TestOllamaProbe:
    @pytest.mark.asyncio
    async def test_reads_context_length_and_no_output_cap(self):
        async def fetch(method, url, *, json=None, headers=None):
            assert method == "POST" and url.endswith("/api/show")
            assert json == {"name": "llama3.2"}
            return {"model_info": {"llama.context_length": 131072, "general.architecture": "llama"}}

        caps = await probe_model_capabilities(
            "ollama", "llama3.2", base_url="http://ollama:11434", fetch_json=fetch
        )
        assert caps.context_length == 131072
        assert caps.max_output_tokens is None, "Ollama exposes no output cap"


class TestProbeIsBestEffort:
    @pytest.mark.asyncio
    async def test_non_probeable_provider_does_not_fetch(self):
        calls = []

        async def fetch(method, url, *, json=None, headers=None):
            calls.append(url)
            return {}

        caps = await probe_model_capabilities("mistral", "mistral-large", fetch_json=fetch)
        assert caps is None
        assert not calls, "opaque providers must not be probed (ADR-047 §5)"

    @pytest.mark.asyncio
    async def test_fetch_failure_degrades_to_none_never_raises(self):
        async def boom(method, url, *, json=None, headers=None):
            raise RuntimeError("network down")

        caps = await probe_model_capabilities("openrouter", "any/model", fetch_json=boom)
        assert caps is None, "a probe failure must degrade silently, not break generation"

    @pytest.mark.asyncio
    async def test_result_is_cached_no_second_fetch(self):
        calls = []

        async def fetch(method, url, *, json=None, headers=None):
            calls.append(url)
            return _openrouter_models_payload()

        for _ in range(3):
            await probe_model_capabilities(
                "openrouter", "mistralai/mistral-medium-3", fetch_json=fetch
            )
        assert len(calls) == 1, "the probe must be cached for the process lifetime"


class TestResolveEffectiveOutputCap:
    @pytest.mark.asyncio
    async def test_operator_declared_cap_wins(self, monkeypatch):
        from applire.config import settings
        monkeypatch.setattr(settings, "llm_max_output_tokens", 5000, raising=False)
        monkeypatch.setattr(settings, "llm_provider", "openrouter", raising=False)
        assert await resolve_effective_output_cap() == 5000

    @pytest.mark.asyncio
    async def test_falls_back_to_probed_cap_when_undeclared(self, monkeypatch):
        from applire.config import settings
        monkeypatch.setattr(settings, "llm_max_output_tokens", 0, raising=False)
        monkeypatch.setattr(settings, "llm_provider", "openrouter", raising=False)
        monkeypatch.setattr(settings, "openrouter_model", "capped/model", raising=False)
        monkeypatch.setattr(settings, "openrouter_base_url", "", raising=False)
        monkeypatch.setattr(settings, "openrouter_api_key", "", raising=False)
        # Pre-seed the cache so resolve() reads the probe without any network.
        from applire.providers.llm import capabilities as cap_mod
        cap_mod._CACHE[("openrouter", "capped/model", "")] = ModelCapabilities(max_output_tokens=8192)
        assert await resolve_effective_output_cap() == 8192

    @pytest.mark.asyncio
    async def test_non_probeable_and_undeclared_is_zero(self, monkeypatch):
        from applire.config import settings
        monkeypatch.setattr(settings, "llm_max_output_tokens", 0, raising=False)
        monkeypatch.setattr(settings, "llm_provider", "mistral", raising=False)
        assert await resolve_effective_output_cap() == 0, "unknown cap → reactive fallback"
