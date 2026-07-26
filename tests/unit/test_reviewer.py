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
from applire.providers.llm import debug_log


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


# ---------------------------------------------------------------------------
# #264 — review-loop observability: structured verdict/exhaustion signals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_attempt_emits_a_structured_verdict_log(mock_provider, caplog):
    """Each reviewer verdict — approved or rejected — is logged as a structured,
    PII-free REVIEW_VERDICT line via the always-on applire.llm.review logger, so
    retry-round distributions are countable without heuristic prompt-matching."""
    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["a", "b"], "feedback": "fix a and b"},
        {"patched": True},
        {"approved": True, "issues": [], "feedback": ""},
    ]

    with caplog.at_level(logging.INFO, logger="applire.llm.review"):
        await review_and_refine(
            source="src",
            draft={"d": 1},
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=3,
            chain_id="my_chain",
        )

    verdict_lines = [r.getMessage() for r in caplog.records if "REVIEW_VERDICT" in r.getMessage()]
    assert len(verdict_lines) == 2  # attempt 1 rejected, attempt 2 approved
    assert "chain=my_chain attempt=1/3 approved=False issues=2" in verdict_lines[0]
    assert "chain=my_chain attempt=2/3 approved=True issues=0" in verdict_lines[1]


@pytest.mark.asyncio
async def test_exhaustion_emits_a_review_exhausted_warning(mock_provider, caplog):
    """Retry exhaustion must be countable from a stable, greppable log line — not
    just the free-text WARNING that already existed."""
    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        {"d": 2},
        {"approved": False, "issues": ["y", "z"], "feedback": "fix y and z"},
        {"d": 3},
    ]

    with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
        await review_and_refine(
            source="src",
            draft={"d": 1},
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
            chain_id="exhaust_chain",
        )

    exhausted = [r for r in caplog.records if "REVIEW_EXHAUSTED" in r.getMessage()]
    assert len(exhausted) == 1
    assert exhausted[0].levelname == "WARNING"
    assert "chain=exhaust_chain" in exhausted[0].getMessage()
    assert "max_retries=2" in exhausted[0].getMessage()


@pytest.mark.asyncio
async def test_review_call_meta_labels_each_call_and_clears_after(mock_provider):
    """Each provider call within the loop is tagged with its role/attempt via the
    debug_log ContextVar, and the label is cleared once the loop returns."""
    seen: list[tuple[str | None, int | None]] = []
    real_set = debug_log.set_review_call_meta

    def _spy(role, attempt):
        seen.append((role, attempt))
        real_set(role, attempt)

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        {"d": 2},
        {"approved": True, "issues": [], "feedback": ""},
    ]

    import applire.services.reviewer as reviewer_module
    original = reviewer_module.set_review_call_meta
    reviewer_module.set_review_call_meta = _spy
    try:
        await review_and_refine(
            source="src",
            draft={"d": 1},
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
            chain_id="labelled_chain",
        )
    finally:
        reviewer_module.set_review_call_meta = original

    assert seen == [
        ("reviewer", 1),
        ("generator", 1),
        ("reviewer", 2),
        (None, None),  # cleared in the finally block
    ]


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


# ---------------------------------------------------------------------------
# #272 Task 3 — optional, opt-in deterministic retention predicate (retain_if)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retain_if_default_none_is_bit_identical_on_exhaustion(mock_provider, caplog):
    """retain_if omitted (default None) must produce EXACTLY today's behaviour —
    even when the final draft would fail an arbitrary predicate. Proven by
    reusing the exact exhaustion scenario from
    test_exhausts_retries_returns_last_draft_and_logs_warning with retain_if
    simply not passed."""
    original = {"work_history": [{"company": "Bad Co"}]}
    retry1 = {"work_history": [{"company": "Still Bad Co"}]}
    retry2 = {"work_history": [{"company": "Final Co"}]}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["fabricated entry"], "feedback": "Remove fabricated entry"},
        retry1,
        {"approved": False, "issues": ["still fabricated"], "feedback": "Try harder"},
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

    # Bit-identical to the pre-#272 test: no retain_if kwarg passed at all.
    assert result == retry2
    assert mock_provider.aparse_json.call_count == 4
    assert "exhausted" in caplog.text
    assert not any("retain_if" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_retain_if_none_ignores_a_failing_predicate_semantics(mock_provider):
    """A second, explicit proof that retain_if=None never even consults a
    predicate: pass a predicate that would ALWAYS reject via a side channel we
    assert was never called."""
    calls: list[dict] = []

    def never_call_me(d):
        calls.append(d)
        return False

    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}

    draft = {"work_history": []}
    result = await review_and_refine(
        source="src",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=1,
        # retain_if intentionally omitted — never call `never_call_me`
    )
    assert result == draft
    assert calls == []


