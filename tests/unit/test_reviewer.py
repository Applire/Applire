"""
Unit tests for services/reviewer.py — review_and_refine() retry loop.

No Docker, no DB, no real LLM.

Run:
    pytest tests/unit/test_reviewer.py -v
"""
import sys
import logging
from pathlib import Path
from unittest.mock import AsyncMock, call

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.reviewer import review_and_refine
from applire.constants import REVIEW_VERDICT_MAX_TOKENS
from applire.exceptions import LLMTruncatedError, LLMTimeoutError


@pytest.fixture
def mock_provider():
    provider = AsyncMock()
    return provider


# ---------------------------------------------------------------------------
# max_retries=0 — disabled path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_retries_zero_returns_draft_immediately(mock_provider):
    """When max_retries=0 the review layer is disabled — no LLM calls at all."""
    draft = {"work_history": [{"company": "Acme"}]}
    result = await review_and_refine(
        source="Acme Software Developer 2020-2022",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry prompt",
        generator_system="gen system",
        reviewer_prompt_fn=lambda s, d: "review prompt",
        reviewer_system="rev system",
        provider=mock_provider,
        max_retries=0,
    )
    assert result is draft
    mock_provider.aparse_json.assert_not_called()


# ---------------------------------------------------------------------------
# Approves on first reviewer call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approves_on_first_pass_returns_draft_unchanged(mock_provider):
    """If reviewer approves immediately, draft is returned as-is."""
    draft = {"work_history": [{"company": "Acme", "role": "Dev"}]}
    mock_provider.aparse_json.return_value = {
        "approved": True,
        "issues": [],
        "feedback": "",
    }

    result = await review_and_refine(
        source="Acme Dev 2020-2022",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: f"retry: {f}",
        generator_system="gen system",
        reviewer_prompt_fn=lambda s, d: "review prompt",
        reviewer_system="rev system",
        provider=mock_provider,
        max_retries=2,
    )

    assert result == draft
    # Only the reviewer is called — no generator retry
    assert mock_provider.aparse_json.call_count == 1


# ---------------------------------------------------------------------------
# Rejects once, then approves
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_once_then_approves_returns_revised_draft(mock_provider):
    """One rejection triggers one generator retry; second review approves."""
    original = {"work_history": [{"company": "Acme", "role": "Dev"}]}
    revised = {"work_history": [{"company": "Acme", "role": "Dev", "start_date": "2020"}]}

    mock_provider.aparse_json.side_effect = [
        # Reviewer call 1: reject
        {"approved": False, "issues": ["start_date missing"], "feedback": "Add start_date from source"},
        # Generator retry 1: revised draft
        revised,
        # Reviewer call 2: approve
        {"approved": True, "issues": [], "feedback": ""},
    ]

    result = await review_and_refine(
        source="Acme Dev 2020-2022",
        draft=original,
        generator_prompt_fn=lambda d, f, s: f"retry with feedback: {f}",
        generator_system="gen system",
        reviewer_prompt_fn=lambda s, d: "review prompt",
        reviewer_system="rev system",
        provider=mock_provider,
        max_retries=2,
    )

    assert result == revised
    assert mock_provider.aparse_json.call_count == 3


# ---------------------------------------------------------------------------
# Retry exhaustion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exhausts_retries_returns_last_draft_and_logs_warning(mock_provider, caplog):
    """When all retries are exhausted, the last generated draft is returned and a warning logged."""
    original = {"work_history": [{"company": "Bad Co"}]}
    retry1 = {"work_history": [{"company": "Still Bad Co"}]}
    retry2 = {"work_history": [{"company": "Final Co"}]}

    mock_provider.aparse_json.side_effect = [
        # attempt 0: reviewer rejects
        {"approved": False, "issues": ["fabricated entry"], "feedback": "Remove fabricated entry"},
        # attempt 0: generator retry
        retry1,
        # attempt 1: reviewer rejects again
        {"approved": False, "issues": ["still fabricated"], "feedback": "Try harder"},
        # attempt 1: generator retry
        retry2,
    ]

    with caplog.at_level(logging.WARNING, logger="applire.services.reviewer"):
        result = await review_and_refine(
            source="original cv text",
            draft=original,
            generator_prompt_fn=lambda d, f, s: f"retry: {f}",
            generator_system="gen system",
            reviewer_prompt_fn=lambda s, d: "review prompt",
            reviewer_system="rev system",
            provider=mock_provider,
            max_retries=2,
        )

    assert result == retry2
    assert mock_provider.aparse_json.call_count == 4
    assert "exhausted" in caplog.text


