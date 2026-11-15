# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""R2-13 (P-1): one tolerant JSON parse for every provider (ADR-009 amended 2026-10-24).

The founder's reconcile reply (Requesty, ``gpt-6-luna``, 2026-10-23) carried text
after the JSON object; ``requesty.py``'s strict ``json.loads`` raised ``Extra
data: line 1 column 915`` and the interview answer was lost. These tests pin:

* the helper's rules (first JSON value; a non-JSON tail is discarded; a second
  JSON value or no JSON value raises ``LLMResponseParseError``);
* ONE seam test per provider ``aparse_json`` (Requesty, OpenRouter, OpenAI,
  Anthropic, Ollama, Mistral, Replay) driving the provider's real ``aparse_json``
  body with the founder's reply shape — reverting any one provider to
  ``json.loads`` fails its own test;
* the debug log keeps the raw reply on a parse failure (was ``response: None``)
  and the discarded tail on success;
* the always-on INFO line carries no reply text (PII boundary);
* ``LLMResponseParseError`` still reaches the existing ``except
  json.JSONDecodeError`` handlers (``prompts/preamble.py`` reads ``exc.doc``).
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from applire.config import settings
from applire.exceptions import LLMError, LLMResponseParseError
from applire.providers.llm.json_reply import (
    parse_json_reply,
    take_discarded_prose,
    take_discarded_tail,
)

# The founder's shape, synthetic content: an object, then prose.
_OBJ = {"ops": [{"op": "add_bullet", "text": "Led the Kafka migration"}], "empty_reason": None}
_TRAILING = json.dumps(_OBJ) + "\n\nNote: I merged the new role as an alias of the current entry."
_SECOND_OBJECT = json.dumps(_OBJ) + '\n{"ops": []}'


# --------------------------------------------------------------------------- #
# the helper's rules
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "reply",
    [
        json.dumps(_OBJ),
        "  \n" + json.dumps(_OBJ) + "\n  ",
        _TRAILING,
        "```json\n" + json.dumps(_OBJ) + "\n```",
        "```json\n" + json.dumps(_OBJ) + "\n```\nHope this helps!",
        "```\n" + json.dumps(_OBJ) + "\n```",
        json.dumps(_OBJ) + " Note: {not json either",
        json.dumps(_OBJ) + "\n(I used [1] the CV and {2} the answer.)",
    ],
    ids=[
        "plain", "whitespace", "trailing-prose", "fenced", "fenced+prose",
        "bare-fence", "tail-with-broken-brace", "tail-prose-with-brackets",
    ],
)
def test_the_first_json_value_is_read_and_a_non_json_tail_is_discarded(reply):
    assert parse_json_reply(reply, provider="T") == _OBJ


@pytest.mark.parametrize(
    "reply",
    [
        _SECOND_OBJECT,
        json.dumps(_OBJ) + json.dumps(_OBJ),
        "```json\n" + json.dumps(_OBJ) + "\n```\n```json\n{\"ops\": []}\n```",
        json.dumps(_OBJ) + "\n[1, 2]",
        json.dumps(_OBJ) + "\n42",
        json.dumps(_OBJ) + '\n"and a string"',
    ],
    ids=["second-object", "glued-objects", "two-fenced-objects", "second-array", "scalar-tail", "string-tail"],
)
def test_a_second_json_value_after_the_first_is_a_failure(reply):
    with pytest.raises(LLMResponseParseError) as info:
        parse_json_reply(reply, provider="T")
    assert "second JSON value" in info.value.msg
    assert info.value.doc == reply


@pytest.mark.parametrize(
    "reply",
    ["", "   ", None, '{"ops": [', "```json\n```", "Sure, here is my answer.",
     "Items [0] and [1] are supported; see {placeholder}.",
     'We need JSON.\n{"ops": [{"op": "add_bullet", "text": "Led',
     'Draft: [{"op": "add"',
     ],
    ids=["empty", "blank", "none", "broken", "empty-fence", "prose-only",
         "prose-with-brackets-only", "prose-then-broken-object", "prose-then-broken-array"],
)
def test_no_json_value_is_a_failure(reply):
    with pytest.raises(LLMResponseParseError) as info:
        parse_json_reply(reply, provider="T")
    assert info.value.second_value is False


# --------------------------------------------------------------------------- #
# B8-15 (founder 2026-11-13): leading prose is skipped, the ONE value read
# --------------------------------------------------------------------------- #

