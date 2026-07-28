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
    # retry1 must NOT be byte-identical to `original` — an identical draft is itself a
    # cycle (wave-6 Task 1) and would legitimately stop the loop there, before this
    # test's retain_if-substitution scenario (which needs a THIRD, worse draft) ever
    # develops. A distinct-but-still-passing draft isolates the retain_if behaviour.
    retry1 = {"body": {"paragraphs": ["opening (revised)", "closing paragraph that is long enough to pass"]}}
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


# ---------------------------------------------------------------------------
# Wave 6 Task 1 — deterministic cycle detection (loop oscillation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_detection_stops_early_instead_of_exhausting_retries(mock_provider, caplog):
    """Synthetic reproduction of the pinned seniority_level 2-cycle: the reviewer
    alternates two critiques and the generator alternates two drafts (draft A -> draft
    B -> draft A -> ...). Draft 3 is byte-identical to draft 1 (a cycle by definition),
    so the loop must stop at that point rather than burning the remaining retries."""
    draft_a = {"seniority_level": "Mid-Senior level"}
    draft_b = {"seniority_level": None}

    mock_provider.aparse_json.side_effect = [
        # attempt 1: reviewer rejects A ("not stated") -> generator produces B
        {"approved": False, "issues": ["seniority not stated"], "feedback": "null it out"},
        draft_b,
        # attempt 2: reviewer rejects B ("explicitly stated but null") -> generator
        # reverts to A — this is the repeat that closes the cycle.
        {"approved": False, "issues": ["seniority explicitly stated but extracted as null"], "feedback": "restore it"},
        draft_a,
        # attempts 3-5 would continue oscillating forever if the loop didn't stop here.
        {"approved": False, "issues": ["not stated"], "feedback": "null it"},
        draft_b,
        {"approved": False, "issues": ["stated"], "feedback": "restore it"},
        draft_a,
    ]

    with caplog.at_level(logging.WARNING):
        result = await review_and_refine(
            source="... Mid-Senior level ...",
            draft=draft_a,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=5,
            chain_id="cycle_chain",
        )

    assert result == draft_a
    # Cycle detected right after the 2nd generator retry reproduces draft_a: reviewer
    # attempt 1 + generator 1 + reviewer attempt 2 + generator 2 = 4 calls, then stop.
    assert mock_provider.aparse_json.call_count == 4
    cycle_lines = [
        r.getMessage() for r in caplog.records if "CYCLE" in r.getMessage().upper()
    ]
    assert cycle_lines, "expected a distinguishable cycle-detected log line"
    assert any("cycle_chain" in line for line in cycle_lines)
    # Must read distinctly from ordinary exhaustion — never say "exhausted".
    assert not any("exhausted" in line.lower() for line in cycle_lines)


@pytest.mark.asyncio
async def test_cycle_detection_compares_against_the_initial_draft_too(mock_provider, caplog):
    """A generator retry that reproduces the ORIGINAL draft (not just a prior retry)
    is also a cycle — the initial draft must be part of the comparison set."""
    original = {"company_name": "Connect-AI"}
    dropped = {"company_name": None}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["not stated"], "feedback": "drop it"},
        dropped,
        {"approved": False, "issues": ["should be Connect-AI"], "feedback": "restore it"},
        original,  # identical to the very first draft passed in
    ]

    with caplog.at_level(logging.WARNING):
        result = await review_and_refine(
            source="... Connect-AI ...",
            draft=original,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=5,
            chain_id="cycle_vs_initial",
        )

    assert result == original
    assert mock_provider.aparse_json.call_count == 4


