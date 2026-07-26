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

"""Wave-6 JD-prompt shape fix (prompt-tuning session, 2026-07-26).

Pinned failure (live provider, Connect-AI posting, .run5fixture/jd_chain.jsonl):
round-1 extraction produced short concept terms ("Embeddings", "RAG pipelines",
"AI evaluation" ...); the reviewer/corrector rounds rewrote them into prose
sentences ("Production experience with RAG, embeddings, ranking and retrieval
pipelines" ...). required_skills/nice_to_have_skills/keywords feed
build_keyword_ledger() (services/keyword_ledger.py), whose entries are matched
against CV/letter text via ats_audit.surface_present — a concept noun matches
real document text, a sentence matches nothing. This is the SHAPE contract fix:
job_analysis.py (extraction) and review_job_analysis.py (reviewer + corrector)
must all agree these three fields are a controlled vocabulary of short concept
terms, never sentences/quotations — and the verbatim-grounding rule added
earlier must not fight that (verbatim presence PROVES grounding for a concept;
absence of a verbatim phrase does NOT disprove it).

No Docker, no DB — pure prompt-wording assertions.
"""

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.prompts.job_analysis import SYSTEM_PROMPT
from applire.prompts.review_job_analysis import (
    JOB_ANALYSIS_REFINEMENT_PROMPT,
    JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# Task 1 — extraction prompt states the field-shape contract
# ---------------------------------------------------------------------------


class TestExtractionPromptFieldShapeContract:
    def test_states_concept_term_shape(self):
        prompt = SYSTEM_PROMPT.lower()
        assert "concept" in prompt
        # 1-4 words guidance must be present somewhere near the shape rule.
        assert "1" in SYSTEM_PROMPT and "4" in SYSTEM_PROMPT

    def test_forbids_sentences_and_quotations(self):
        prompt = SYSTEM_PROMPT.lower()
        assert "sentence" in prompt
        assert "quot" in prompt  # "quotation"/"quoting"/"quoted"

    def test_gives_illustrative_good_bad_pair(self):
        # Role-generic shapes drawn from the pinned failure — not personal data.
        assert "Embeddings" in SYSTEM_PROMPT or "RAG pipelines" in SYSTEM_PROMPT
        # At least one prose-shaped counter-example must also appear so the
        # contrast (good vs bad) is explicit, not just an assertion.
        prompt_lower = SYSTEM_PROMPT.lower()
        assert (
            "production experience with rag" in prompt_lower
            or "hands-on experience with agentic systems" in prompt_lower
            or "building and deploying ai-powered products" in prompt_lower
        )

    def test_notes_downstream_literal_matching(self):
        prompt = SYSTEM_PROMPT.lower()
        # Must explain WHY the shape matters: literal/verbatim matching downstream.
        assert "match" in prompt

    def test_applies_to_all_three_shape_fields(self):
        # The contract must name all three fields it governs.
        for field in ("required_skills", "nice_to_have_skills", "keywords"):
            assert field in SYSTEM_PROMPT

    def test_schema_still_present_and_valid_json_instruction_intact(self):
        # Guardrail: don't regress the existing schema/response-format instructions.
        assert "berufsbild_code" in SYSTEM_PROMPT
        assert "Respond ONLY with a valid JSON object" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Task 2 — reviewer + corrector taught the same contract, reconciled with the
# verbatim-grounding rule
# ---------------------------------------------------------------------------


class TestReviewerShapeContract:
    def test_reviewer_judges_concept_presence_not_verbatim_phrase(self):
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT.lower()
        assert "concept" in prompt

    def test_reviewer_names_the_embeddings_example_as_correct_not_fabricated(self):
        """Pinned example: extracting "Embeddings" from a sentence that only
        contains it as a sub-phrase is correct extraction, not fabrication."""
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT
        assert "Embeddings" in prompt or "embeddings" in prompt.lower()
        prompt_lower = prompt.lower()
        assert "not a fabrication" in prompt_lower or "not fabrication" in prompt_lower

    def test_reviewer_never_asks_to_rewrite_a_concept_into_the_source_sentence(self):
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT.lower()
        assert "rewrite" in prompt or "rewritten" in prompt

    def test_verbatim_proves_but_non_verbatim_does_not_disprove(self):
        """The most important wording in the task: reconcile the verbatim-
        grounding rule (added earlier) with the concept-shape rule (added now)
        so the two never fight over a legitimately-extracted concept term."""
        prompt_lower = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT.lower()
        assert "verbatim" in prompt_lower
        assert "does not disprove" in prompt_lower or "does not mean" in prompt_lower or "is not disproof" in prompt_lower

    def test_corrector_preserves_shape_rule(self):
        prompt = JOB_ANALYSIS_REFINEMENT_PROMPT.lower()
        assert "concept" in prompt
        assert "sentence" in prompt or "quotation" in prompt

    def test_genuine_fabrication_checks_still_present(self):
        """Guardrail: the shape reconciliation must not weaken the existing
        anti-fabrication checks — a term with NO basis anywhere in the source
        must still be flaggable."""
        prompt = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT
        for marker in (
            "FABRICATED REQUIREMENT",
            "MISCLASSIFICATION",
            "FABRICATED KEYWORDS",
            "INVENTED TITLE OR COMPANY",
            "SENIORITY/LANGUAGE OVERREACH",
        ):
            assert marker in prompt

    def test_hallucinated_skill_with_no_source_basis_still_reads_as_flagged(self):
        """A skill absent from the source text entirely (not merely non-verbatim)
        must remain inside the fabrication-check language — the concept-shape
        carve-out only concerns HOW a grounded concept is worded, never WHETHER
        an ungrounded one is allowed through."""
        prompt_lower = JOB_ANALYSIS_REVIEW_SYSTEM_PROMPT.lower()
        assert "fabricated" in prompt_lower
        assert "not stated or clearly" in prompt_lower or "no basis" in prompt_lower or "not mentioned" in prompt_lower
