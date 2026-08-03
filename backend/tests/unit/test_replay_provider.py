# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-073 — the replay provider's own contract.

These are gate tests, not pins: each asserts a property the tier is *relied on*
for, and each would fail if the property were removed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from applire.providers.llm.debug_log import set_review_call_meta, set_stage
from applire.providers.llm.replay import (
    ReplayExhausted,
    ReplayLLMProvider,
    ReplayMiss,
    load_slice,
)

SLICE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "replay"
    / "cover_letter_review_loop.jsonl"
)


@pytest.fixture
def records() -> list[dict]:
    return load_slice(SLICE)


@pytest.fixture(autouse=True)
def _clean_call_site():
    yield
    set_stage("")
    set_review_call_meta(None, None)


@pytest.mark.asyncio
async def test_it_serves_the_recorded_response_for_the_current_seam(records):
    """Selection is by (stage, review_role) — the caller's own labelling."""
    provider = ReplayLLMProvider(records)

    set_stage("cover_letter")
    set_review_call_meta("reviewer", 1)
    first = await provider.aparse_json("any prompt at all")

    assert "approved" in first, "did not get the reviewer's recorded verdict"
    assert provider.served == [("cover_letter", "reviewer")]


@pytest.mark.asyncio
async def test_the_prompt_text_has_no_influence_on_selection(records):
    """The #362 property, asserted directly.

    MockLLMProvider fingerprints its caller by substring-matching the system
    prompt, which is ambiguous — both real extraction prompts open with the same
    sentence. Replay must not have a fingerprint to collide, so two calls at the
    same seam with wildly different prompts must both be served, in order.
    """
    provider = ReplayLLMProvider(records)
    set_stage("cover_letter")
    set_review_call_meta("generator", 1)

    a = await provider.aparse_json("You are an expert CV analyst specialised in the DACH…")
    set_review_call_meta("generator", 2)
    b = await provider.aparse_json("kkk", system="totally unrelated system prompt")

    assert a["header"]["name"] == b["header"]["name"]
    assert provider.served == [
        ("cover_letter", "generator"),
        ("cover_letter", "generator"),
    ]


@pytest.mark.asyncio
async def test_an_unrecorded_seam_fails_closed(records):
    """A replay provider that fell back to a generic response would recreate the
    exact defect this tier exists to remove — a green test that never reached the
    code. So a miss must raise, loudly, naming the seams it does have."""
    provider = ReplayLLMProvider(records)
    set_stage("interview_question")
    set_review_call_meta(None, None)

    with pytest.raises(ReplayMiss) as excinfo:
        await provider.aparse_json("anything")
    assert "interview_question" in str(excinfo.value)
    assert "cover_letter" in str(excinfo.value), "the error must name the recorded seams"


@pytest.mark.asyncio
async def test_more_calls_than_the_capture_made_is_an_error_not_a_repeat(records):
    """Replaying the last response forever would let a runaway loop look healthy.
    Exhaustion is a behaviour change and must surface as one."""
    provider = ReplayLLMProvider(records)
    set_stage("cover_letter")
    set_review_call_meta("reviewer", 1)

    await provider.aparse_json("1")
    await provider.aparse_json("2")  # both recorded reviewer turns consumed

    with pytest.raises(ReplayExhausted):
        await provider.aparse_json("3")


@pytest.mark.asyncio
async def test_assert_fully_consumed_catches_a_branch_that_stopped_running(records):
    """The reachability half. A leftover recording means the code under test made
    FEWER calls than production did — usually a branch that silently stopped
    firing, which is failure mode 5 and is invisible to an output assertion."""
    provider = ReplayLLMProvider(records)
    set_stage("cover_letter")
    set_review_call_meta("reviewer", 1)
    await provider.aparse_json("only one of the two reviewer turns")

    with pytest.raises(AssertionError) as excinfo:
        provider.assert_fully_consumed()
    assert "never replayed" in str(excinfo.value)


def test_a_missing_slice_raises_rather_than_skipping():
    """A fixture-gated skip is how 9 tests in this repo stopped being tests. The
    loader must treat a missing slice as a broken tier, not an inapplicable test."""
    with pytest.raises(FileNotFoundError):
        load_slice(SLICE.parent / "definitely_not_here.jsonl")
