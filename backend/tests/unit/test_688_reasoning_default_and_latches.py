# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""#688 (ADR-009 amended 2026-10-10): reasoning OFF by default on OpenRouter and
Requesty, and the fallbacks that keep a model which refuses the switch working.

Pins, from the adversarial review of the default flip:
1. the shipped default — Settings and a default-constructed provider send the
   disable (every other provider test pins the toggle to false);
2. the refusal latch is PROCESS-wide — get_provider() builds a provider per
   request, so an instance latch cost one wasted 400 on every request;
3. OpenRouter retries ANY 400 on the disable once without it (not only the
   "mandatory" wording), latching only when that retry succeeds;
4. Requesty sends the operator's configured effort when the model refuses "none".
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai
import pytest


def _ok(text: str = "ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=text))],
        usage=None,
    )


def _bad_request(message: str, host: str) -> openai.BadRequestError:
    request = httpx.Request("POST", f"https://{host}/v1/chat/completions")
    return openai.BadRequestError(message=message, response=httpx.Response(400, request=request), body=None)


def _client(fn):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fn)))


def _pin(monkeypatch, prefix: str, *, effort: str = "") -> None:
    import applire.config as cfg

    monkeypatch.setattr(cfg.settings, f"{prefix}_reasoning_effort", effort, raising=False)
    monkeypatch.setattr(cfg.settings, f"{prefix}_disable_thinking", True, raising=False)


def _openrouter(model: str = "z-ai/glm-5.3-flash"):
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openrouter import OpenRouterProvider

        return OpenRouterProvider(api_key="k", model=model)


def _requesty(model: str = "m", **kwargs):
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider

        return RequestyProvider(api_key="k", model=model, **kwargs)


# ── 1. the shipped default ────────────────────────────────────────────────────