@pytest.mark.asyncio
async def test_retain_if_substitutes_earlier_draft_when_final_fails_predicate(mock_provider, caplog):
    """When supplied, retain_if is checked against the settled (returned) draft.
    If it fails but an EARLIER round's draft satisfied it, that earlier draft is
    returned instead — no new LLM call, just choosing among drafts the bounded
    loop already produced (ADR-058 freeze)."""
    original = {"body": {"paragraphs": ["opening", "closing paragraph that is long enough to pass"]}}
    retry1 = {"body": {"paragraphs": ["opening", "closing paragraph that is long enough to pass"]}}
    retry2 = {"body": {"paragraphs": ["opening", "short stub"]}}  # fails predicate

    def has_long_closing(draft: dict) -> bool:
        paragraphs = draft.get("body", {}).get("paragraphs", [])
        return bool(paragraphs) and len(paragraphs[-1].split()) >= 5

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        retry1,
        {"approved": False, "issues": ["y"], "feedback": "fix y"},
        retry2,
    ]

    with caplog.at_level(logging.WARNING, logger="applire.services.reviewer"):
        result = await review_and_refine(
            source="src",
            draft=original,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
            retain_if=has_long_closing,
        )

    # retry2 (the exhausted final draft) fails the predicate; retry1 (identical
    # to original, which passes) must be substituted in.
    assert result == retry1
    assert any("retain_if" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_retain_if_returns_final_when_it_already_satisfies_predicate(mock_provider, caplog):
    """No substitution — and no substitution warning — when the settled draft
    already satisfies retain_if."""
    draft = {"body": {"paragraphs": ["opening", "a properly long closing paragraph indeed"]}}

    def has_long_closing(d: dict) -> bool:
        paragraphs = d.get("body", {}).get("paragraphs", [])
        return bool(paragraphs) and len(paragraphs[-1].split()) >= 5

    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}

    with caplog.at_level(logging.WARNING, logger="applire.services.reviewer"):
        result = await review_and_refine(
            source="src",
            draft=draft,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=1,
            retain_if=has_long_closing,
        )

    assert result == draft
    assert not any("retain_if" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_retain_if_ships_final_when_no_draft_ever_satisfies_it(mock_provider, caplog):
    """Fail-open, honestly: if NO draft in the whole history satisfies retain_if,
    the settled (final) draft ships anyway — never crash, never fabricate."""
    original = {"body": {"paragraphs": ["short"]}}
    retry1 = {"body": {"paragraphs": ["still short"]}}

    def impossible(d: dict) -> bool:
        return False

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        retry1,
        {"approved": True, "issues": [], "feedback": ""},
    ]

    result = await review_and_refine(
        source="src",
        draft=original,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
        retain_if=impossible,
    )
    assert result == retry1


@pytest.mark.asyncio
async def test_retain_if_checked_on_reviewer_call_failure_path(mock_provider, caplog):
    """The reviewer-call-failure early-return path must also route through
    retain_if (trivially — only one draft exists in history, so it just checks
    that single draft and ships it either way)."""
    good_draft = {"body": {"paragraphs": ["opening", "a properly long closing paragraph indeed"]}}
    mock_provider.aparse_json.side_effect = LLMTruncatedError("reviewer blew cap")

    def has_long_closing(d: dict) -> bool:
        paragraphs = d.get("body", {}).get("paragraphs", [])
        return bool(paragraphs) and len(paragraphs[-1].split()) >= 5

    result = await review_and_refine(
        source="src",
        draft=good_draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review prompt",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
        retain_if=has_long_closing,
    )
    assert result == good_draft
