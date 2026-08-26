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

"""Drift-guard tests for refinement-mode system prompts.

Each refinement prompt MUST:
- Exist as a module-level constant.
- Contain a distinctive lowercase fingerprint that the mock LLM can detect.
- Frame the task as "patch this JSON", not "extract from text".
- Be substantially shorter than its corresponding extraction prompt (target <=1.5 KB).
"""

import asyncio


def test_cv_extraction_refinement_prompt_exists_and_is_distinct():
    from applire.prompts.cv_extraction import (
        CV_EXTRACTION_REFINEMENT_PROMPT,
        GENERIC_CV_EXTRACTION_PROMPT,
    )

    assert isinstance(CV_EXTRACTION_REFINEMENT_PROMPT, str)
    assert "cv profile corrector" in CV_EXTRACTION_REFINEMENT_PROMPT.lower()
    assert "patch" in CV_EXTRACTION_REFINEMENT_PROMPT.lower()
    assert len(CV_EXTRACTION_REFINEMENT_PROMPT) < len(GENERIC_CV_EXTRACTION_PROMPT)
    assert len(CV_EXTRACTION_REFINEMENT_PROMPT) <= 1500


def test_profile_extraction_refinement_prompt_exists_and_is_distinct():
    from applire.prompts.profile_extraction import (
        PROFILE_EXTRACTION_REFINEMENT_PROMPT,
        SYSTEM_PROMPT,
    )

    assert isinstance(PROFILE_EXTRACTION_REFINEMENT_PROMPT, str)
    assert "profile data corrector" in PROFILE_EXTRACTION_REFINEMENT_PROMPT.lower()
    assert "patch" in PROFILE_EXTRACTION_REFINEMENT_PROMPT.lower()
    assert len(PROFILE_EXTRACTION_REFINEMENT_PROMPT) < len(SYSTEM_PROMPT)
    assert len(PROFILE_EXTRACTION_REFINEMENT_PROMPT) <= 1500


def test_cv_tailoring_refinement_prompt_exists_and_is_distinct():
    from applire.prompts.cv_tailoring import (
        CV_TAILORING_REFINEMENT_PROMPT,
        SYSTEM_PROMPT,
    )

    assert isinstance(CV_TAILORING_REFINEMENT_PROMPT, str)
    assert "tailored cv corrector" in CV_TAILORING_REFINEMENT_PROMPT.lower()
    assert "patch" in CV_TAILORING_REFINEMENT_PROMPT.lower()
    assert len(CV_TAILORING_REFINEMENT_PROMPT) < len(SYSTEM_PROMPT)
    # 1500 → 1900 with #580 (ADR-077 amended 2026-08-26): the PINNED FACTS rule —
    # insert a demanded pin verbatim under the named id, keep pins intact, a truth
    # finding wins. Mapped to SF-PIN.2; the ceiling again sits just above the size.
    assert len(CV_TAILORING_REFINEMENT_PROMPT) <= 1900


def test_all_reviewer_prompts_use_referential_critique():
    """US193 / ADR-021 amended: a bounded reviewer must NOT quote source passages
    back (that re-emits bulk text and blows the cap on small-output models). Each
    reviewer system prompt instead critiques *referentially* — naming the offending
    location — and the refiner re-reads the source itself (US194)."""
    from applire.prompts.review_cv_extraction import (
        CV_EXTRACTION_REVIEW_SYSTEM_PROMPT,
    )
    from applire.prompts.review_profile_extraction import (
        REVIEW_SYSTEM_PROMPT as _PROFILE_REVIEW,
    )
    from applire.prompts.review_cv_tailoring import (
        REVIEW_SYSTEM_PROMPT as _TAILORING_REVIEW,
    )

    for name, prompt in [
        ("review_cv_extraction", CV_EXTRACTION_REVIEW_SYSTEM_PROMPT),
        ("review_profile_extraction", _PROFILE_REVIEW),
        ("review_cv_tailoring", _TAILORING_REVIEW),
    ]:
        assert "referential" in prompt.lower(), f"{name} missing referential-critique rule"
        # The superseded verbatim-quote rule must be gone.
        assert "quote the relevant source passages" not in prompt.lower(), (
            f"{name} still carries the removed quote-source rule"
        )


def test_mock_returns_schema_valid_response_for_each_refinement_prompt():
    """The mock must recognise every refinement prompt fingerprint and return
    a deterministic dict — never the generic fallback. This keeps the review
    retry loop terminating cleanly in CI."""
    from applire.prompts.cv_extraction import CV_EXTRACTION_REFINEMENT_PROMPT
    from applire.prompts.profile_extraction import PROFILE_EXTRACTION_REFINEMENT_PROMPT
    from applire.prompts.cv_tailoring import CV_TAILORING_REFINEMENT_PROMPT
    from applire.providers.llm.mock import MockLLMProvider

    provider = MockLLMProvider()

    async def call_all() -> list[dict]:
        return [
            await provider.aparse_json("patch this", system=p)
            for p in (
                CV_EXTRACTION_REFINEMENT_PROMPT,
                PROFILE_EXTRACTION_REFINEMENT_PROMPT,
                CV_TAILORING_REFINEMENT_PROMPT,
            )
        ]

    results = asyncio.run(call_all())
    for r in results:
        assert isinstance(r, dict)
        # Generic fallback returns {"mock": True, "raw_prompt_length": N}.
        # Real fingerprint matches return schema-shaped data without "mock".
        assert "mock" not in r, f"Refinement prompt hit generic fallback: {r}"