def test_settings_default_turns_reasoning_off_on_both_gateways(monkeypatch):
    from applire.config import Settings

    for var in ("OPENROUTER_DISABLE_THINKING", "REQUESTY_DISABLE_THINKING"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.openrouter_disable_thinking is True
    assert s.requesty_disable_thinking is True


@pytest.mark.asyncio
async def test_default_constructed_providers_send_the_disable(monkeypatch):
    from applire.config import Settings
    import applire.config as cfg

    defaults = Settings(_env_file=None)
    for name in ("openrouter", "requesty"):
        monkeypatch.setattr(cfg.settings, f"{name}_disable_thinking", getattr(defaults, f"{name}_disable_thinking"))
        monkeypatch.setattr(cfg.settings, f"{name}_reasoning_effort", "", raising=False)
    calls: list[dict] = []

    async def capture(**kwargs):
        calls.append(kwargs)
        return _ok()

    o = _openrouter("any/model")
    o._client = _client(capture)
    await o.acomplete("q")
    r = _requesty()
    r._client = _client(capture)
    await r.acomplete("q")
    assert calls[0]["extra_body"] == {"reasoning": {"enabled": False}}
    assert calls[1]["extra_body"] == {"reasoning_effort": "none"}


# ── 2. the latch outlives the provider object ─────────────────────────────────


@pytest.mark.asyncio
async def test_openrouter_refusal_latch_reaches_the_next_request_s_provider(monkeypatch):
    """MUTATION KILL: make `_reasoning_rejected` an instance attribute again and the
    second provider re-sends the disable (3 calls instead of 1)."""
    _pin(monkeypatch, "openrouter")
    attempts: list[dict] = []

    async def mandatory(**kwargs):
        attempts.append(kwargs)
        if (kwargs.get("extra_body") or {}).get("reasoning", {}).get("enabled") is False:
            raise _bad_request("Reasoning is mandatory for this endpoint and cannot be disabled", "openrouter.ai")
        return _ok()

    first = _openrouter()
    first._client = _client(mandatory)
    await first.acomplete("q")
    assert len(attempts) == 2

    attempts.clear()
    second = _openrouter()  # what get_provider() does for the next request
    second._client = _client(mandatory)
    await second.acomplete("q")
    assert len(attempts) == 1
    assert attempts[0].get("extra_body") is None


@pytest.mark.asyncio
async def test_latch_is_per_model(monkeypatch):
    _pin(monkeypatch, "openrouter")

    async def mandatory(**kwargs):
        if (kwargs.get("extra_body") or {}).get("reasoning", {}).get("enabled") is False:
            raise _bad_request("Reasoning is mandatory for this endpoint", "openrouter.ai")
        return _ok()

    glm = _openrouter("z-ai/glm-5.3-flash")
    glm._client = _client(mandatory)
    await glm.acomplete("q")
    assert _openrouter("z-ai/glm-5.3-flash")._reasoning_rejected is True
    assert _openrouter("google/gemini-3.5-flash")._reasoning_rejected is False


@pytest.mark.asyncio
async def test_requesty_refusal_latch_reaches_the_next_request_s_provider(monkeypatch):
    _pin(monkeypatch, "requesty")
    attempts: list[dict] = []

    async def refuse_effort(**kwargs):
        attempts.append(kwargs)
        if (kwargs.get("extra_body") or {}).get("reasoning_effort"):
            raise _bad_request("reasoning_effort is not supported", "router.eu.requesty.ai")
        return _ok()

    first = _requesty()
    first._client = _client(refuse_effort)
    await first.acomplete("q")
    assert len(attempts) == 2
    attempts.clear()
    second = _requesty()
    second._client = _client(refuse_effort)
    await second.acomplete("q")
    assert len(attempts) == 1
    assert attempts[0].get("extra_body") is None


# ── 3. OpenRouter: any 400 on the disable gets one retry without it ──────────


@pytest.mark.asyncio
async def test_openrouter_retries_a_differently_worded_refusal_and_latches(monkeypatch):
    """The default sends the disable to every model, so a refusal worded without
    "mandatory" must not fail the call. MUTATION KILL: restore `raise` for a
    non-mandatory 400 and this call raises."""
    _pin(monkeypatch, "openrouter", effort="low")
    attempts: list[dict] = []

    async def gemini_style(**kwargs):
        attempts.append(kwargs)
        if (kwargs.get("extra_body") or {}).get("reasoning", {}).get("enabled") is False:
            raise _bad_request("Budget 0 is invalid. This model only works in thinking mode.", "openrouter.ai")
        return _ok("answer")

    p = _openrouter("google/gemini-x")
    p._client = _client(gemini_style)
    assert await p.acomplete("q", max_tokens=256) == "answer"
    assert attempts[1]["extra_body"] == {"reasoning": {"effort": "low"}}
    assert attempts[1]["max_tokens"] >= 4096
    assert p._reasoning_rejected is True


@pytest.mark.asyncio
async def test_openrouter_unrelated_400_raises_the_original_and_does_not_latch(monkeypatch):
    _pin(monkeypatch, "openrouter")
    attempts: list[dict] = []

    async def too_long(**kwargs):
        attempts.append(kwargs)
        n = len(attempts)
        raise _bad_request(f"This model's maximum context length is 8192 tokens (attempt {n})", "openrouter.ai")

    p = _openrouter("any/model")
    p._client = _client(too_long)
    from applire.exceptions import LLMProviderError

    with pytest.raises(LLMProviderError) as info:
        await p.acomplete("q")
    assert "(attempt 1)" in info.value.detail  # the ORIGINAL error, not the retry's
    assert len(attempts) == 2  # the one retry without the disable
    assert p._reasoning_rejected is False


# ── 4. Requesty: the configured effort replaces a refused "none" ─────────────


@pytest.mark.asyncio
async def test_requesty_sends_the_configured_effort_when_none_is_refused(monkeypatch):
    """MUTATION KILL: drop `_effort_after_refusal` and the retry goes out bare."""
    _pin(monkeypatch, "requesty", effort="low")
    attempts: list[dict] = []

    async def refuse_none(**kwargs):
        attempts.append(kwargs)
        if (kwargs.get("extra_body") or {}).get("reasoning_effort") == "none":
            raise _bad_request("reasoning_effort 'none' is not supported by this model", "router.eu.requesty.ai")
        return _ok()

    p = _requesty()
    p._client = _client(refuse_none)
    await p.acomplete("q")
    assert [a.get("extra_body") for a in attempts] == [{"reasoning_effort": "none"}, {"reasoning_effort": "low"}]

    attempts.clear()
    await _requesty_with(refuse_none).acomplete("q")
    assert [a.get("extra_body") for a in attempts] == [{"reasoning_effort": "low"}]


def _requesty_with(fn):
    p = _requesty()
    p._client = _client(fn)
    return p


@pytest.mark.asyncio
async def test_requesty_strips_when_the_configured_effort_is_refused_too(monkeypatch):
    _pin(monkeypatch, "requesty", effort="low")
    attempts: list[dict] = []

    async def refuse_any(**kwargs):
        attempts.append(kwargs)
        if (kwargs.get("extra_body") or {}).get("reasoning_effort"):
            raise _bad_request("reasoning_effort is not supported", "router.eu.requesty.ai")
        return _ok()

    await _requesty_with(refuse_any).acomplete("q")
    assert [a.get("extra_body") for a in attempts] == [{"reasoning_effort": "none"}, {"reasoning_effort": "low"}, None]
    attempts.clear()
    await _requesty_with(refuse_any).acomplete("q")
    assert [a.get("extra_body") for a in attempts] == [None]


@pytest.mark.asyncio
async def test_requesty_policy_flag_400_does_not_latch_reasoning_on(monkeypatch):
    """Build-5 delivery run: OpenAI's content-policy 400 links
    `…/guides/reasoning#advice-on-prompting`; reading that link as a refusal
    latched reasoning ON for the model for the rest of the process."""
    _pin(monkeypatch, "requesty")
    attempts: list[dict] = []
    flagged = {"done": False}

    async def policy_once(**kwargs):
        attempts.append(kwargs)
        if not flagged["done"]:
            flagged["done"] = True
            raise _bad_request(
                "Invalid prompt: your prompt was flagged as potentially violating our usage policy. "
                "Please try again with a different prompt: "
                "https://platform.openai.com/docs/guides/reasoning#advice-on-prompting",
                "router.eu.requesty.ai",
            )
        return _ok()

    first = _requesty()
    first._client = _client(policy_once)
    await first.acomplete("q")
    assert first._reasoning_rejected is False
    attempts.clear()
    second = _requesty()
    second._client = _client(policy_once)
    await second.acomplete("q")
    assert (attempts[0].get("extra_body") or {}).get("reasoning_effort") == "none"
