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

"""
US144 — grounding regression corpus, REAL-LLM tier (JF-M-3.1 / 6.1 / 6.2).

Asserts the ADR-021 judges actually REJECT each known fabrication. This is the
tier that mock-LLM CI structurally cannot cover (an LLM-judge can only be
validated against a real LLM). Run:

    INTEGRATION_LLM=1 pytest tests/integration/test_grounding_corpus_llm.py -v
"""
import json
import os
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))
_fixtures = Path(__file__).parent.parent / "fixtures"
if str(_fixtures) not in sys.path:
    sys.path.insert(0, str(_fixtures))

from grounding_corpus import (  # noqa: E402
    EXTRACTION_CASES,
    TAILORING_CASES,
    LEGITIMATE_EXTRACTION_CASES,
    MISATTRIBUTION_EXTRACTION_CASES,
    LEGITIMATE_PROJECT_CASES,
    REJECT_PROJECT_CASES,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_LLM"),
    reason="Real-LLM test — set INTEGRATION_LLM=1 to run",
)


async def _judge(system: str, user: str) -> dict:
    from applire.providers.llm import get_provider

    provider = get_provider()
    return await provider.aparse_json(user, system=system, temperature=0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", EXTRACTION_CASES, ids=lambda c: c["id"])
async def test_extraction_judge_rejects_fabrication(case):
    from applire.prompts.review_cv_extraction import (
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        build_cv_extraction_review_prompt,
    )

    review = await _judge(
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        build_cv_extraction_review_prompt(case["source"], case["draft"]),
    )
    assert review.get("approved") is False, (
        f"{case['id']}: judge approved a known fabrication ({case['why']}). "
        f"issues={review.get('issues')}"
    )
    assert review.get("issues"), f"{case['id']}: rejected but gave no issues"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", LEGITIMATE_EXTRACTION_CASES, ids=lambda c: c["id"])
async def test_extraction_judge_approves_legitimate_paraphrase(case):
    """US171 false-positive guard — the recalibrated judge must NOT reject a faithful
    paraphrase / sentence-split / merge. This is the regression the recalibration exists
    to prevent (verbatim matching exhausted retries on exactly this shape)."""
    from applire.prompts.review_cv_extraction import (
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        build_cv_extraction_review_prompt,
    )

    review = await _judge(
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        build_cv_extraction_review_prompt(case["source"], case["draft"]),
    )
    assert review.get("approved") is True, (
        f"{case['id']}: judge over-flagged a legitimate paraphrase ({case['why']}). "
        f"issues={review.get('issues')}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", MISATTRIBUTION_EXTRACTION_CASES, ids=lambda c: c["id"])
async def test_extraction_judge_rejects_cross_role_misattribution(case):
    """US171 priority check A — content that is real but attached to the wrong employer/role
    must be rejected, even though it is not a pure fabrication."""
    from applire.prompts.review_cv_extraction import (
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        build_cv_extraction_review_prompt,
    )

    review = await _judge(
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        build_cv_extraction_review_prompt(case["source"], case["draft"]),
    )
    assert review.get("approved") is False, (
        f"{case['id']}: judge missed a cross-role misattribution ({case['why']}). "
        f"issues={review.get('issues')}"
    )
    assert review.get("issues"), f"{case['id']}: rejected but gave no issues"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", TAILORING_CASES, ids=lambda c: c["id"])
async def test_tailoring_judge_rejects_fabrication(case):
    from applire.prompts.review_cv_tailoring import (
        REVIEW_SYSTEM_PROMPT,
        build_review_prompt,
    )

    source_material = json.dumps(case["source"], ensure_ascii=False, indent=2)
    review = await _judge(REVIEW_SYSTEM_PROMPT, build_review_prompt(source_material, case["draft"]))
    assert review.get("approved") is False, (
        f"{case['id']}: judge approved a known fabrication ({case['why']}). "
        f"issues={review.get('issues')}"
    )
    assert review.get("issues"), f"{case['id']}: rejected but gave no issues"


# ── US172 projects-block real-LLM assertions ───────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("case", LEGITIMATE_PROJECT_CASES, ids=lambda c: c["id"])
async def test_extraction_judge_approves_standalone_project(case):
    """US172 false-positive guard — a standalone personal project with no employer and a
    faithful paraphrase must be APPROVED.  The projects clause explicitly states that the
    absence of an employer is not a reason to flag the entry."""
    from applire.prompts.review_cv_extraction import (
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        build_cv_extraction_review_prompt,
    )

    review = await _judge(
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        build_cv_extraction_review_prompt(case["source"], case["draft"]),
    )
    assert review.get("approved") is True, (
        f"{case['id']}: judge over-flagged a legitimate standalone project ({case['why']}). "
        f"issues={review.get('issues')}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", REJECT_PROJECT_CASES, ids=lambda c: c["id"])
async def test_extraction_judge_rejects_project_with_invented_date(case):
    """US172 anti-fabrication guard — a project with an invented date (absent from source)
    must be REJECTED by the projects clause's date-null rule."""
    from applire.prompts.review_cv_extraction import (
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        build_cv_extraction_review_prompt,
    )

    review = await _judge(
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
        build_cv_extraction_review_prompt(case["source"], case["draft"]),
    )
    assert review.get("approved") is False, (
        f"{case['id']}: judge missed a project with an invented date ({case['why']}). "
        f"issues={review.get('issues')}"
    )
    assert review.get("issues"), f"{case['id']}: rejected but gave no issues"
