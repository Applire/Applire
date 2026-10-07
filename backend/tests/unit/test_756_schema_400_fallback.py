# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#756 — a 400 on a schema-carrying call is retried once without the schema.

Measured 2026-10-07 (model qualification matrix for #688): every Claude host
OpenRouter routes `anthropic/claude-haiku-4.5` to answered all 75/75 reconcile
calls with HTTP 400 under the shipped `LLM_STRUCTURED_OUTPUT=auto`. Neither
refusal is worded the way `base.is_schema_rejection_message` expects, so the
M-3 latch never fired, the call never fell back to `json_object`, and
`reconcile/engine.py` turned every 400 into an empty `ReconcileResult` — every
interview answer and every import merge was silently lost.

The fix (founder ruling M-1, ADR-063 clause (f) amended 2026-10-07) decides by
the RETRY, not the wording: a 400 that disappears without the schema is a schema
rejection by construction, so the latch is set; a 400 that persists surfaces
exactly as before and does NOT latch (the 2026-09-10 adversarial finding — an
unrelated 400 must never turn structured output off — stays closed).

One seam test set per provider that sends a schema (OpenRouter, Requesty), with
the two refusals captured verbatim from the matrix run as fixtures (request and
account ids replaced).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from applire.providers.llm.base import is_schema_rejection_message
from applire.providers.llm.openrouter import OpenRouterProvider
from applire.providers.llm.requesty import RequestyProvider
from applire.services.profile.reconcile.schema_out import reconcile_json_schema_param

# ── the two refusals, verbatim from the 2026-10-07 matrix LLM log ────────────

INVALID_ENUM_400 = (
    "Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, "
    "'metadata': {'raw': '{\"type\":\"error\",\"error\":{\"type\":\"invalid_request_error\","
    "\"message\":\"output_config.format.schema: Invalid schema: Enum value \\'already_known\\' "
    "does not match declared type \\'[\\'string\\', \\'null\\']\\'\"},\"request_id\":\"req_X\"}', "
    "'provider_name': 'Azure', 'is_byok': False, 'previous_errors': [{'code': 400, "
    "'message': 'Provider returned error', 'provider_name': 'Amazon Bedrock', "
    "'raw': '{\"message\":\"output_config.format.schema: Invalid schema: Enum value "
    "\\'already_known\\' does not match declared type \\'[\\'string\\', \\'null\\']\\'\"}'}]}}, "
    "'user_id': 'user_X'}"
)

GRAMMAR_TOO_LARGE_400 = (
    "Error code: 400 - {'error': {'message': 'Provider returned error', 'code': 400, "
    "'metadata': {'raw': '{\"type\":\"error\",\"error\":{\"type\":\"invalid_request_error\","
    "\"message\":\"The compiled grammar is too large, which would cause performance issues. "
    "Simplify your tool schemas or reduce the number of strict tools.\"},"
    "\"request_id\":\"req_X\"}', 'provider_name': 'Azure', 'is_byok': False, "
    "'previous_errors': [{'code': 400, 'message': 'Provider returned error', "
    "'provider_name': 'Amazon Bedrock', 'raw': '{\"message\":\"The compiled grammar is too "
    "large, which would cause performance issues. Simplify your tool schemas or reduce the "
    "number of strict tools.\"}'}]}}, 'user_id': 'user_X'}"
)

CONTEXT_LENGTH_400 = (
    "Error code: 400 - This model's maximum context length is 8192 tokens. Your request "
    "resulted in 9000 tokens. Please reduce the length of the messages."
)

CAPTURED_REFUSALS = [
    pytest.param(INVALID_ENUM_400, id="invalid-enum"),
    pytest.param(GRAMMAR_TOO_LARGE_400, id="grammar-too-large"),
]

PROVIDERS = [
    pytest.param(OpenRouterProvider, id="openrouter"),
    pytest.param(RequestyProvider, id="requesty"),
]

_GOOD_JSON = '{"ops": [], "ambiguities": [], "denials": [], "empty_reason": "already_known"}'


def _bad_request(message: str) -> openai.BadRequestError:
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://gateway.invalid/v1/chat/completions")
    )
    return openai.BadRequestError(message, response=response, body=None)


