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
US144 — grounding/hallucination regression corpus (CI tier, no LLM).

The corpus is a labelled set of fabrication cases (JF-M-3.1 extraction, JF-M-6.1/6.2
tailoring). These deterministic tests guard two invariants without calling an LLM:

1. Each case is genuinely a fabrication — the `fabricated_token` is present in the
   draft but ABSENT from the truthful source. (Validates the corpus itself.)
2. The reviewer prompt builder hands the judge BOTH the source and the fabricated
   claim — the necessary condition for the judge to catch it. (Guards US142 prompt
   edits and future refactors from silently dropping source or draft.)

The real-LLM assertion that the judge actually rejects each case lives in
`tests/integration/test_grounding_corpus_llm.py` (INTEGRATION_LLM=1).
"""
import json
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))
_fixtures = Path(__file__).parent.parent / "fixtures"
if str(_fixtures) not in sys.path:
    sys.path.insert(0, str(_fixtures))

from grounding_corpus import EXTRACTION_CASES, TAILORING_CASES  # noqa: E402
from applire.prompts.review_cv_extraction import build_cv_extraction_review_prompt  # noqa: E402
from applire.prompts.review_cv_tailoring import build_review_prompt as build_tailoring_review_prompt  # noqa: E402


class TestCorpusIsWellFormed:
    def test_corpus_is_non_empty(self):
        assert len(EXTRACTION_CASES) >= 3
        assert len(TAILORING_CASES) >= 3

    @pytest.mark.parametrize("case", EXTRACTION_CASES, ids=lambda c: c["id"])
    def test_extraction_case_token_absent_from_source(self, case):
        assert case["fabricated_token"].lower() not in case["source"].lower(), (
            f"{case['id']}: fabricated token must be ungrounded (absent from source)"
        )

    @pytest.mark.parametrize("case", TAILORING_CASES, ids=lambda c: c["id"])
    def test_tailoring_case_token_absent_from_source(self, case):
        src = json.dumps(case["source"], ensure_ascii=False)
        assert case["fabricated_token"].lower() not in src.lower(), (
            f"{case['id']}: fabricated token must be ungrounded (absent from source)"
        )


class TestReviewerPromptHandsJudgeBothSides:
    @pytest.mark.parametrize("case", EXTRACTION_CASES, ids=lambda c: c["id"])
    def test_extraction_prompt_embeds_source_and_fabrication(self, case):
        prompt = build_cv_extraction_review_prompt(case["source"], case["draft"])
        assert case["source_anchor"] in prompt          # truthful source is present
        assert case["fabricated_token"] in prompt        # the claim to be caught is present

    @pytest.mark.parametrize("case", TAILORING_CASES, ids=lambda c: c["id"])
    def test_tailoring_prompt_embeds_source_and_fabrication(self, case):
        source_material = json.dumps(case["source"], ensure_ascii=False, indent=2)
        prompt = build_tailoring_review_prompt(source_material, case["draft"])
        assert case["source_anchor"] in prompt
        assert case["fabricated_token"] in prompt
