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

"""LLM Review Layer — reviewer-guided retry loop (ADR-021, Sprint 20).

review_and_refine() runs a reviewer LLM call after the initial generator output.
If the reviewer rejects the draft it feeds the critique back to the generator
and retries, up to max_retries times.

Never raises — on retry exhaustion the last draft is returned and a WARNING
is logged so the issue is observable without breaking the caller's flow.
"""

import logging
from collections.abc import Callable
from typing import Any

from applire.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


async def review_and_refine(
    source: str,
    draft: dict[str, Any],
    generator_prompt_fn: Callable[[dict[str, Any], str], str],
    generator_system: str,
    reviewer_prompt_fn: Callable[[str, dict[str, Any]], str],
    reviewer_system: str,
    provider: LLMProvider,
    max_retries: int,
    generator_max_tokens: int = 4096,
    chain_id: str = "unknown",
    disable_thinking: bool | None = None,
) -> dict[str, Any]:
    """Run a reviewer-guided retry loop over an LLM generator output.

    Source text is passed to the reviewer only. The generator's retry call
    receives the previous draft and the reviewer's feedback — no raw source.
    If the reviewer's fix requires content not present in the draft, the
    reviewer is expected to quote the relevant source passages in `feedback`.

    Args:
        source: The original source material the reviewer checks the draft against.
        draft: The initial generator output to be reviewed.
        generator_prompt_fn: Called as fn(previous_draft, feedback) -> str.
        generator_system: The refinement-mode system prompt (NOT the extraction prompt).
        reviewer_prompt_fn: Called as fn(source, draft) -> str.
        reviewer_system: The reviewer's system prompt.
        provider: LLM provider — same instance used by the calling service.
        max_retries: Maximum number of generator retries. 0 = review layer disabled.
        generator_max_tokens: Token budget for the generator retry calls.
        chain_id: Identifier for the calling chain (cv_extraction, profile_extraction,
                  cv_tailoring, interview_response, interview_question). Used for log dimensionality.
        disable_thinking: Suppress reasoning on both reviewer and generator calls.
                  Set True for short "chrome" loops (e.g. interview-question language
                  review) so a small token budget reaches the answer, not the reasoning
                  trace, under thinking models. Leave None for serious content (CV,
                  cover letter) where reasoning improves quality (ADR-009 amendment).

    Returns:
        The approved draft, or the last generated draft if retries are exhausted.
    """
    if max_retries <= 0:
        return draft

    current_draft = draft
    last_issues: list[str] = []

    for attempt in range(max_retries):
        review: dict = await provider.aparse_json(
            reviewer_prompt_fn(source, current_draft),
            system=reviewer_system,
            temperature=0.1,
            disable_thinking=disable_thinking,
        )

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

        retry_prompt = generator_prompt_fn(current_draft, feedback)
        logger.info(
            "review_and_refine: chain=%s attempt=%d retry_input_chars=%d feedback_chars=%d",
            chain_id,
            attempt + 1,
            len(retry_prompt),
            len(feedback),
        )

        current_draft = await provider.aparse_json(
            retry_prompt,
            system=generator_system,
            temperature=0.1,
            max_tokens=generator_max_tokens,
            disable_thinking=disable_thinking,
        )

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