@pytest.mark.asyncio
async def test_cycle_detected_emits_a_stable_review_cycle_detected_line(mock_provider, caplog):
    """Mirrors #264's REVIEW_EXHAUSTED contract: a cycle-stop must emit its own
    stable, always-on, PII-free line via the applire.llm.review logger, distinct from
    REVIEW_EXHAUSTED, so cycle-stops are countable after the fact just like
    exhaustion is."""
    draft_a = {"k": "A"}
    draft_b = {"k": "B"}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "y"},
        draft_b,
        {"approved": False, "issues": ["x2"], "feedback": "y2"},
        draft_a,
    ]

    with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
        await review_and_refine(
            source="src",
            draft=draft_a,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=5,
            chain_id="stable_cycle_chain",
        )

    cycle_records = [r for r in caplog.records if "REVIEW_CYCLE_DETECTED" in r.getMessage()]
    assert len(cycle_records) == 1
    assert cycle_records[0].levelname == "WARNING"
    assert "chain=stable_cycle_chain" in cycle_records[0].getMessage()
    exhausted_records = [r for r in caplog.records if "REVIEW_EXHAUSTED" in r.getMessage()]
    assert not exhausted_records


@pytest.mark.asyncio
async def test_no_cycle_when_every_draft_is_distinct(mock_provider):
    """Sanity check: genuinely distinct drafts across retries must NOT be flagged as a
    cycle and must run to normal exhaustion/approval."""
    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        {"d": 2},
        {"approved": False, "issues": ["y"], "feedback": "fix y"},
        {"d": 3},
        {"approved": True, "issues": [], "feedback": ""},
    ]

    result = await review_and_refine(
        source="src",
        draft={"d": 1},
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=3,
    )

    assert result == {"d": 3}
    assert mock_provider.aparse_json.call_count == 5


# ---------------------------------------------------------------------------
# Wave 6 Task 2 — required_fields no-regression floor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_fields_default_none_is_bit_identical_on_field_loss(mock_provider, caplog):
    """required_fields omitted (default None) must reproduce today's behaviour EXACTLY
    even when a field is dropped and never recovers — no-op proof."""
    original = {"company_name": "Connect-AI", "role_title": "Lead AI Engineer"}
    dropped1 = {"company_name": None, "role_title": ""}
    dropped2 = {"company_name": None, "role_title": ""}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["not stated"], "feedback": "drop both"},
        dropped1,
        {"approved": False, "issues": ["still wrong"], "feedback": "try again"},
        dropped2,
    ]

    with caplog.at_level(logging.WARNING, logger="applire.services.reviewer"):
        result = await review_and_refine(
            source="Connect-AI Lead AI Engineer ...",
            draft=original,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
        )

    # required_fields not passed at all: today's plain exhaustion behaviour.
    assert result == dropped2
    assert not any("required_fields" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_required_fields_ships_the_earlier_value_when_dropped(mock_provider, caplog):
    """A synthetic field-loss case: draft 1 has company_name, draft 2 drops it and the
    reviewer never approves again — the loop must ship draft 1's value for the
    declared required field rather than the emptied final draft."""
    original = {"company_name": "Connect-AI", "role_title": "Lead AI Engineer"}
    dropped = {"company_name": None, "role_title": ""}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["not stated"], "feedback": "drop both"},
        dropped,
        {"approved": False, "issues": ["still wrong"], "feedback": "try again"},
        dropped,
    ]

    with caplog.at_level(logging.WARNING, logger="applire.services.reviewer"):
        result = await review_and_refine(
            source="Connect-AI Lead AI Engineer ...",
            draft=original,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
            required_fields=("company_name", "role_title"),
        )

    assert result["company_name"] == "Connect-AI"
    assert result["role_title"] == "Lead AI Engineer"
    assert any("required_fields" in r.getMessage() or "regression" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_required_fields_does_not_treat_a_legitimate_change_as_regression(mock_provider, caplog):
    """A field the reviewer legitimately changed (still present, just different) must
    NOT be treated as a loss — no substitution, no warning."""
    original = {"company_name": "Connect AI GmbH", "role_title": "AI Engineer"}
    corrected = {"company_name": "Connect-AI", "role_title": "Lead AI Engineer"}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["title normalisation"], "feedback": "tidy the name/title"},
        corrected,
        {"approved": True, "issues": [], "feedback": ""},
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
            required_fields=("company_name", "role_title"),
        )

    assert result == corrected
    assert not any("required_fields" in r.getMessage() or "regression" in r.getMessage()
                   for r in caplog.records)