# ---------------------------------------------------------------------------
# Reviewer prompt is called with correct arguments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_receives_source_and_current_draft(mock_provider):
    """Verifies the reviewer is called with (source, current_draft)."""
    draft = {"key": "value"}
    received_args: list[tuple] = []

    def capture_reviewer(source: str, d: dict) -> str:
        received_args.append((source, d))
        return "review prompt"

    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}

    await review_and_refine(
        source="the source material",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=capture_reviewer,
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=1,
    )

    assert received_args == [("the source material", draft)]


# ---------------------------------------------------------------------------
# US193 — Bounded reviewer (output-by-contract)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reviewer_call_uses_bounded_output_budget(mock_provider):
    """ADR-021 amended / US193: the reviewer is bounded-output-by-contract — it emits
    a small verdict, never re-emits the document. Its aparse_json call must carry a
    small max_tokens (REVIEW_VERDICT_MAX_TOKENS), far below the generator budget, so a
    capped model can never truncate the verdict (the Mistral-8k blind-test failure)."""
    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}

    await review_and_refine(
        source="src",
        draft={"k": "v"},
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review prompt",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
        generator_max_tokens=16384,
    )

    _, kwargs = mock_provider.aparse_json.call_args
    assert kwargs["max_tokens"] == REVIEW_VERDICT_MAX_TOKENS
    assert REVIEW_VERDICT_MAX_TOKENS < 16384


@pytest.mark.asyncio
async def test_reviewer_max_tokens_override_is_respected(mock_provider):
    """An explicit reviewer_max_tokens overrides the default verdict budget."""
    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}

    await review_and_refine(
        source="src",
        draft={"k": "v"},
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review prompt",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=1,
        reviewer_max_tokens=777,
    )

    _, kwargs = mock_provider.aparse_json.call_args
    assert kwargs["max_tokens"] == 777


# ---------------------------------------------------------------------------
# US194 — Refiner re-reads source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generator_retry_receives_feedback_and_source(mock_provider):
    """ADR-021 amended / US194: referential critique requires the refiner to re-read the
    source, so generator_prompt_fn is called as fn(previous_draft, feedback, source)."""
    draft = {"key": "original"}
    received_args: list[tuple] = []

    def capture_generator(d: dict, feedback: str, source: str) -> str:
        received_args.append((d, feedback, source))
        return "retry prompt"

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "specific critique"},
        {"key": "revised"},
        {"approved": True, "issues": [], "feedback": ""},
    ]

    await review_and_refine(
        source="THE SOURCE MATERIAL",
        draft=draft,
        generator_prompt_fn=capture_generator,
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review prompt",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
    )

    assert received_args[0] == (draft, "specific critique", "THE SOURCE MATERIAL")


# ---------------------------------------------------------------------------
# US194 — Cap-safe: refiner truncation/timeout keeps the last good draft
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [LLMTruncatedError("cap"), LLMTimeoutError("slow")])
async def test_refiner_truncation_keeps_last_good_draft(mock_provider, caplog, exc):
    """If the refiner blows the output cap (or times out), the loop must NOT crash and
    must ship the last validated draft (the already-good segmented output), not a
    truncated one. This is the cap-safety property the segmented generation relies on."""
    good_draft = {"work_history": [{"company": "Acme", "role": "Dev"}]}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["fix summary"], "feedback": "tighten summary"},
        exc,  # refiner call truncates / times out
    ]

    with caplog.at_level(logging.WARNING, logger="applire.services.reviewer"):
        result = await review_and_refine(
            source="src",
            draft=good_draft,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review prompt",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
        )

    assert result == good_draft
    assert mock_provider.aparse_json.call_count == 2  # reviewer + one failed refiner, no further loop


@pytest.mark.asyncio
async def test_reviewer_truncation_ships_current_draft(mock_provider, caplog):
    """If the reviewer call itself fails (cap/timeout) the draft ships un-reviewed rather
    than crashing the flow — degraded review is preferable to a broken generation."""
    good_draft = {"work_history": [{"company": "Acme"}]}
    mock_provider.aparse_json.side_effect = LLMTruncatedError("reviewer blew cap")

    with caplog.at_level(logging.WARNING, logger="applire.services.reviewer"):
        result = await review_and_refine(
            source="src",
            draft=good_draft,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review prompt",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
        )

    assert result == good_draft
    assert mock_provider.aparse_json.call_count == 1