# The captured shapes (synthetic content): Luna thinking-off leaks its reasoning
# ("We need …") before the object; Sonnet 5.5 judges each item in prose first,
# citing evidence as [0]; a fenced block after an introduction.
_LUNA = 'We need output JSON with analysis first. Issues? none.\n{\n  "approved": true,\n  "issues": []\n}'
_SONNET = 'Item 0: The claim says Python. Evidence [0] supports it.\n\n{"items": [{"id": 0, "supported": true}]}'


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("Sure! " + json.dumps(_OBJ), _OBJ),
        (_LUNA, {"approved": True, "issues": []}),
        (_SONNET, {"items": [{"id": 0, "supported": True}]}),
        ("Here it is:\n```json\n" + json.dumps(_OBJ) + "\n```", _OBJ),
        ("Here it is:\n```json\n" + json.dumps(_OBJ) + "\n```\nHope this helps.", _OBJ),
        ("See {placeholder} and [2].\n" + json.dumps(_OBJ), _OBJ),
        ('The list:\n[{"a": 1}, {"b": 2}]', [{"a": 1}, {"b": 2}]),
    ],
    ids=["sure", "luna-reasoning", "sonnet-items", "fenced-after-intro", "fenced-intro-outro",
         "prose-brackets-then-object", "array-of-objects"],
)
def test_leading_prose_is_skipped_and_the_one_value_is_read(reply, expected):
    take_discarded_prose()
    assert parse_json_reply(reply, provider="T") == expected
    assert take_discarded_prose()  # the prose text goes to the debug log


@pytest.mark.parametrize(
    "reply",
    [
        'Option A: {"ops": []}\nOption B: {"ops": [{"op": "add"}]}',
        'Analysis first.\n{"analysis": "x"}{"approved": true}',
        'Example {"ops": []} then the answer:\n' + json.dumps(_OBJ),
        'We need JSON.\n' + json.dumps(_OBJ) + '\nAlternatively: {"ops": []}',
    ],
    ids=["two-options", "split-analysis", "example-then-answer", "answer-then-alternative"],
)
def test_two_values_after_prose_still_raise(reply):
    # "no guessing which is the answer" (B8-15)
    with pytest.raises(LLMResponseParseError) as info:
        parse_json_reply(reply, provider="T")
    assert info.value.second_value is True


def test_a_broken_answer_after_prose_is_never_read_for_an_object_inside_it():
    # the outer object breaks; `{"op": "add_bullet", …}` inside it decodes — not read
    reply = 'We need JSON.\n{"ops": [{"op": "add_bullet", "text": "Led"}], "empty_reason": '
    with pytest.raises(LLMResponseParseError) as info:
        parse_json_reply(reply, provider="T")
    assert "not valid JSON" in info.value.msg


def test_a_reply_that_opens_with_json_never_takes_the_prose_path():
    # R2-13 unchanged: the first value is read, a prose tail with an object in it
    # is a tail (discarded), not a second candidate.
    reply = json.dumps(_OBJ) + '\nNote: an empty answer would be {"ops": []} here.'
    take_discarded_prose()
    assert parse_json_reply(reply, provider="T") == _OBJ
    assert take_discarded_prose() is None


def test_the_prose_info_line_carries_its_length_never_its_text():
    log = logging.getLogger("applire.providers.llm.json_reply")
    cap, old_level = _Capture(), log.level
    log.addHandler(cap)
    log.setLevel(logging.INFO)
    try:
        take_discarded_prose()
        parse_json_reply(_LUNA, provider="Requesty", model="m")
    finally:
        log.removeHandler(cap)
        log.setLevel(old_level)
    lines = [m for m in cap.lines if "LLM_JSON_PROSE_SKIPPED" in m]
    assert len(lines) == 1
    assert "provider=Requesty" in lines[0] and "prose_chars=" in lines[0]
    assert "analysis" not in lines[0] and "need" not in lines[0]  # PII boundary
    assert take_discarded_prose().startswith("We need output JSON")


def test_an_array_reply_is_still_read():
    # gap clustering's mock answers a list; a real provider may too.
    assert parse_json_reply('[{"a": 1}]\nDone.', provider="T") == [{"a": 1}]