@pytest.mark.asyncio
async def test_required_fields_absent_key_counts_as_missing(mock_provider):
    """'Missing' includes a key that is entirely absent, not just None/''."""
    original = {"company_name": "Connect-AI"}
    dropped_key = {}  # company_name key entirely absent

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "drop it"},
        dropped_key,
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
        required_fields=("company_name",),
    )

    assert result["company_name"] == "Connect-AI"


@pytest.mark.asyncio
async def test_required_fields_fail_open_when_no_draft_ever_had_the_field(mock_provider):
    """If the declared field was never present in ANY draft (e.g. a JD that genuinely
    never named a company), there is nothing to restore — ship the final draft as-is,
    never fabricate a value."""
    original = {"company_name": None}
    still_none = {"company_name": None}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "y"},
        still_none,
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
        required_fields=("company_name",),
    )

    assert result["company_name"] is None


@pytest.mark.asyncio
async def test_required_fields_candidate_must_also_satisfy_retain_if(mock_provider, caplog):
    """When both retain_if and required_fields are supplied, a candidate draft used to
    restore a lost required field must ALSO satisfy retain_if — never reintroduce a
    draft the retention predicate would itself reject."""
    # draft 1 has company_name but fails retain_if (closing too short);
    # draft 2 has neither company_name nor a passing retain_if;
    # there is no draft that has BOTH — nothing eligible to restore from.
    draft1 = {"company_name": "Connect-AI", "body": "short"}
    draft2 = {"company_name": None, "body": "short"}

    def retain_if(d: dict) -> bool:
        return len(d.get("body", "")) > 20

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "y"},
        draft2,
        {"approved": True, "issues": [], "feedback": ""},
    ]

    result = await review_and_refine(
        source="src",
        draft=draft1,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
        required_fields=("company_name",),
        retain_if=retain_if,
    )

    # No eligible candidate (draft1 fails retain_if, draft2 lacks the field) —
    # fail open rather than reintroducing a retain_if-failing draft.
    assert result["company_name"] is None


# ---------------------------------------------------------------------------
# Wave-6 follow-up (charter run #6, Task 2) — retention design v2: prefer_if,
# a SECONDARY structural tie-break over drafts retain_if already accepts.
# ---------------------------------------------------------------------------


def _closing(d: dict) -> bool:
    return bool(d.get("closing"))


def _within_budget(d: dict) -> bool:
    return d.get("words", 0) <= 300


@pytest.mark.asyncio
async def test_prefer_if_default_none_is_bit_identical_to_retain_if_only(mock_provider, caplog):
    """prefer_if omitted (default None) must reproduce the pre-existing
    retain_if-only algorithm EXACTLY — reusing the retain_if substitution
    scenario from test_retain_if_substitutes_earlier_draft_when_final_fails_predicate
    with prefer_if simply not passed."""
    original = {"body": {"paragraphs": ["opening", "closing paragraph that is long enough to pass"]}}
    retry1 = {"body": {"paragraphs": ["opening (revised)", "closing paragraph that is long enough to pass"]}}
    retry2 = {"body": {"paragraphs": ["opening", "short stub"]}}

    def has_long_closing(draft: dict) -> bool:
        paragraphs = draft.get("body", {}).get("paragraphs", [])
        return bool(paragraphs) and len(paragraphs[-1].split()) >= 5

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        retry1,
        {"approved": False, "issues": ["y"], "feedback": "fix y"},
        retry2,
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
        retain_if=has_long_closing,
        # prefer_if intentionally omitted
    )

    assert result == retry1


@pytest.mark.asyncio
async def test_prefer_if_is_a_noop_when_retain_if_is_none(mock_provider):
    """prefer_if without retain_if must never even be consulted — it is only a
    tie-break AMONG drafts retain_if already accepts, so it is meaningless (and
    must be inert) when there is no retain_if at all."""
    calls: list[dict] = []

    def never_call_me(d):
        calls.append(d)
        return False

    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}

    draft = {"words": 999}
    result = await review_and_refine(
        source="src",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=1,
        prefer_if=never_call_me,
        # retain_if intentionally omitted
    )
    assert result == draft
    assert calls == []


