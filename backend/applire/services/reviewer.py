# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""LLM Review Layer — reviewer-guided retry loop (ADR-021, Sprint 20;
amended 2026-06-29 / E036 for cap-safety).

review_and_refine() runs a reviewer LLM call after the initial generator output.
If the reviewer rejects the draft it feeds the critique back to the generator
and retries, up to max_retries times.

Cap-safety (ADR-021 amended / ADR-047 call-shape taxonomy):
  * The reviewer is **bounded-output-by-contract** — it reads the full draft +
    source (large INPUT is fine) but only ever emits a small {approved, issues,
    feedback} verdict, capped at ``reviewer_max_tokens``. It must never re-emit the
    document, so a capped model can't truncate the verdict (the Mistral-8k crash).
  * Critique is **referential**, not verbatim: the reviewer points at the offending
    location and the **refiner re-reads the source** (``generator_prompt_fn`` now
    takes ``source``) to fix it. This keeps the verdict small.
  * A reviewer or refiner call that still blows the output cap (or times out) is
    caught — the last validated draft ships rather than crashing the flow.

Never raises — on retry exhaustion, reviewer failure, or refiner truncation the last
known-good draft is returned and a WARNING is logged so the issue stays observable.

Observability (#264): every reviewer verdict, and any exhaustion or call failure, is
also logged via the standard (always-on, PII-free) ``applire.llm.review`` logger with
a stable ``REVIEW_VERDICT`` / ``REVIEW_EXHAUSTED`` / ``REVIEW_CALL_FAILED`` prefix, and
each call within the loop is tagged with its role/attempt on the debug-log record (see
``providers/llm/debug_log.py``) — an exhausted review is countable after the fact
without heuristic prompt-matching, and stays visible even when the (dev-only,
prompt-content-bearing) debug log is off.

Retention (#272 Task 3, ADR-058 freeze): the loop above has no no-regression
invariant — each round is a memoryless corrector rewrite, so a reviewer mistake
(or an over-eager correction) can erode content a PRIOR round had right, and later
rounds never recover it. ``retain_if`` is an OPTIONAL, opt-in deterministic
predicate over a settled draft; when supplied, the loop tracks every draft it
produces and, if the FINAL settled draft fails the predicate while an earlier
round's draft satisfied it, substitutes that earlier draft back in (loudly
logged). This never adds an LLM call — it only chooses among drafts the existing
bounded loop already produced. Default ``None`` reproduces today's behaviour for
every existing caller bit-for-bit (proven by test).
"""

import logging
from collections.abc import Callable
from typing import Any

from applire.constants import REVIEW_VERDICT_MAX_TOKENS
from applire.exceptions import LLMTimeoutError, LLMTruncatedError
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import (
    log_review_call_failed,
    log_review_exhausted,
    log_review_verdict,
    set_review_call_meta,
)
from applire.providers.llm.debug_log import set_stage as set_llm_log_stage

logger = logging.getLogger(__name__)


async def review_and_refine(
    source: str,
    draft: dict[str, Any],
    generator_prompt_fn: Callable[[dict[str, Any], str, str], str],
    generator_system: str,
    reviewer_prompt_fn: Callable[[str, dict[str, Any]], str],
    reviewer_system: str,
    provider: LLMProvider,
    max_retries: int,
    generator_max_tokens: int = 4096,
    reviewer_max_tokens: int = REVIEW_VERDICT_MAX_TOKENS,
    chain_id: str = "unknown",
    disable_thinking: bool | None = None,
    retain_if: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Run a reviewer-guided retry loop over an LLM generator output.

    The reviewer reads source + draft but emits only a small bounded verdict
    (``reviewer_max_tokens``). On rejection the refiner is given the previous draft,
    the reviewer's feedback, AND the source so it can act on referential critique.

    Args:
        source: The original source material — passed to the reviewer AND, since the
                ADR-021 amendment, to the refiner so it can re-read the ground truth.
        draft: The initial generator output to be reviewed.
        generator_prompt_fn: Called as fn(previous_draft, feedback, source) -> str.
        generator_system: The refinement-mode system prompt (NOT the extraction prompt).
        reviewer_prompt_fn: Called as fn(source, draft) -> str.
        reviewer_system: The reviewer's system prompt.
        provider: LLM provider — same instance used by the calling service.
        max_retries: Maximum number of generator retries. 0 = review layer disabled.
        generator_max_tokens: Token budget for the generator retry calls.
        reviewer_max_tokens: Bounded output budget for the verdict (default
                  REVIEW_VERDICT_MAX_TOKENS) — keep it far below generator_max_tokens.
        chain_id: Identifier for the calling chain (cv_extraction, profile_extraction,
                  cv_tailoring, interview_response, interview_question). Used for log dimensionality.
        disable_thinking: Suppress reasoning on both reviewer and generator calls.
                  Set True for short "chrome" loops (e.g. interview-question language
                  review) so a small token budget reaches the answer, not the reasoning
                  trace, under thinking models. Leave None for serious content (CV,
                  cover letter) where reasoning improves quality (ADR-009 amendment).
        retain_if: Optional (#272 Task 3) deterministic, STRUCTURAL-ONLY predicate over
                  a draft — never a quality score, never an LLM call. Default None
                  reproduces today's behaviour exactly (no history tracking overhead
                  beyond a plain list append, no behavioural change). When supplied,
                  every draft produced by this loop (the initial draft, and each
                  generator retry) is tracked; if the draft this call would otherwise
                  return fails ``retain_if`` but an EARLIER draft in this same loop
                  satisfied it, that earlier draft is returned instead, and the
                  substitution is logged at WARNING so it stays observable.

    Returns:
        The approved draft, or the last known-good draft if retries are exhausted, the
        reviewer fails, or a refiner call truncates/times out — subject to the
        ``retain_if`` substitution above when that predicate is supplied.
    """
    # #272 Task 3: track every draft this loop produces so an opt-in retain_if can
    # choose among them at settle time. Cheap (a list append) and inert when
    # retain_if is None — the settle helper below short-circuits immediately.
    draft_history: list[dict[str, Any]] = [draft]

    def _settle(final: dict[str, Any]) -> dict[str, Any]:
        """Apply the optional retention predicate to a draft this function is
        about to return. retain_if=None is a pure pass-through — behaviour is
        bit-identical to pre-#272 for every existing caller."""
        if retain_if is None:
            return final
        if retain_if(final):
            return final
        for candidate in reversed(draft_history[:-1]):
            if retain_if(candidate):
                logger.warning(
                    "review_and_refine: chain=%s retain_if rejected the settled draft; "
                    "substituting an earlier round's draft that satisfied the "
                    "retention predicate instead (ADR-058 freeze: no new LLM call, "
                    "only a choice among already-produced drafts).",
                    chain_id,
                )
                return candidate
        logger.warning(
            "review_and_refine: chain=%s retain_if rejected the settled draft and no "
            "earlier draft in this loop satisfied it either; shipping the settled "
            "draft as-is (fail-open — never fabricate to satisfy the predicate).",
            chain_id,
        )
        return final

    if max_retries <= 0:
        return _settle(draft)

    # Tag every reviewer/refiner LLM call in this loop for the debug log (no-op in prod).
    set_llm_log_stage(chain_id)

    current_draft = draft
    last_issues: list[str] = []

    try:
        for attempt in range(max_retries):
            # #264: label this call's role/attempt on the debug-log record — `stage`
            # alone can't tell a reviewer-verdict call apart from a corrector-retry
            # call in the same chain.
            set_review_call_meta("reviewer", attempt + 1)
            try:
                review: dict = await provider.aparse_json(
                    reviewer_prompt_fn(source, current_draft),
                    system=reviewer_system,
                    temperature=0.1,
                    max_tokens=reviewer_max_tokens,
                    disable_thinking=disable_thinking,
                )
            except (LLMTruncatedError, LLMTimeoutError) as exc:
                # The bounded verdict should never blow the cap; if it somehow does (or the
                # call times out) ship the current draft un-reviewed rather than crash.
                log_review_call_failed(chain_id, "reviewer", attempt + 1, type(exc).__name__)
                logger.warning(
                    "review_and_refine: chain=%s reviewer call failed (%s) on attempt %d; "
                    "shipping current draft un-reviewed",
                    chain_id,
                    type(exc).__name__,
                    attempt + 1,
                )
                return _settle(current_draft)

            approved = bool(review.get("approved", False))
            last_issues = review.get("issues", [])
            # #264: structured, always-on verdict line — every attempt, approved or
            # not, so retry-round distributions are countable without heuristic
            # prompt-matching over the (dev-only) debug log.
            log_review_verdict(
                chain_id, attempt + 1, max_retries, approved=approved, issues_count=len(last_issues)
            )
            if approved:
                return _settle(current_draft)

            feedback = review.get("feedback", "")
            logger.debug(
                "review_and_refine attempt %d/%d rejected. Issues: %r",
                attempt + 1,
                max_retries,
                last_issues,
            )

            retry_prompt = generator_prompt_fn(current_draft, feedback, source)
            logger.info(
                "review_and_refine: chain=%s attempt=%d retry_input_chars=%d feedback_chars=%d",
                chain_id,
                attempt + 1,
                len(retry_prompt),
                len(feedback),
            )

            set_review_call_meta("generator", attempt + 1)
            try:
                current_draft = await provider.aparse_json(
                    retry_prompt,
                    system=generator_system,
                    temperature=0.1,
                    max_tokens=generator_max_tokens,
                    disable_thinking=disable_thinking,
                )
            except (LLMTruncatedError, LLMTimeoutError) as exc:
                # The refiner regenerates the document and can blow a small output cap.
                # The pre-refinement draft was already validated (e.g. the segmented
                # generation that produced it), so ship that rather than a truncated
                # refinement or a crash (ADR-021 amended / ADR-047 cap-safety).
                log_review_call_failed(chain_id, "generator", attempt + 1, type(exc).__name__)
                logger.warning(
                    "review_and_refine: chain=%s refiner call truncated/timed out (%s) on "
                    "attempt %d; keeping last known-good draft. Last issues: %r",
                    chain_id,
                    type(exc).__name__,
                    attempt + 1,
                    last_issues,
                )
                return _settle(current_draft)

            # #272 Task 3: a fresh draft was produced — track it so an opt-in
            # retain_if can consider it at settle time. No-op cost when
            # retain_if is None (the list is simply never consulted).
            draft_history.append(current_draft)

        # Exhausted all retries — return the last generated draft unreviewed.
        # This is intentional: degraded output is preferable to a broken flow
        # (spec: ADR-021; worst-case call count = 2 * max_retries). #264: this is the
        # "ships silently" case — make it loudly, durably visible.
        log_review_exhausted(chain_id, max_retries, len(last_issues))
        logger.warning(
            "review_and_refine: chain=%s %d retries exhausted. Last known issues: %r",
            chain_id,
            max_retries,
            last_issues,
        )
        return _settle(current_draft)
    finally:
        # Clear the role/attempt label so a later, unrelated call in this task
        # doesn't inherit a stale review-loop position.
        set_review_call_meta(None, None)