def test_the_error_is_also_a_json_decode_error_carrying_the_reply():
    with pytest.raises(json.JSONDecodeError) as info:  # the six legacy handlers
        parse_json_reply(_SECOND_OBJECT, provider="T")
    exc = info.value
    assert isinstance(exc, LLMError) and isinstance(exc, LLMResponseParseError)
    assert exc.doc == _SECOND_OBJECT
    assert "Kafka" not in str(exc)  # str() carries no reply content


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_the_info_line_names_the_discard_without_its_text():
    # A handler on the module logger itself: another test may stop the
    # `applire` logger propagating to the root (caplog's handler).
    log = logging.getLogger("applire.providers.llm.json_reply")
    cap, old_level = _Capture(), log.level
    log.addHandler(cap)
    log.setLevel(logging.INFO)
    try:
        take_discarded_tail()
        parse_json_reply(_TRAILING, provider="Requesty", model="m")
    finally:
        log.removeHandler(cap)
        log.setLevel(old_level)
    lines = [m for m in cap.lines if "LLM_JSON_TAIL_DISCARDED" in m]
    assert len(lines) == 1
    assert "provider=Requesty" in lines[0] and "tail_kind=prose" in lines[0]
    assert "alias" not in lines[0] and "merged" not in lines[0]  # PII boundary
    assert "merged the new role" in take_discarded_tail()  # the text goes to the debug log only


def test_a_fence_only_tail_is_not_reported():
    take_discarded_tail()
    parse_json_reply("```json\n" + json.dumps(_OBJ) + "\n```", provider="T")
    assert take_discarded_tail() is None


# --------------------------------------------------------------------------- #
# one seam test per provider aparse_json — the real body, a faked transport
# --------------------------------------------------------------------------- #

def _requesty(monkeypatch, reply):
    monkeypatch.setattr(settings, "requesty_model", "m")
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.requesty import RequestyProvider
        p = RequestyProvider(api_key="test-key")
    p._parse_json = AsyncMock(return_value=reply)
    return p


def _openrouter(monkeypatch, reply):
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openrouter import OpenRouterProvider
        p = OpenRouterProvider(api_key="test-key", model="m")
    p._parse_json = AsyncMock(return_value=reply)
    return p


def _openai(monkeypatch, reply):
    with patch("openai.AsyncOpenAI"):
        from applire.providers.llm.openai import OpenAIProvider
        p = OpenAIProvider(api_key="test-key")
    p._parse_json = AsyncMock(return_value=reply)
    return p


def _anthropic(monkeypatch, reply):
    # A model that still takes the assistant prefill (Claude Haiku 4.5).
    monkeypatch.setattr(settings, "anthropic_model", "claude-haiku-4-5")
    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider
        p = AnthropicProvider(api_key="test-key")
    # The model continues the prefilled "{": hand it the reply minus its "{".
    assert reply.startswith("{")
    p._create = AsyncMock(return_value=reply[1:])
    return p


def _anthropic_current(monkeypatch, reply):
    # A current model rejects a prefill (WP-0): the whole reply comes back.
    monkeypatch.setattr(settings, "anthropic_model", "claude-sonnet-5-5")
    with patch("anthropic.AsyncAnthropic"):
        from applire.providers.llm.anthropic import AnthropicProvider
        p = AnthropicProvider(api_key="test-key")
    p._create = AsyncMock(return_value=reply)
    return p


def _ollama(monkeypatch, reply):
    from applire.providers.llm.ollama import OllamaProvider
    p = OllamaProvider(base_url="http://localhost:11434", model="llama3.2")
    p._parse_json = AsyncMock(return_value=reply)
    return p


def _mistral(monkeypatch, reply):
    from applire.providers.llm.mistral import MistralProvider
    p = MistralProvider(api_key="test-key", model="m")
    p._parse_json = AsyncMock(return_value=reply)
    return p


def _replay(monkeypatch, reply):
    from applire.providers.llm.replay import ReplayLLMProvider
    return ReplayLLMProvider([{"stage": "", "response": reply}])


_PROVIDERS = {
    "requesty": _requesty,
    "openrouter": _openrouter,
    "openai": _openai,
    "anthropic": _anthropic,
    "anthropic_current": _anthropic_current,
    "ollama": _ollama,
    "mistral": _mistral,
    "replay": _replay,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(_PROVIDERS))
async def test_seam_each_provider_reads_a_reply_with_trailing_text(monkeypatch, name):
    provider = _PROVIDERS[name](monkeypatch, _TRAILING)
    assert await provider.aparse_json("reconcile") == _OBJ


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(_PROVIDERS))
async def test_seam_each_provider_refuses_a_second_json_object(monkeypatch, name):
    provider = _PROVIDERS[name](monkeypatch, _SECOND_OBJECT)
    with pytest.raises(LLMResponseParseError):
        await provider.aparse_json("reconcile")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(set(_PROVIDERS) - {"anthropic"}))
