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

"""Hazelnut build 7 — the Anthropic adapter's JSON transport on no-prefill models.

UX audit 4 (2026-11-07, Claude Sonnet 5.5): `aparse_json` sent no prefill (a 400
on ≥ 4.6), no structured output and no format instruction, and Sonnet 5.5 answered
in prose before the JSON — the Truthfulness Oracle's cv_audit failed 2/2, field
expectations 12/44 (with max_tokens-300 truncations), the terminal CV review
truncated at 2560. Replays of the failed calls: 32/38 + 3/3 failed before, 0/41
after. These tests pin the request SHAPE of each control; the SDK is mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import anthropic
import httpx
import pytest
from anthropic.types import Message, TextBlock, ThinkingBlock, Usage

from applire.exceptions import LLMProviderError, LLMResponseParseError, LLMTruncatedError

_STRICT = {
    "name": "t",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "industry_context": {"type": ["string", "null"]},
            "work": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"role": {"type": "string"}},
                    "required": ["role"],
                },
            },
        },
        "required": ["summary", "industry_context", "work"],
    },
}
# The reconciler's shape: one free-form object (`RequestConfirmation.context`).
_OPEN = {
    "name": "r",
    "strict": False,
    "schema": {
        "type": "object",
        "properties": {"context": {"type": "object", "additionalProperties": True}},
    },
}


def _message(*blocks, stop="end_turn") -> Message:
    return Message.model_construct(
        id="msg_1", type="message", role="assistant", model="m",
        content=list(blocks), stop_reason=stop, stop_sequence=None,
        usage=Usage.model_construct(input_tokens=5, output_tokens=5),
    )


def _text(t: str) -> TextBlock:
    return TextBlock.model_construct(type="text", text=t, citations=None)


def _provider(model: str):
    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider

        return AnthropicProvider(api_key="test-key", model=model)


def _bad_request(msg: str) -> anthropic.BadRequestError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(msg, response=httpx.Response(400, request=req), body=None)


def _calls(p):
    return [c.kwargs for c in p._client.messages.create.call_args_list]


# ── structured output (output_config.format) ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_schema_goes_out_as_structured_output_closed_and_without_the_text_block():
    p = _provider("claude-sonnet-5-5")
    p._client.messages.create = AsyncMock(
        return_value=_message(_text('{"summary": "s", "industry_context": null, "work": []}'))
    )
    await p.aparse_json("go", json_schema=_STRICT)
    kw = _calls(p)[0]
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    sch = fmt["schema"]
    assert sch["additionalProperties"] is False
    assert sch["properties"]["work"]["items"]["additionalProperties"] is False
    # a type list is split into anyOf (the SDK transform reads `type` as one name)
    assert sch["properties"]["industry_context"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert kw["messages"] == [{"role": "user", "content": "go"}]


@pytest.mark.asyncio
async def test_a_schema_replaces_the_prefill_on_a_prefill_model():
    # Structured output and a prefill are incompatible; the schema is the stronger one.
    p = _provider("claude-haiku-4-5")
    p._client.messages.create = AsyncMock(
        return_value=_message(_text('{"summary": "s", "industry_context": null, "work": []}'))
    )
    assert (await p.aparse_json("go", json_schema=_STRICT))["summary"] == "s"
    kw = _calls(p)[0]
    assert "format" in kw["output_config"]
    assert kw["messages"] == [{"role": "user", "content": "go"}]


@pytest.mark.asyncio
async def test_the_schema_merges_with_the_thinking_off_effort_never_overwrites_it():
    p = _provider("claude-opus-5-5")
    p._client.messages.create = AsyncMock(
        return_value=_message(_text('{"summary": "s", "industry_context": null, "work": []}'))
    )
    await p.aparse_json("go", json_schema=_STRICT, disable_thinking=True)
    oc = _calls(p)[0]["output_config"]
    assert oc["effort"] == "low"
    assert oc["format"]["type"] == "json_schema"


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", [None, _OPEN], ids=["no-schema", "free-form-object"])
async def test_no_usable_schema_sends_no_output_config_and_the_json_only_block(schema):
    from applire.providers.llm.anthropic import JSON_ONLY_INSTRUCTION

    p = _provider("claude-sonnet-5-5")
    p._client.messages.create = AsyncMock(return_value=_message(_text('{"a": 1}')))
    assert await p.aparse_json("go", json_schema=schema) == {"a": 1}
    kw = _calls(p)[0]
    assert "output_config" not in kw
    assert kw["messages"] == [{"role": "user", "content": [
        {"type": "text", "text": "go"},
        {"type": "text", "text": JSON_ONLY_INSTRUCTION},
    ]}]


@pytest.mark.asyncio
async def test_a_prefill_model_without_a_schema_keeps_the_prefill_and_no_text_block():
    p = _provider("claude-haiku-4-5")
    p._client.messages.create = AsyncMock(return_value=_message(_text('"a": 1}')))
    assert await p.aparse_json("go") == {"a": 1}
    assert _calls(p)[0]["messages"] == [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "{"},
    ]


# ── schema rejection: retry once without, latch ──────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("wording", [
    # the real Sonnet 5.5 400 (2026-11-07 probe): not caught by the wording check
    "output_config.format.schema: Invalid schema: Circular reference detected in schema "
    "definitions: n -> n. Self-referencing or mutually-referencing definitions are not supported.",
    "json_schema is not supported for this model",
], ids=["unworded", "worded"])
async def test_a_rejected_schema_is_retried_without_it_once_and_latched(wording):
    p = _provider("claude-sonnet-5-5")
    p._client.messages.create = AsyncMock(
        side_effect=[_bad_request(wording), _message(_text('{"a": 1}'))]
    )
    assert await p.aparse_json("go", json_schema=_STRICT) == {"a": 1}
    calls = _calls(p)
    assert len(calls) == 2
    assert "format" in calls[0]["output_config"]
    assert "output_config" not in calls[1]
    assert p._json_schema_rejected is True
    # the next call in the process sends no schema at all
    p._client.messages.create = AsyncMock(return_value=_message(_text('{"a": 2}')))
    await p.aparse_json("go", json_schema=_STRICT)
    assert "output_config" not in _calls(p)[0]


@pytest.mark.asyncio
async def test_a_400_that_persists_without_the_schema_surfaces_and_does_not_latch():
    p = _provider("claude-sonnet-5-5")
    p._client.messages.create = AsyncMock(
        side_effect=[_bad_request("prompt is too long"), _bad_request("prompt is too long")]
    )
    with pytest.raises(LLMProviderError):
        await p.aparse_json("go", json_schema=_STRICT)
    assert len(_calls(p)) == 2
    assert p._json_schema_rejected is False


# ── the corrective turn ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_prose_first_reply_gets_one_corrective_turn_with_the_reply_unchanged():
    from applire.providers.llm.anthropic import CORRECTIVE_TURN

    p = _provider("claude-sonnet-5-5")
    thinking = ThinkingBlock.model_construct(type="thinking", thinking="", signature="sig")
    prose = _text('Item 0: The claim says … Supported.\n\n{"items": []}')
    p._client.messages.create = AsyncMock(
        side_effect=[_message(thinking, prose), _message(_text('{"items": []}'))]
    )
    assert await p.aparse_json("judge") == {"items": []}
    calls = _calls(p)
    assert len(calls) == 2
    follow = calls[1]["messages"]
    assert follow[0] == calls[0]["messages"][0]
    assert follow[1]["role"] == "assistant"
    # every block goes back as received — the thinking block with its signature
    assert follow[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert follow[1]["content"][1]["text"].startswith("Item 0:")
    assert follow[2] == {"role": "user", "content": CORRECTIVE_TURN}


@pytest.mark.asyncio
async def test_the_corrective_turn_runs_once_never_loops():
    p = _provider("claude-sonnet-5-5")
    p._client.messages.create = AsyncMock(
        side_effect=[_message(_text("Prose.")), _message(_text("Still prose.")),
                     _message(_text('{"never": "reached"}'))]
    )
    with pytest.raises(LLMResponseParseError):
        await p.aparse_json("go")
    assert len(_calls(p)) == 2


@pytest.mark.asyncio
async def test_a_second_json_value_still_raises_and_is_not_re_asked():
    # R2-13: half an answer is never kept unseen — and never quietly re-asked.
    p = _provider("claude-sonnet-5-5")
    p._client.messages.create = AsyncMock(
        side_effect=[_message(_text('{"a": 1}\n{"b": 2}')), _message(_text('{"a": 1}'))]
    )
    with pytest.raises(LLMResponseParseError) as exc:
        await p.aparse_json("go")
    assert exc.value.second_value is True
    assert len(_calls(p)) == 1


@pytest.mark.asyncio
async def test_a_prefill_model_keeps_its_single_shot_parse():
    p = _provider("claude-haiku-4-5")
    p._client.messages.create = AsyncMock(
        side_effect=[_message(_text("not json")), _message(_text('"a": 1}'))]
    )
    with pytest.raises(LLMResponseParseError):
        await p.aparse_json("go")
    assert len(_calls(p)) == 1


# ── thinking headroom and the truncation net ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("model,disable,expected", [
    ("claude-sonnet-5-5", None, 300 + 4096),     # adaptive by default: thinking shares the budget
    ("claude-sonnet-5-5", True, 300),            # between_tools: thinking off
    ("claude-opus-5-5", True, 300 + 4096),       # cannot turn thinking off (effort low)
    ("claude-fable-5-1", True, 300 + 4096),
    ("claude-haiku-5-5", True, 300),
    ("claude-unknown-9", None, 300 + 4096),      # the current surface
    ("claude-sonnet-4-6", None, 300),            # does not think unless asked
    ("claude-opus-4-8", None, 300),
])
async def test_a_thinking_request_gets_headroom_on_top_of_the_answer_budget(model, disable, expected):
    p = _provider(model)
    p._client.messages.create = AsyncMock(return_value=_message(_text('{"a": 1}')))
    await p.aparse_json("go", max_tokens=300, disable_thinking=disable)
    assert _calls(p)[0]["max_tokens"] == expected
    p._client.messages.create = AsyncMock(return_value=_message(_text("hi")))
    await p.acomplete("go", max_tokens=300, disable_thinking=disable)
    assert _calls(p)[0]["max_tokens"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["aparse_json", "acomplete"])
async def test_a_truncated_call_is_retried_once_with_a_doubled_budget(method):
    p = _provider("claude-sonnet-5-5")
    p._client.messages.create = AsyncMock(side_effect=[
        _message(ThinkingBlock.model_construct(type="thinking", thinking="", signature="s"),
                 stop="max_tokens"),
        _message(_text('{"a": 1}')),
    ])
    await getattr(p, method)("go", max_tokens=300)
    assert [c["max_tokens"] for c in _calls(p)] == [300 + 4096, 600 + 4096]


@pytest.mark.asyncio
async def test_a_second_truncation_still_raises():
    p = _provider("claude-sonnet-5-5")
    p._client.messages.create = AsyncMock(return_value=_message(_text("{"), stop="max_tokens"))
    with pytest.raises(LLMTruncatedError):
        await p.aparse_json("go", max_tokens=300)
    assert len(_calls(p)) == 2