def _completion(content: str = _GOOD_JSON) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
    )


class _ScriptedClient:
    """Answers each `create` from a script keyed on the response_format type.

    `fail` maps a response_format type (`json_schema`, `json_object`) to the
    400 message that request gets; anything not in it succeeds. Every request's
    response_format type and extra_body are recorded in `calls`.
    """

    def __init__(self, fail: dict[str, str]):
        self.fail = fail
        self.calls: list[tuple[str | None, dict | None]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        fmt = (kwargs.get("response_format") or {}).get("type")
        self.calls.append((fmt, kwargs.get("extra_body")))
        if fmt in self.fail:
            raise _bad_request(self.fail[fmt])
        return _completion()

    @property
    def formats(self) -> list[str | None]:
        return [fmt for fmt, _ in self.calls]


def _provider(cls, client: _ScriptedClient):
    provider = cls(api_key="test-key", model="anthropic/claude-haiku-4.5",
                   disable_thinking=False, reasoning_effort="")
    provider._client = client
    return provider


def _schema_fmt() -> dict:
    return {"type": "json_schema", "json_schema": reconcile_json_schema_param()}


# ── the premise: the shipped wording regex does not recognise either refusal ─


@pytest.mark.parametrize("message", CAPTURED_REFUSALS)
def test_the_captured_refusals_are_not_recognised_by_the_wording_regex(message):
    """Why the wording alone cannot carry the fallback: both real refusals miss
    it. If a later regex widening makes these match, the retry still holds —
    this test only documents that the regex is not the control."""
    assert is_schema_rejection_message(message) is False


# ── 400 → retry without schema → latch ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", PROVIDERS)
@pytest.mark.parametrize("message", CAPTURED_REFUSALS)
async def test_a_schema_400_is_retried_once_without_the_schema_and_latched(cls, message):
    """MUTATION KILLS: drop the schema-less retry in `_create` and the call
    raises; drop the latch assignment and `_json_schema_rejected` stays False."""
    client = _ScriptedClient(fail={"json_schema": message})
    provider = _provider(cls, client)

    response = await provider._create(
        max_tokens=1024, extra_body=None,
        model="anthropic/claude-haiku-4.5", messages=[], response_format=_schema_fmt(),
    )

    assert response.choices[0].message.content == _GOOD_JSON
    assert client.formats == ["json_schema", "json_object"]
    assert provider._json_schema_rejected is True


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", PROVIDERS)
@pytest.mark.parametrize("message", CAPTURED_REFUSALS)
async def test_after_the_latch_the_next_call_sends_no_schema(cls, message):
    """End to end through `aparse_json`: the first reconcile call completes on
    `json_object` (the turn is NOT lost), and the second call does not pay the
    rejected round-trip again.

    MUTATION KILL: drop the latch and the second call sends the schema again
    (calls become schema, object, schema, object)."""
    client = _ScriptedClient(fail={"json_schema": message})
    provider = _provider(cls, client)
    schema = reconcile_json_schema_param()

    first = await provider.aparse_json("p", json_schema=schema)
    second = await provider.aparse_json("p", json_schema=schema)

    assert first == json.loads(_GOOD_JSON)
    assert second == json.loads(_GOOD_JSON)
    assert client.formats == ["json_schema", "json_object", "json_object"]


# ── a 400 that persists without the schema: surfaced as today, no loop ──────


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", PROVIDERS)
async def test_a_400_that_also_fails_without_the_schema_surfaces_and_does_not_latch(cls):
    """A context-length 400 fails with or without the schema. It costs exactly
    one retry, the ORIGINAL error surfaces, and structured output stays on —
    the 2026-09-10 adversarial finding (an unrelated 400 must not latch)."""
    client = _ScriptedClient(
        fail={"json_schema": CONTEXT_LENGTH_400, "json_object": CONTEXT_LENGTH_400}
    )
    provider = _provider(cls, client)

    with pytest.raises(openai.BadRequestError) as excinfo:
        await provider._create(
            max_tokens=1024, extra_body=None,
            model="m", messages=[], response_format=_schema_fmt(),
        )

    assert CONTEXT_LENGTH_400 in str(excinfo.value)
    assert client.formats == ["json_schema", "json_object"]
    assert provider._json_schema_rejected is False


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", PROVIDERS)
async def test_the_surfaced_error_is_the_original_not_the_retrys(cls):
    """When the schema-less retry fails for a DIFFERENT reason, the caller
    still sees the error of the request it actually made."""
    client = _ScriptedClient(
        fail={"json_schema": INVALID_ENUM_400, "json_object": CONTEXT_LENGTH_400}
    )
    provider = _provider(cls, client)

    with pytest.raises(openai.BadRequestError) as excinfo:
        await provider._create(
            max_tokens=1024, extra_body=None,
            model="m", messages=[], response_format=_schema_fmt(),
        )

    assert "Invalid schema" in str(excinfo.value)
    assert client.formats == ["json_schema", "json_object"]
    assert provider._json_schema_rejected is False


# ── a 400 on a call WITHOUT a schema is untouched ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("cls", PROVIDERS)
@pytest.mark.parametrize("message", [*CAPTURED_REFUSALS, CONTEXT_LENGTH_400])
async def test_a_400_on_a_call_without_a_schema_is_not_retried(cls, message):
    client = _ScriptedClient(fail={"json_object": message, None: message})
    provider = _provider(cls, client)

    with pytest.raises(openai.BadRequestError):
        await provider._create(
            max_tokens=1024, extra_body=None,
            model="m", messages=[], response_format={"type": "json_object"},
        )
    with pytest.raises(openai.BadRequestError):
        await provider._create(max_tokens=1024, extra_body=None, model="m", messages=[])

    assert client.formats == ["json_object", None]
    assert provider._json_schema_rejected is False


# ── the reasoning fallbacks still see the original 400 ──────────────────────


@pytest.mark.asyncio
async def test_openrouter_mandatory_reasoning_400_on_a_schema_call_still_reaches_its_fallback():
    """A model that refuses `reasoning: {enabled: false}` 400s with or without
    the schema; the schema-less retry fails, and the ORIGINAL 400 must still
    reach the mandatory-reasoning fallback (M-4) — and must not latch the
    schema off, because the schema was never the problem."""
    mandatory = "Error code: 400 - Reasoning is mandatory for this endpoint and cannot be disabled"
    calls: list[tuple[str | None, dict | None]] = []

    async def create(**kwargs):
        fmt = (kwargs.get("response_format") or {}).get("type")
        extra = kwargs.get("extra_body")
        calls.append((fmt, extra))
        if extra and extra.get("reasoning", {}).get("enabled") is False:
            raise _bad_request(mandatory)
        return _completion()

    provider = OpenRouterProvider(api_key="k", model="z-ai/glm", disable_thinking=True,
                                  reasoning_effort="")
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    await provider._create(
        max_tokens=1024, extra_body={"reasoning": {"enabled": False}},
        model="m", messages=[], response_format=_schema_fmt(),
    )

    assert [fmt for fmt, _ in calls] == ["json_schema", "json_object", "json_schema"]
    assert calls[-1][1] is None
    assert provider._reasoning_rejected is True
    assert provider._json_schema_rejected is False


@pytest.mark.asyncio
async def test_requesty_reasoning_effort_400_on_a_schema_call_still_reaches_its_fallback():
    """Requesty's twin of the test above: a `reasoning_effort` refusal on a
    schema call still gets the stripped-reasoning retry, schema intact."""
    refusal = "Error code: 400 - reasoning_effort is not supported for this model"
    calls: list[tuple[str | None, dict | None]] = []

    async def create(**kwargs):
        fmt = (kwargs.get("response_format") or {}).get("type")
        extra = kwargs.get("extra_body")
        calls.append((fmt, extra))
        if extra and "reasoning_effort" in extra:
            raise _bad_request(refusal)
        return _completion()

    provider = RequestyProvider(api_key="k", model="m", disable_thinking=True,
                                reasoning_effort="")
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    await provider._create(
        max_tokens=1024, extra_body={"reasoning_effort": "none"},
        model="m", messages=[], response_format=_schema_fmt(),
    )

    assert [fmt for fmt, _ in calls] == ["json_schema", "json_object", "json_schema"]
    assert calls[-1][1] is None
    assert provider._reasoning_rejected is True
    assert provider._json_schema_rejected is False