async def test_seam_each_provider_reads_a_prose_first_reply_in_one_call(monkeypatch, name):
    # B8-15: every provider reaches the one parse, so leading prose is skipped on
    # each, with no second call. ("anthropic" is the prefill shape: its reply is
    # "{" + text and is never prose-first.)
    provider = _PROVIDERS[name](monkeypatch, _LUNA)
    assert await provider.aparse_json("judge") == {"approved": True, "issues": []}
    transport = getattr(provider, "_create", None) if name.startswith("anthropic") else getattr(
        provider, "_parse_json", None)
    if isinstance(transport, AsyncMock):
        assert transport.await_count == 1


# --------------------------------------------------------------------------- #
# the debug log keeps the raw reply
# --------------------------------------------------------------------------- #

def _records(tmp_path):
    files = list(tmp_path.glob("*.jsonl"))
    assert files, "no debug log file written"
    return [json.loads(line) for line in files[0].read_text().splitlines()]


@pytest.mark.asyncio
async def test_the_debug_log_records_the_raw_reply_on_a_parse_failure(monkeypatch, tmp_path):
    from applire.providers.llm.debug_log import llm_log_stage, wrap_provider

    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path))
    provider = wrap_provider(_requesty(monkeypatch, _SECOND_OBJECT))
    with llm_log_stage("reconcile"), pytest.raises(LLMResponseParseError):
        await provider.aparse_json("reconcile")
    (rec,) = _records(tmp_path)
    assert rec["ok"] is False and rec["stage"] == "reconcile"
    assert rec["error"].startswith("LLMResponseParseError")
    assert rec["response"] == _SECOND_OBJECT  # was None before build 4


@pytest.mark.asyncio
async def test_the_debug_log_records_a_discarded_tail_on_success(monkeypatch, tmp_path):
    from applire.providers.llm.debug_log import wrap_provider

    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path))
    provider = wrap_provider(_requesty(monkeypatch, _TRAILING))
    assert await provider.aparse_json("reconcile") == _OBJ
    clean = wrap_provider(_requesty(monkeypatch, json.dumps(_OBJ)))
    assert await clean.aparse_json("reconcile") == _OBJ
    first, second = _records(tmp_path)
    assert first["ok"] is True and first["response"] == _OBJ
    assert "merged the new role" in first["discarded_tail"]
    assert second["discarded_tail"] is None  # never inherited from the previous call


@pytest.mark.asyncio
async def test_the_debug_log_records_the_skipped_prose_on_success(monkeypatch, tmp_path):
    from applire.providers.llm.debug_log import wrap_provider

    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path))
    assert await wrap_provider(_requesty(monkeypatch, _LUNA)).aparse_json("x") == {
        "approved": True, "issues": []}
    assert await wrap_provider(_requesty(monkeypatch, json.dumps(_OBJ))).aparse_json("x") == _OBJ
    first, second = _records(tmp_path)
    assert first["discarded_prose"].startswith("We need output JSON")
    assert second["discarded_prose"] is None  # never inherited from the previous call


@pytest.mark.asyncio
async def test_a_replayed_failure_record_fails_the_same_way(monkeypatch, tmp_path):
    """The record the debug log writes on a failure replays as that failure."""
    from applire.providers.llm.debug_log import wrap_provider
    from applire.providers.llm.replay import ReplayLLMProvider

    monkeypatch.setattr(settings, "llm_debug_log", True)
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path))
    with pytest.raises(LLMResponseParseError):
        await wrap_provider(_requesty(monkeypatch, _SECOND_OBJECT)).aparse_json("x")
    (rec,) = _records(tmp_path)
    monkeypatch.setattr(settings, "llm_debug_log", False)
    with pytest.raises(LLMResponseParseError):
        await ReplayLLMProvider([rec]).aparse_json("x")


# --------------------------------------------------------------------------- #
# the legacy `except json.JSONDecodeError` handlers still catch it
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_preamble_analysis_retry_still_catches_the_parse_error(monkeypatch):
    """`prompts/preamble.aparse_json` arm c catches `json.JSONDecodeError` and
    reads `exc.doc`: a second-object reply must reach that retry, not escape it."""
    from applire.prompts import preamble

    calls = []

    class _P:
        async def aparse_json(self, prompt, **kwargs):
            calls.append(prompt)
            if len(calls) == 1:
                raise LLMResponseParseError("T reply holds a second JSON value", _SECOND_OBJECT, 10)
            return {"ok": True}

    site = next(s for s, cfg in preamble.SITES.items() if cfg.analysis)
    monkeypatch.setattr(preamble, "site_arm", lambda _site: "c")
    monkeypatch.setattr(preamble, "repair_broken_analysis", lambda _doc: None)
    assert await preamble.aparse_json(_P(), site, "prompt") == {"ok": True}
    assert len(calls) == 2  # the retry without the analysis field ran