@pytest.mark.asyncio
async def test_prefer_if_selects_draft_satisfying_both_over_retain_if_only(mock_provider, caplog):
    """Given drafts [long+closing, short+no-closing, short+closing], the
    short+closing draft (satisfying BOTH retain_if and prefer_if) is chosen —
    even though it is neither the final nor the most recent draft."""
    original = {"closing": True, "words": 250}  # short + closing: satisfies both
    retry1 = {"closing": True, "words": 400}  # long + closing: retain_if only
    retry2 = {"closing": False, "words": 200}  # short + no closing: fails retain_if

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
            retain_if=_closing,
            prefer_if=_within_budget,
        )

    assert result == original
    assert any("prefer_if" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_prefer_if_falls_back_to_retain_if_only_and_logs_loudly(mock_provider, caplog):
    """Given only [long+closing, short+no-closing] — no draft satisfies both —
    the long+closing draft (retain_if alone) is chosen, and a loud WARNING
    names that the non-negotiable structural floor shipped without its
    secondary preference (closing wins over fitting the budget)."""
    original = {"closing": True, "words": 400}  # long + closing
    retry1 = {"closing": False, "words": 200}  # short + no closing

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        retry1,
        {"approved": True, "issues": [], "feedback": ""},
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
            retain_if=_closing,
            prefer_if=_within_budget,
        )

    assert result == original
    assert any(
        "retain_if" in r.getMessage() and "prefer_if" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_prefer_if_never_overrides_a_failing_retain_if(mock_provider):
    """prefer_if can never select a draft retain_if rejects, even when that
    draft satisfies prefer_if and the retain_if-satisfying draft does not —
    structure (retain_if) always wins."""
    original = {"closing": True, "words": 999}  # closing, but over budget
    retry1 = {"closing": False, "words": 1}  # tiny (prefer_if True), no closing

    def impossible_retain(d: dict) -> bool:
        return _closing(d)

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
        retain_if=impossible_retain,
        prefer_if=_within_budget,
    )

    # retry1 satisfies prefer_if but fails retain_if — must NEVER be chosen.
    assert result == original


@pytest.mark.asyncio
async def test_prefer_if_no_substitution_log_when_final_already_satisfies_both(mock_provider, caplog):
    """No substitution and no warning when the settled draft already satisfies
    both retain_if and prefer_if."""
    draft = {"closing": True, "words": 100}

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
            retain_if=_closing,
            prefer_if=_within_budget,
        )

    assert result == draft
    assert not any(
        "retain_if" in r.getMessage() or "prefer_if" in r.getMessage() for r in caplog.records
    )


# ---------------------------------------------------------------------------
# ADR-021 amended 2026-07-28 — the severity gate: the writer runs again ONLY
# for a blocking issue. See prompts/review_severity.py (the contract) and
# services/review_issues.py (parsing + the #306 measurement checks).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_minor_only_round_does_not_spend_a_rewrite(mock_provider, caplog):
    """A reviewer round that rejects the draft but raises only MINOR issues
    ships it — a rewrite is a memoryless regeneration that can erode a correct
    fact, and that trade is never worth making for polish."""
    draft = {"body": {"paragraphs": ["Wir stellen Verbundverpackungen her."]}}

    mock_provider.aparse_json.return_value = {
        "approved": False,
        "issues": [
            {"severity": "minor", "issue": "The employer name repeats across paragraphs."},
            {"severity": "minor", "issue": "Opening sentence is a little flat."},
        ],
        "feedback": "tighten it",
    }

    with caplog.at_level(logging.INFO, logger="applire.llm.review"):
        result = await review_and_refine(
            source="src",
            draft=draft,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=5,
            chain_id="cover_letter",
        )

    # Ships the ORIGINAL draft — no generator retry call was ever made, so
    # only the single reviewer call happened.
    assert result == draft
    assert mock_provider.aparse_json.call_count == 1
    minor_only = [r for r in caplog.records if "REVIEW_MINOR_ONLY" in r.getMessage()]
    assert len(minor_only) == 1
    assert "chain=cover_letter" in minor_only[0].getMessage()
    assert "minor=2" in minor_only[0].getMessage()


