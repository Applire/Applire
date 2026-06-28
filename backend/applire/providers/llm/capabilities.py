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

"""US191 (ADR-047 §5) — model capability probe.

A deferred OPTIMISATION on top of the segmentation floor (US189/US190): when a
model's output limit is discoverable we can pre-select segmented mode and skip
the doomed first large call. It is **never** the stability mechanism —
segmentation must work with the probe absent (ADR-047 §5 boundary). Therefore the
probe:

* requires no auth (OpenRouter ``/api/v1/models`` is public; Ollama ``/api/show``
  is local),
* is cached for the process lifetime (one fetch per model),
* never raises — any failure returns ``None`` and the caller falls back to the
  reactive path,
* covers only OpenRouter (output cap) and Ollama (context length); opaque
  OpenAI-compatible endpoints expose nothing, so they are not probed.

The HTTP fetcher is injectable so the logic is unit-tested without a network.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

_OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

# An injectable async HTTP-JSON fetcher: (method, url, json=, headers=) -> parsed JSON.
FetchJson = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ModelCapabilities:
    """What we could discover about a model. Every field is optional — an absent
    value means "unknown", which the caller treats as "fall back to reactive"."""

    max_output_tokens: int | None = None
    context_length: int | None = None
    reasoning_mandatory: bool | None = None


# Process-lifetime cache keyed by (provider, model, base_url). A cached ``None``
# is a real result (probed, nothing usable) — don't re-fetch it every call.
_CACHE: dict[tuple[str, str, str], ModelCapabilities | None] = {}


def clear_capability_cache() -> None:
    """Drop the probe cache (tests; or an operator model switch)."""
    _CACHE.clear()


async def probe_model_capabilities(
    provider: str,
    model: str,
    *,
    base_url: str = "",
    api_key: str = "",
    fetch_json: FetchJson | None = None,
) -> ModelCapabilities | None:
    """Probe ``model``'s limits for ``provider``; cached, never raises.

    Returns ``None`` when the provider is not probeable or the probe fails — the
    caller then relies on the reactive segmentation fallback."""
    key = (provider.lower(), model, base_url)
    if key in _CACHE:
        return _CACHE[key]

    fetch = fetch_json or _default_fetch_json
    try:
        if provider.lower() == "openrouter":
            caps = await _probe_openrouter(model, base_url, fetch, api_key)
        elif provider.lower() == "ollama":
            caps = await _probe_ollama(model, base_url, fetch)
        else:
            caps = None  # opaque / non-probeable endpoint (ADR-047 §5)
    except Exception:  # noqa: BLE001 — the probe is best-effort; never break a generation
        logger.debug("capability probe failed for %s/%s; degrading to reactive", provider, model)
        caps = None

    _CACHE[key] = caps
    return caps


async def _probe_openrouter(
    model: str, base_url: str, fetch: FetchJson, api_key: str
) -> ModelCapabilities | None:
    """OpenRouter ``/api/v1/models`` (public): per-model ``top_provider``
    ``max_completion_tokens`` is the output cap we care about."""
    root = (base_url or _OPENROUTER_DEFAULT_BASE_URL).rstrip("/")
    # The list endpoint is auth-free; send the key if we have it (harmless).
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    payload = await fetch("GET", f"{root}/models", headers=headers)
    entry = _find_model_entry(payload, model)
    if entry is None:
        return None
    top = entry.get("top_provider") or {}
    max_out = top.get("max_completion_tokens")
    ctx = top.get("context_length") or entry.get("context_length")
    return ModelCapabilities(
        max_output_tokens=int(max_out) if isinstance(max_out, (int, float)) else None,
        context_length=int(ctx) if isinstance(ctx, (int, float)) else None,
    )


async def _probe_ollama(
    model: str, base_url: str, fetch: FetchJson
) -> ModelCapabilities | None:
    """Ollama ``/api/show`` exposes context length (no output cap)."""
    root = (base_url or "").rstrip("/")
    data = await fetch("POST", f"{root}/api/show", json={"name": model})
    info = (data or {}).get("model_info") or {}
    ctx = next(
        (v for k, v in info.items() if k.endswith(".context_length") and isinstance(v, (int, float))),
        None,
    )
    if ctx is None:
        return None
    return ModelCapabilities(context_length=int(ctx))


def _find_model_entry(payload: Any, model: str) -> dict | None:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return None
    return next((e for e in data if isinstance(e, dict) and e.get("id") == model), None)


async def _default_fetch_json(method: str, url: str, *, json: Any = None, headers: Any = None) -> Any:
    """Real HTTP fetcher (short timeout; the probe must never stall a generation)."""
    import httpx

    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.request(method, url, json=json, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def resolve_effective_output_cap() -> int:
    """The output-token ceiling the segmentation decisions should use (ADR-047 §2/§5).

    Precedence: an operator-declared ``LLM_MAX_OUTPUT_TOKENS`` always wins (explicit
    override). Otherwise, if the active provider is probeable and reports a cap, use
    it. Otherwise ``0`` (unknown) — the reactive fallback covers it. Never raises."""
    from applire.config import settings

    declared = settings.llm_max_output_tokens
    if declared and declared > 0:
        return declared

    provider = settings.llm_provider.lower()
    coords = _active_model_coords(provider, settings)
    if coords is None:
        return 0
    model, base_url, api_key = coords
    caps = await probe_model_capabilities(provider, model, base_url=base_url, api_key=api_key)
    if caps and caps.max_output_tokens:
        return caps.max_output_tokens
    return 0


def _active_model_coords(provider: str, settings: Any) -> tuple[str, str, str] | None:
    """Model/base-url/key for the active provider, or ``None`` if not probeable."""
    if provider == "openrouter":
        return (settings.openrouter_model, settings.openrouter_base_url, settings.openrouter_api_key)
    if provider == "ollama":
        return (settings.ollama_model, settings.ollama_base_url, "")
    return None
