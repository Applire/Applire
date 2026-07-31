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

"""Every ADR-021 reviewer chain must be recognised by MockLLMProvider.

Regression guard for a defect class that unit tests structurally cannot catch
(CI 2026-07-25, PR #275): #264 wired a new JD-analysis reviewer, but the mock
provider did not recognise its system prompt, so under ``LLM_PROVIDER=mock`` the
call fell through to the generic ``{"mock": ...}`` fallback. ``review_and_refine``
reads ``approved=None``, retries to exhaustion, and ships the fallback dict —
which then fails ``JobAnalysis`` validation and surfaces as HTTP 422 on
``/api/jobs/analyze``. The unit suites missed it because the tests touching that
path patch ``LLM_REVIEW_MAX_RETRIES=0``, disabling the review layer entirely; only
the Integration & E2E job (real mock stack, real retry budget) reproduced it.

This test asserts the invariant directly against each reviewer prompt's own
system text, so adding a reviewer without teaching the mock about it fails here
rather than in CI's slowest job.
"""
from __future__ import annotations

import pytest

from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as CL_REVIEW
from applire.prompts.review_cv_extraction import (
    CV_EXTRACTION_REVIEW_SYSTEM_PROMPT as CV_EXTRACT_REVIEW,
)
from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT as CV_TAILOR_REVIEW
from applire.prompts.review_cv_language import (
    CV_LANGUAGE_REVIEW_SYSTEM_PROMPT as CV_LANGUAGE_REVIEW,
)
from applire.prompts.review_job_analysis import (
    JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT as JD_REVIEW,
)
from applire.providers.llm.mock import MockLLMProvider

# Every reviewer whose verdict gates a document the user receives. A reviewer
# missing here ships a corrupt artifact under the mock provider.
REVIEWER_PROMPTS = {
    "cover_letter": CL_REVIEW,
    "cv_extraction": CV_EXTRACT_REVIEW,
    "cv_tailoring": CV_TAILOR_REVIEW,
    # E049/ADR-067: the CV-language enforcement chain (services/cv.py
    # _review_cv_language) opens "You are a language reviewer for an AI-generated,
    # tailored CV draft..." — recognised by mock.py's "language reviewer" match.
    "cv_language": CV_LANGUAGE_REVIEW,
    "job_analysis": JD_REVIEW,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("chain,system_prompt", sorted(REVIEWER_PROMPTS.items()))
async def test_mock_recognises_every_reviewer_chain(chain: str, system_prompt: str):
    """The mock must return a real reviewer verdict, never the generic fallback."""
    result = await MockLLMProvider().aparse_json(
        "review this draft", system=system_prompt
    )

    assert "mock" not in result, (
        f"MockLLMProvider does not recognise the {chain!r} reviewer — it fell through "
        f"to the generic fallback. review_and_refine will read approved=None, retry to "
        f"exhaustion, and ship a corrupt artifact on the mock stack. Add a system-prompt "
        f"match for this chain in providers/llm/mock.py."
    )
    assert result.get("approved") is True, (
        f"the {chain!r} reviewer's mock verdict must be an explicit approval "
        f"(approved=True), got {result!r}"
    )


@pytest.mark.asyncio
async def test_mock_recognises_the_outcome_critic_chain():
    """ADR-060 outcome critic (E049 49.6) — not an ADR-021 reviewer (no
    ``approved`` field), but the same defect class killed it once already:
    between 2026-07-30 and 2026-07-31 the critic had NO mock fingerprint,
    fell to ``{"mock": ...}`` on every mock-stack run, and every mock
    ``critic_report`` ended ``judgement_error`` — the advisory path was never
    exercised by IQ/OQ/PQ at all. The mock must answer the critic's response
    shape (``findings``), never the generic fallback."""
    from applire.prompts.outcome_critic import SYSTEM_PROMPT as CRITIC_SYSTEM

    result = await MockLLMProvider().aparse_json(
        "PASS A judgement over an assembled CV", system=CRITIC_SYSTEM
    )
    assert "mock" not in result, (
        "MockLLMProvider does not recognise the outcome critic — it fell "
        "through to the generic fallback; every mock-stack critic_report will "
        "end judgement_error and the advisory surface is never exercised. Add "
        "a system-prompt match in providers/llm/mock.py."
    )
    assert isinstance(result.get("findings"), list)