@pytest.mark.asyncio
async def test_one_blocking_issue_among_minor_ones_still_rewrites(mock_provider):
    """The gate is ANY blocking issue, not a majority — one untrue claim
    alongside three wording nits still costs a rewrite."""
    draft = {"body": {"paragraphs": ["draft"]}}
    retried = {"body": {"paragraphs": ["retried"]}}

    mock_provider.aparse_json.side_effect = [
        {
            "approved": False,
            "issues": [
                {"severity": "minor", "issue": "Repetitive employer naming."},
                {"severity": "blocking", "issue": "Paragraph 2 invents a 40% figure."},
                {"severity": "minor", "issue": "Closing is wordy."},
            ],
            "feedback": "remove the invented figure",
        },
        retried,
        {"approved": True, "issues": [], "feedback": ""},
    ]

    result = await review_and_refine(
        source="src",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
    )

    assert result == retried
    assert mock_provider.aparse_json.call_count == 3


@pytest.mark.asyncio
async def test_pre_severity_string_issues_still_rewrite(mock_provider):
    """Fail-safe: a model (or mock, or older prompt) that emits plain issue
    strings gets EXACTLY the pre-amendment behaviour — a rewrite. The gate must
    never turn an unparsed verdict into a silent approval."""
    draft = {"body": {"paragraphs": ["draft"]}}
    retried = {"body": {"paragraphs": ["retried"]}}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["Missing required closing paragraph."], "feedback": "fix"},
        retried,
        {"approved": True, "issues": [], "feedback": ""},
    ]

    result = await review_and_refine(
        source="src",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
    )

    assert result == retried
    assert mock_provider.aparse_json.call_count == 3


@pytest.mark.asyncio
async def test_rejection_naming_no_issue_at_all_still_rewrites(mock_provider):
    """A rejection that enumerates NOTHING (all the substance in `feedback`) is
    not a minor-only round — it is an unreadable one, and it must retry exactly
    as it did before severity existed."""
    draft = {"body": {"paragraphs": ["draft"]}}
    retried = {"body": {"paragraphs": ["retried"]}}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": [], "feedback": "Paragraph 2 invents a figure."},
        retried,
        {"approved": True, "issues": [], "feedback": ""},
    ]

    result = await review_and_refine(
        source="src",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
    )

    assert result == retried
    assert mock_provider.aparse_json.call_count == 3


@pytest.mark.asyncio
async def test_unsound_blocking_issues_are_measured_but_no_longer_short_circuit(
    mock_provider, caplog
):
    """#306 demoted to measurement (2026-07-28): an all-self-refuting round is
    still COUNTED as imprecise, but it no longer changes what the loop does —
    the issues were filed blocking, so the rewrite happens. Reviewer precision
    is now a prompt/severity concern, not a pattern-matching one."""
    draft = {"body": {"paragraphs": ["draft"]}}
    retried = {"body": {"paragraphs": ["retried"]}}
    self_refuting_issue = (
        "Invented employer fact — 'X' is not in the job_description text "
        "(only 'X' appears)."
    )

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": [self_refuting_issue], "feedback": "fix"},
        retried,
        {"approved": True, "issues": [], "feedback": ""},
    ]

    with caplog.at_level(logging.INFO, logger="applire.llm.review"):
        result = await review_and_refine(
            source="src",
            draft=draft,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
            chain_id="cover_letter",
        )

    assert result == retried
    assert mock_provider.aparse_json.call_count == 3
    precision = [r for r in caplog.records if "REVIEW_PRECISION" in r.getMessage()]
    assert "raised=1 survived=0 discarded=1" in precision[0].getMessage()
    assert not any("REVIEW_MINOR_ONLY" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_precision_log_emitted_every_attempt(mock_provider, caplog):
    """REVIEW_PRECISION is logged every reviewer attempt with raised/survived
    counts, so a chain's reviewer noise ratio is countable after the fact."""
    self_refuting_issue = (
        "Invented employer fact — 'X' is not in the job_description text "
        "(only 'X' appears)."
    )
    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": [self_refuting_issue, "genuine issue"], "feedback": "f"},
        {"body": {"paragraphs": ["retried"]}},
        {"approved": True, "issues": [], "feedback": ""},
    ]

    with caplog.at_level(logging.INFO, logger="applire.llm.review"):
        await review_and_refine(
            source="src",
            draft={"body": {"paragraphs": ["draft"]}},
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
            chain_id="job_analysis",
        )

    precision_lines = [r.getMessage() for r in caplog.records if "REVIEW_PRECISION" in r.getMessage()]
    assert len(precision_lines) == 2
    assert "chain=job_analysis attempt=1 raised=2 survived=1 discarded=1" in precision_lines[0]
    assert "chain=job_analysis attempt=2 raised=0 survived=0 discarded=0" in precision_lines[1]


