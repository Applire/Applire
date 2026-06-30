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
"""

import logging
from collections.abc import Callable
from typing import Any

from applire.constants import REVIEW_VERDICT_MAX_TOKENS
from applire.exceptions import LLMTimeoutError, LLMTruncatedError
from applire.providers.llm.base import LLMProvider

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

    Returns:
        The approved draft, or the last known-good draft if retries are exhausted, the
        reviewer fails, or a refiner call truncates/times out.
    """
    if max_retries <= 0:
        return draft

    current_draft = draft
    last_issues: list[str] = []

    for attempt in range(max_retries):
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
            logger.warning(
                "review_and_refine: chain=%s reviewer call failed (%s) on attempt %d; "
                "shipping current draft un-reviewed",
                chain_id,
                type(exc).__name__,
                attempt + 1,
            )
            return current_draft

        if review.get("approved", False):
            return current_draft

        last_issues = review.get("issues", [])
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
            logger.warning(
                "review_and_refine: chain=%s refiner call truncated/timed out (%s) on "
                "attempt %d; keeping last known-good draft. Last issues: %r",
                chain_id,
                type(exc).__name__,
                attempt + 1,
                last_issues,
            )
            return current_draft

    # Exhausted all retries — return the last generated draft unreviewed.
    # This is intentional: degraded output is preferable to a broken flow
    # (spec: ADR-021; worst-case call count = 2 * max_retries).
    logger.warning(
        "review_and_refine: chain=%s %d retries exhausted. Last known issues: %r",
        chain_id,
        max_retries,
        last_issues,
    )
    return current_draft
