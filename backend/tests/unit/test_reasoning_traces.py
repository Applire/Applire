# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Founder ruling M-4 — only the answer is consumed, never the thinking.

One fixture per trace shape that exists in the wild, plus the empty-answer
control. The shapes are not invented: each names the provider whose response
carries it (`providers/llm/reasoning.py`'s table).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from applire.exceptions import LLMTruncatedError
from applire.providers.llm.reasoning import (
    clean_completion,
    extract_reasoning_tokens,
    finalise_completion,
    split_reasoning,
    take_trace,
)

_JSON = '{"ops": [], "denials": ["insulin manufacturing"]}'


# ── the strip ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "shape,raw",
    [
        # Inline paired span in front of the answer — the shape that breaks
        # json.loads on OpenAI-compatible gateways.
        ("paired", f"<think>The answer names three employers.</think>\n{_JSON}"),
        # Same, with the vendor's other tag names.
        ("thinking", f"<thinking>Let me check the vault.</thinking>{_JSON}"),
        ("reasoning", f"<reasoning>Rule 9 applies to the denial only.</reasoning>{_JSON}"),
        # Whitespace and case drift inside the tag.
        ("sloppy", f"< THINK >hmm< / think >\n\n{_JSON}"),
        # Trace AFTER the answer (some models append a justification).
        ("trailing", f"{_JSON}<think>I chose add_bullets because…</think>"),
        # Bare closing tag, no opener — the model marked only where it ended.
        ("orphan-close", f"first I consider the denial…</think>\n{_JSON}"),
    ],
)
def test_the_answer_survives_every_inline_trace_shape(shape, raw):
    answer, trace = split_reasoning(raw)
    assert json.loads(answer) == {"ops": [], "denials": ["insulin manufacturing"]}, shape
    assert trace, f"{shape}: the trace was dropped without being captured"


def test_an_unclosed_opener_takes_everything_after_it():
    """The budget ran out mid-trace: the answer is what came BEFORE the tag."""
    answer, trace = split_reasoning(f"{_JSON}\n<think>and now I ran out of bud")
    assert json.loads(answer)["ops"] == []
    assert trace.startswith("<think>")


def test_a_complete_span_and_a_later_truncated_one_both_go():
    answer, trace = split_reasoning(
        f"<think>first pass</think>{_JSON}<thinking>second pass, cut off"
    )
    assert json.loads(answer)["ops"] == []
    assert "first pass" in trace and "second pass" in trace


def test_a_normal_answer_is_returned_byte_identical():
    """No tag, no change — the strip may not touch an ordinary completion."""
    text = 'Sehr geehrte Damen und Herren,\n\nich denke, <b>das</b> passt.'
    answer, trace = split_reasoning(text)
    assert answer == text.strip()
    assert trace == ""


def test_the_word_think_in_prose_is_not_a_tag():
    """A candidate writing about thinking must not lose their sentence."""
    text = "I think in systems, and I documented that thinking for the team."
    answer, trace = split_reasoning(text)
    assert answer == text
    assert trace == ""


def test_a_non_string_completion_passes_through_untouched():
    assert clean_completion(None) is None  # type: ignore[arg-type]


def test_the_stripped_trace_reaches_the_debug_log_channel_once():
    take_trace()  # clear anything a previous test left
    clean_completion(f"<think>weighing rule 9</think>{_JSON}", model="m", method="x")
    assert take_trace() == "<think>weighing rule 9</think>"
    assert take_trace() is None, "a trace may only be consumed once"


def test_a_trace_is_never_returned_to_the_caller():
    """The load-bearing property: no code path can read the trace as the answer."""
    answer = clean_completion(f"<think>secret chain of thought</think>{_JSON}")
    assert "secret chain of thought" not in answer


# ── adversarial pass 2026-09-10 — a tag-shaped literal INSIDE the JSON ────────
# The three patterns above are not JSON-string-aware: they match a tag-like
# substring wherever it sits. A candidate's own words can legitimately contain
# a literal "<think>...</think>" span (a workshop titled "Design Thinking", a
# quip about "stop </think>ing"), and the reconciler paraphrases it straight
# into a bullet's own text. Each shape below is a COMPLETE, VALID JSON
# completion before the strip runs (asserted) — the strip must not be the
# reason it stops being one.


