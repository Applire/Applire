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
