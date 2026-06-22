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

from grounding_corpus import (  # noqa: E402
    EXTRACTION_CASES,
    TAILORING_CASES,
    LEGITIMATE_EXTRACTION_CASES,
    MISATTRIBUTION_EXTRACTION_CASES,
    LEGITIMATE_PROJECT_CASES,
    REJECT_PROJECT_CASES,
)
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


class TestLegitimateExtractionCorpus:
    """US171 — the false-positive guard. These drafts faithfully paraphrase / split /
    merge the source and invent NOTHING, so the recalibrated reviewer MUST approve them.
    (The real-LLM `approved is True` assertion lives in test_grounding_corpus_llm.py.)"""

    def test_corpus_is_non_empty(self):
        assert len(LEGITIMATE_EXTRACTION_CASES) >= 1

    @pytest.mark.parametrize("case", LEGITIMATE_EXTRACTION_CASES, ids=lambda c: c["id"])
    def test_source_anchor_is_genuinely_in_source(self, case):
        assert case["source_anchor"].lower() in case["source"].lower()

    @pytest.mark.parametrize("case", LEGITIMATE_EXTRACTION_CASES, ids=lambda c: c["id"])
    def test_paraphrase_is_not_verbatim_but_is_in_draft(self, case):
        # the token genuinely exercises paraphrase tolerance: it is NOT a verbatim copy
        # of the source (that is what used to trip the verbatim matcher) yet appears in the draft
        assert case["paraphrase_token"].lower() not in case["source"].lower(), (
            f"{case['id']}: paraphrase token is verbatim in source — does not exercise the recalibration"
        )
        draft = json.dumps(case["draft"], ensure_ascii=False)
        assert case["paraphrase_token"] in draft

    @pytest.mark.parametrize("case", LEGITIMATE_EXTRACTION_CASES, ids=lambda c: c["id"])
    def test_prompt_embeds_source_and_paraphrased_draft(self, case):
        prompt = build_cv_extraction_review_prompt(case["source"], case["draft"])
        assert case["source_anchor"] in prompt          # truthful source is present
        assert case["paraphrase_token"] in prompt        # the paraphrase the judge must accept


class TestMisattributionCorpus:
    """US171 — cross-role misattribution. The moved content IS in the source (so it is not a
    fabrication) but is attached to the WRONG employer/role, which the reviewer MUST reject."""

    def test_corpus_is_non_empty(self):
        assert len(MISATTRIBUTION_EXTRACTION_CASES) >= 1

    @pytest.mark.parametrize("case", MISATTRIBUTION_EXTRACTION_CASES, ids=lambda c: c["id"])
    def test_misattributed_content_is_present_in_source(self, case):
        # distinguishes this from a pure fabrication: the content genuinely exists in the source
        assert case["misattributed_content"].lower() in case["source"].lower()

    @pytest.mark.parametrize("case", MISATTRIBUTION_EXTRACTION_CASES, ids=lambda c: c["id"])
    def test_draft_places_content_under_wrong_employer(self, case):
        wrong = next(
            e for e in case["draft"]["work_experience"] if e["company"] == case["wrong_employer"]
        )
        flat = json.dumps(wrong, ensure_ascii=False)
        assert case["misattributed_content"] in flat, (
            f"{case['id']}: misattributed content not found under the wrong employer entry"
        )

    @pytest.mark.parametrize("case", MISATTRIBUTION_EXTRACTION_CASES, ids=lambda c: c["id"])
    def test_prompt_embeds_source_and_misattributed_content(self, case):
        prompt = build_cv_extraction_review_prompt(case["source"], case["draft"])
        assert case["source_anchor"] in prompt
        assert case["misattributed_content"] in prompt


class TestLegitimateProjectCorpus:
    """US172 (ADR-044) — false-positive guard for the projects block.

    A standalone personal project with no employer field must NOT be flagged as
    a shell/fabricated/empty entry by the recalibrated reviewer.  The real-LLM
    `approved is True` assertion lives in test_grounding_corpus_llm.py."""

    def test_corpus_is_non_empty(self):
        assert len(LEGITIMATE_PROJECT_CASES) >= 1

    @pytest.mark.parametrize("case", LEGITIMATE_PROJECT_CASES, ids=lambda c: c["id"])
    def test_source_anchor_is_genuinely_in_source(self, case):
        assert case["source_anchor"].lower() in case["source"].lower()

    @pytest.mark.parametrize("case", LEGITIMATE_PROJECT_CASES, ids=lambda c: c["id"])
    def test_paraphrase_not_verbatim_but_in_draft(self, case):
        assert case["paraphrase_token"].lower() not in case["source"].lower(), (
            f"{case['id']}: paraphrase token is verbatim in source — does not exercise tolerance"
        )
        draft = json.dumps(case["draft"], ensure_ascii=False)
        assert case["paraphrase_token"] in draft

    @pytest.mark.parametrize("case", LEGITIMATE_PROJECT_CASES, ids=lambda c: c["id"])
    def test_project_entry_has_no_employer(self, case):
        # guards the corpus itself: these cases genuinely stand alone (no parent experience)
        for entry in case["draft"].get("projects", []):
            assert entry.get("associated_experience") is None, (
                f"{case['id']}: project links to a parent experience — does not test the standalone path"
            )

    @pytest.mark.parametrize("case", LEGITIMATE_PROJECT_CASES, ids=lambda c: c["id"])
    def test_prompt_embeds_source_and_paraphrased_draft(self, case):
        prompt = build_cv_extraction_review_prompt(case["source"], case["draft"])
        assert case["source_anchor"] in prompt
        assert case["paraphrase_token"] in prompt


class TestRejectProjectCorpus:
    """US172 (ADR-044) — must-REJECT cases for the projects block.

    A project with an invented date (absent from the source) must be rejected.
    The real-LLM `approved is False` assertion lives in test_grounding_corpus_llm.py."""

    def test_corpus_is_non_empty(self):
        assert len(REJECT_PROJECT_CASES) >= 1

    @pytest.mark.parametrize("case", REJECT_PROJECT_CASES, ids=lambda c: c["id"])
    def test_fabricated_token_absent_from_source(self, case):
        assert case["fabricated_token"].lower() not in case["source"].lower(), (
            f"{case['id']}: fabricated token must be ungrounded (absent from source)"
        )

    @pytest.mark.parametrize("case", REJECT_PROJECT_CASES, ids=lambda c: c["id"])
    def test_fabricated_token_present_in_draft(self, case):
        draft = json.dumps(case["draft"], ensure_ascii=False)
        assert case["fabricated_token"] in draft, (
            f"{case['id']}: fabricated token must be present in draft"
        )

    @pytest.mark.parametrize("case", REJECT_PROJECT_CASES, ids=lambda c: c["id"])
    def test_prompt_embeds_source_and_fabrication(self, case):
        prompt = build_cv_extraction_review_prompt(case["source"], case["draft"])
        assert case["source_anchor"] in prompt
        assert case["fabricated_token"] in prompt