def test_a_paired_tag_inside_a_json_string_is_not_eaten():
    """The bullet keeps its own words — a wrapping trace and a JSON-internal
    tag-shaped substring are not the same shape, and only the first is trace.

    MUTATION KILL: drop the `_inside_json_string` guard in `_keep` and the
    bullet's "Design Thinking" is silently deleted while the JSON still
    parses — the single worst shape, because nothing signals the loss.
    """
    raw = json.dumps({
        "ops": [{
            "op": "add_bullets", "target": "w1",
            "responsibilities": [
                "Led a workshop called <think>Design Thinking</think> for the product team"
            ],
            "achievements": [], "technologies": [],
        }],
        "ambiguities": [], "denials": [],
    })
    json.loads(raw)  # the fixture itself is valid JSON before any stripping
    answer, trace = split_reasoning(raw)
    assert answer == raw, "a JSON-internal tag-shaped substring must survive untouched"
    assert trace == ""
    parsed = json.loads(answer)
    assert "Design Thinking" in parsed["ops"][0]["responsibilities"][0]


def test_an_unclosed_looking_tag_inside_a_json_string_does_not_truncate_the_payload():
    """`<think>` with no close, inside a string value, must not read as "the
    budget ran out" and delete everything after it — that is the WHOLE rest
    of the JSON batch, not a trace.

    MUTATION KILL: drop the `_inside_json_string` guard on the UNCLOSED branch
    and `answer` becomes an unterminated JSON string (`json.loads` raises),
    which is exactly the silent-loss failure M-4 exists to close, caused by
    the fix itself.
    """
    raw = json.dumps({
        "ops": [{
            "op": "add_bullets", "target": "w1",
            "responsibilities": [
                "Wrote a blog post about <think>ing outside the box for engineers"
            ],
            "achievements": [], "technologies": [],
        }],
        "ambiguities": [], "denials": [],
    })
    json.loads(raw)
    answer, trace = split_reasoning(raw)
    assert answer == raw
    assert trace == ""
    json.loads(answer)  # must still parse


def test_an_orphan_close_looking_tag_inside_a_json_string_does_not_eat_the_prefix():
    """A bare `</think>`-shaped substring deep inside a bullet must not read
    as "the trace ended here" and delete the entire JSON payload before it.

    MUTATION KILL: drop the `_inside_json_string` guard on the ORPHAN_CLOSE
    branch and `answer` is left starting mid-string with no opening brace —
    `json.loads` raises "Expecting value".
    """
    raw = json.dumps({
        "ops": [{
            "op": "add_bullets", "target": "w1",
            "responsibilities": [
                "Coined the motto 'Stop </think>ing, start doing' for the sprint retro"
            ],
            "achievements": [], "technologies": [],
        }],
        "ambiguities": [], "denials": [],
    })
    json.loads(raw)
    answer, trace = split_reasoning(raw)
    assert answer == raw
    assert trace == ""
    json.loads(answer)


# ── the token split ───────────────────────────────────────────────────────────


def _openai_shaped(prompt: int, completion: int, reasoning: int | None):
    details = (
        SimpleNamespace(reasoning_tokens=reasoning) if reasoning is not None else None
    )
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            completion_tokens_details=details,
        )
    )


def test_a_bare_trace_with_an_odd_number_of_quotes_before_the_payload_is_still_stripped():
    """The JSON-string guard must not read a trace's own quotes as JSON.

    A bare (orphan-close) trace precedes the payload and can quote anything —
    here an odd number of `"` characters. Parity counted from the start of the
    completion would call the closing tag "inside a string", keep the trace,
    and hand `json.loads` prose. Parity counted from the payload's first
    bracket cannot: nothing before it is JSON.

    MUTATION KILL: count from index 0 instead of the first `{`/`[` and this
    fails with the trace still in front of the payload.
    """
    trace = 'the candidate said "no" to insulin, and "15 years' + " at three places…</think>\n"
    raw = trace + _JSON
    answer, got_trace = split_reasoning(raw)
    assert answer == _JSON
    assert got_trace.startswith("the candidate said")
    assert json.loads(answer)


def test_a_leading_paired_trace_with_an_odd_number_of_quotes_is_still_stripped():
    raw = '<think>one " stray quote</think>' + _JSON
    answer, got_trace = split_reasoning(raw)
    assert answer == _JSON
    assert "stray quote" in got_trace


def test_openrouter_and_requesty_report_the_split():
    assert extract_reasoning_tokens(_openai_shaped(1000, 4000, 3200)) == 3200


def test_a_dict_shaped_response_reports_the_split_too():
    body = {"usage": {"completion_tokens": 900, "completion_tokens_details": {"reasoning_tokens": 700}}}
    assert extract_reasoning_tokens(body) == 700