@pytest.mark.asyncio
async def test_plain_issues_are_unaffected_by_the_filter(mock_provider):
    """Regression guard: ordinary short issue text (the existing fixtures
    throughout this file use plain strings like 'x', 'a', 'b') must never be
    treated as all-discardable — every existing caller's retry behaviour is
    unaffected by #306 (a)."""
    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        {"d": 2},
        {"approved": True, "issues": [], "feedback": ""},
    ]

    result = await review_and_refine(
        source="src",
        draft={"d": 1},
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=2,
    )
    assert result == {"d": 2}
    assert mock_provider.aparse_json.call_count == 3


# ---------------------------------------------------------------------------
# #306 (b) — load_bearing_fn: the retain_if/prefer_if substitution must not be
# evidence-blind. Pinned against charter run #7 case 2's actual mechanism:
# round 0 (pre-review) fits the word budget but is missing the OEE arc every
# later round carried; the settled (final) round is over budget but keeps it.
# ---------------------------------------------------------------------------


def _figures(d: dict) -> frozenset:
    """Minimal stand-in for load_bearing_fn_from_ledger in these tests — the
    real factory is covered in test_load_bearing.py; here we only need SOME
    deterministic per-draft figure-count function."""
    return frozenset(d.get("figures", ()))


@pytest.mark.asyncio
async def test_load_bearing_fn_refuses_evidence_poorer_substitution(mock_provider, caplog):
    """Given [round0: short+closing+NO figures, round1: long+closing+figures
    (the settled/final draft, over budget)], the prefer_if scan would normally
    substitute round0 (satisfies retain_if AND prefer_if) — but round0 is
    evidence-poorer, so the substitution must be REFUSED and the final draft
    (over budget, but with its figures intact) ships instead."""
    round0 = {"closing": True, "words": 100, "figures": []}
    round1 = {"closing": True, "words": 400, "figures": ["percent:61", "percent:73"]}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        round1,
        {"approved": True, "issues": [], "feedback": ""},
    ]

    with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
        result = await review_and_refine(
            source="src",
            draft=round0,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
            retain_if=_closing,
            prefer_if=_within_budget,
            load_bearing_fn=_figures,
        )

    # round1 (the final/settled draft) ships — round0 was refused despite
    # satisfying BOTH structural predicates, because it would have lost both
    # figures.
    assert result == round1
    refused = [r for r in caplog.records if "REVIEW_SUBSTITUTION_REFUSED" in r.getMessage()]
    assert len(refused) == 1
    assert "percent:61" in refused[0].getMessage()
    assert "percent:73" in refused[0].getMessage()
    # No substitution actually happened, so no substitution DIFF log either.
    assert not any("REVIEW_SUBSTITUTION_DIFF" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_load_bearing_fn_allows_substitution_when_not_poorer(mock_provider, caplog):
    """When the candidate draft satisfies retain_if+prefer_if and is NOT
    evidence-poorer than the settled draft, the substitution proceeds exactly
    as before, and the diff is logged (retained/lost/gained)."""
    round0 = {"closing": True, "words": 100, "figures": ["percent:61"]}
    round1 = {"closing": True, "words": 400, "figures": ["percent:61"]}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        round1,
        {"approved": True, "issues": [], "feedback": ""},
    ]

    with caplog.at_level(logging.WARNING, logger="applire.llm.review"):
        result = await review_and_refine(
            source="src",
            draft=round0,
            generator_prompt_fn=lambda d, f, s: "retry",
            generator_system="gen",
            reviewer_prompt_fn=lambda s, d: "review",
            reviewer_system="rev",
            provider=mock_provider,
            max_retries=2,
            retain_if=_closing,
            prefer_if=_within_budget,
            load_bearing_fn=_figures,
        )

    assert result == round0
    diffs = [r for r in caplog.records if "REVIEW_SUBSTITUTION_DIFF" in r.getMessage()]
    assert len(diffs) == 1
    assert "retained=['percent:61']" in diffs[0].getMessage()
    assert "lost=[]" in diffs[0].getMessage()