@pytest.mark.parametrize(
    "response",
    [
        # Anthropic — thinking is billed inside output_tokens, no split field.
        SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=20)),
        # Ollama — no reasoning accounting at all.
        {"prompt_eval_count": 10, "eval_count": 20},
        # A provider that reports no usage.
        SimpleNamespace(usage=None),
        None,
    ],
)
def test_an_unreported_split_is_unknown_not_zero_reasoning(response):
    """None means "the provider did not say", which the row stores as 0.

    The distinction matters: a fabricated 0 would read as "this model did no
    thinking", which is a claim the response does not support.
    """
    assert extract_reasoning_tokens(response) is None


def test_reasoning_tokens_are_a_subset_of_completion_tokens():
    """Never added to the total — that would double-count the invoice."""
    from applire.providers.llm.usage import extract_usage

    usage = extract_usage(_openai_shaped(1000, 4000, 3200))
    assert usage is not None
    assert usage.reasoning_tokens == 3200
    assert usage.completion_tokens == 4000
    assert usage.total_tokens == 5000


# ── the empty-answer control ──────────────────────────────────────────────────


def test_a_billed_call_that_returns_no_answer_raises_instead_of_going_silent():
    """§3.7 of the taxonomy: 32,768 completion tokens, `ops: []`, no error."""
    response = _openai_shaped(6000, 32768, 32768)
    with pytest.raises(LLMTruncatedError) as excinfo:
        finalise_completion(response, "", model="deepseek/deepseek-v4-flash-0731",
                            method="aparse_json")
    assert "32768" in str(excinfo.value)


def test_a_trace_only_completion_raises_too():
    """The whole content was the trace: after stripping there is no answer."""
    response = _openai_shaped(6000, 9000, 8800)
    with pytest.raises(LLMTruncatedError):
        finalise_completion(response, "<think>still weighing it up</think>",
                            model="m", method="aparse_json")


def test_an_empty_completion_that_cost_nothing_is_left_alone():
    """A zero-token empty response is an upstream hiccup, not budget exhaustion —
    `raise_if_no_completion` owns that shape and must keep owning it."""
    response = _openai_shaped(6000, 0, None)
    assert finalise_completion(response, "", model="m", method="acomplete") == ""


def test_a_real_answer_is_returned_even_when_reasoning_was_expensive():
    response = _openai_shaped(6000, 32000, 31000)
    out = finalise_completion(
        response, f"<think>a very long trace</think>{_JSON}", model="m", method="aparse_json"
    )
    assert json.loads(out)["ops"] == []


# ── the mandatory-reasoning latch (M-4) ──────────────────────────────────────


class _FakeBadRequest(Exception):
    """Shaped like `openai.BadRequestError` for `_is_reasoning_mandatory_error`."""

    def __init__(self) -> None:
        super().__init__(
            "Error code: 400 - Reasoning is mandatory for this endpoint and "
            "cannot be disabled."
        )


@pytest.mark.asyncio
async def test_a_mandatory_reasoning_model_is_asked_to_disable_exactly_once():
    """Measured 2026-09-09: `z-ai/glm-5.3-flash` rejects `reasoning:{enabled:false}`
    with a 400, so an operator running `OPENROUTER_DISABLE_THINKING=true` paid a
    wasted round-trip on EVERY call. Requesty has latched this since #181.

    MUTATION KILL: remove `self._reasoning_rejected = True` in `_create`'s
    except-branch (or the pre-emptive strip at its top) and this test fails with
    4 attempts instead of 3.
    """
    import openai as openai_sdk

    from applire.providers.llm.openrouter import OpenRouterProvider

    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._model = "z-ai/glm-5.3-flash"
    provider._reasoning_effort = ""
    provider._reasoning_rejected = False

    attempts: list[dict | None] = []

    class _Client:
        class chat:  # noqa: N801 - mirrors the SDK's attribute shape
            class completions:
                @staticmethod
                async def create(**kwargs):
                    attempts.append(kwargs.get("extra_body"))
                    body = kwargs.get("extra_body") or {}
                    if body.get("reasoning", {}).get("enabled") is False:
                        raise _err()
                    return "ok"

    def _err():
        exc = openai_sdk.BadRequestError.__new__(openai_sdk.BadRequestError)
        Exception.__init__(
            exc,
            "Error code: 400 - Reasoning is mandatory for this endpoint and "
            "cannot be disabled.",
        )
        return exc

    provider._client = _Client()

    disable = {"reasoning": {"enabled": False}}
    for _ in range(2):
        assert await provider._create(max_tokens=1024, extra_body=dict(disable)) == "ok"

    # Call 1: the doomed request + the fallback. Call 2: the fallback only.
    assert attempts == [disable, None, None], attempts