@pytest.mark.asyncio
async def test_load_bearing_fn_default_none_is_bit_identical(mock_provider):
    """load_bearing_fn omitted (default None) must reproduce the EXACT
    pre-#306 substitution algorithm — reusing the same scenario as
    test_prefer_if_selects_draft_satisfying_both_over_retain_if_only, minus
    load_bearing_fn."""
    original = {"closing": True, "words": 250}
    retry1 = {"closing": True, "words": 400}
    retry2 = {"closing": False, "words": 200}

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["x"], "feedback": "fix x"},
        retry1,
        {"approved": False, "issues": ["y"], "feedback": "fix y"},
        retry2,
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
        retain_if=_closing,
        prefer_if=_within_budget,
        # load_bearing_fn intentionally omitted
    )

    assert result == original


@pytest.mark.asyncio
async def test_load_bearing_fn_is_a_noop_when_retain_if_is_none(mock_provider):
    """load_bearing_fn without retain_if must never even be consulted — like
    prefer_if, it only ever narrows a choice retain_if already makes."""
    calls: list[dict] = []

    def never_call_me(d):
        calls.append(d)
        return frozenset()

    mock_provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}

    draft = {"words": 999}
    result = await review_and_refine(
        source="src",
        draft=draft,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=1,
        load_bearing_fn=never_call_me,
        # retain_if intentionally omitted
    )
    assert result == draft
    assert calls == []


@pytest.mark.asyncio
async def test_load_bearing_fn_scans_further_back_for_a_non_poorer_candidate(mock_provider, caplog):
    """When the MOST RECENT earlier candidate satisfying both predicates is
    evidence-poorer, the scan must keep looking further back rather than
    giving up immediately — an even-earlier draft that is BOTH structurally
    eligible AND not evidence-poorer should still be found and used."""
    round0 = {"closing": True, "words": 100, "figures": ["percent:61"]}  # eligible, NOT poorer
    round1 = {"closing": False, "words": 500, "figures": []}  # fails retain_if
    round2 = {"closing": True, "words": 120, "figures": []}  # eligible but POORER (most recent)
    final = {"closing": True, "words": 999, "figures": ["percent:61"]}  # fails prefer_if

    mock_provider.aparse_json.side_effect = [
        {"approved": False, "issues": ["a"], "feedback": "a"},
        round1,
        {"approved": False, "issues": ["b"], "feedback": "b"},
        round2,
        {"approved": False, "issues": ["c"], "feedback": "c"},
        final,
    ]

    result = await review_and_refine(
        source="src",
        draft=round0,
        generator_prompt_fn=lambda d, f, s: "retry",
        generator_system="gen",
        reviewer_prompt_fn=lambda s, d: "review",
        reviewer_system="rev",
        provider=mock_provider,
        max_retries=3,
        retain_if=_closing,
        prefer_if=_within_budget,
        load_bearing_fn=_figures,
    )

    assert result == round0
