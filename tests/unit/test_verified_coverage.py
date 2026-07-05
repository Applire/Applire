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

"""US213 (#122, ADR-048 amended 2026-07-04): the deterministic claimable-coverage
check feeds the existing ADR-047 refine loop.

The LLM reviewer no longer *detects* coverage (US202 detection retired) — before
each reviewer call the pipeline runs the shared presence predicate (US212) over
the serialised draft and injects the verifiably-absent claimable entries as
ground truth. The reviewer's only coverage role is arbitrating grounding
waivers: reject while the verified list is non-empty, unless surfacing a term
would stretch beyond its ledger evidence.
"""

from unittest.mock import AsyncMock

import pytest

from applire.services.keyword_ledger import (
    coverage_reviewer_prompt_fn,
    render_verified_coverage_block,
    verified_missing_claimable,
)
from applire.services.reviewer import review_and_refine

_LEDGER = [
    {"concept": "education technology", "surface_forms": ["EdTech"], "claimable": True,
     "status": "partial", "sources": ["keyword"], "fit_weight": 0.0,
     "evidence": "educational games development at Provadis"},
    {"concept": "code review practices", "surface_forms": ["Code reviews"], "claimable": True,
     "status": "direct", "sources": ["keyword"], "fit_weight": 0.0,
     "evidence": "enforced code review standards"},
    {"concept": "SaaS", "surface_forms": ["SaaS"], "claimable": False,
     "status": "gap", "sources": ["keyword"], "fit_weight": 0.0, "evidence": ""},
]

_DRAFT_WITHOUT_EDTECH = {
    "summary": "Senior IT leader enforcing code review standards.",
    "work_history": [
        {"company": "Provadis", "role": "Apprentice",
         "bullets": ["Developed educational games using Flash"]},
    ],
    "skills": ["Technical Leadership"],
}

_DRAFT_WITH_EDTECH = {
    "summary": "Senior IT leader with EdTech experience, enforcing code review standards.",
    "work_history": [
        {"company": "Provadis", "role": "Apprentice",
         "bullets": ["Developed educational games (EdTech) using Flash"]},
    ],
    "skills": ["Technical Leadership"],
}


class TestVerifiedMissingClaimable:
    def test_absent_claimable_entry_reported(self):
        """#122 'EdTech': claimable, evidence-adjacent prose in the draft, but the
        literal (and every surface form) absent → verified miss."""
        missing = verified_missing_claimable(_DRAFT_WITHOUT_EDTECH, _LEDGER)
        assert [e["concept"] for e in missing] == ["education technology"]

    def test_present_via_fold_not_reported(self):
        """'Code reviews' (plural form) is covered by 'code review standards' via the
        US212 morphological fold — never a verified miss."""
        missing = verified_missing_claimable(_DRAFT_WITHOUT_EDTECH, _LEDGER)
        assert "code review practices" not in [e["concept"] for e in missing]

    def test_honest_gap_never_reported(self):
        missing = verified_missing_claimable({"summary": "empty"}, _LEDGER)
        assert "SaaS" not in [e["concept"] for e in missing]

    def test_no_ledger_returns_empty(self):
        assert verified_missing_claimable(_DRAFT_WITHOUT_EDTECH, None) == []
        assert verified_missing_claimable(_DRAFT_WITHOUT_EDTECH, []) == []

    def test_nested_draft_strings_are_searched(self):
        """Coverage looks at every string in the draft, however deeply nested."""
        draft = {"a": {"b": [{"c": ["deep EdTech mention"]}]}}
        missing = verified_missing_claimable(draft, _LEDGER)
        assert "education technology" not in [e["concept"] for e in missing]


class TestRenderVerifiedCoverageBlock:
    def test_empty_for_no_misses(self):
        assert render_verified_coverage_block([]) == ""

    def test_block_names_terms_evidence_and_waiver_rule(self):
        block = render_verified_coverage_block(
            verified_missing_claimable(_DRAFT_WITHOUT_EDTECH, _LEDGER)
        )
        low = block.lower()
        assert "edtech" in low
        assert "educational games" in low          # evidence rides along for arbitration
        assert "verified" in low                   # deterministic, not an LLM guess
        assert "waive" in low                      # the reviewer's only coverage judgment
        assert "reject" in low or "approved" in low


class TestCoverageReviewerPromptFn:
    def test_appends_block_when_verified_miss_exists(self):
        base = lambda source, draft: f"BASE[{source}]"
        fn = coverage_reviewer_prompt_fn(base, _LEDGER)
        prompt = fn("src", _DRAFT_WITHOUT_EDTECH)
        assert prompt.startswith("BASE[src]")
        assert "VERIFIED" in prompt and "EdTech" in prompt

    def test_prompt_unchanged_when_all_claimable_covered(self):
        base = lambda source, draft: f"BASE[{source}]"
        fn = coverage_reviewer_prompt_fn(base, _LEDGER)
        assert fn("src", _DRAFT_WITH_EDTECH) == "BASE[src]"

    def test_prompt_unchanged_without_ledger(self):
        base = lambda source, draft: "BASE"
        assert coverage_reviewer_prompt_fn(base, None)("s", _DRAFT_WITHOUT_EDTECH) == "BASE"

    def test_recomputed_per_draft(self):
        """The verified list follows the CURRENT draft — after a refine pass that
        surfaces the term, the block disappears (loop convergence signal)."""
        base = lambda source, draft: "BASE"
        fn = coverage_reviewer_prompt_fn(base, _LEDGER)
        assert "VERIFIED" in fn("s", _DRAFT_WITHOUT_EDTECH)
        assert fn("s", _DRAFT_WITH_EDTECH) == "BASE"


class TestLoopIntegration:
    @pytest.mark.asyncio
    async def test_verified_miss_rejected_then_converges(self):
        """Full loop pass: reviewer sees the verified block, rejects; the refiner
        surfaces the term; the next reviewer prompt carries no block and approves."""
        provider = AsyncMock()
        provider.aparse_json.side_effect = [
            {"approved": False, "issues": ["surface EdTech"], "feedback": "surface EdTech"},
            _DRAFT_WITH_EDTECH,                                  # refiner output
            {"approved": True, "issues": [], "feedback": ""},
        ]
        reviewer_prompts: list[str] = []
        base = lambda source, draft: f"review source={source}"

        def capturing_fn(source, draft):
            p = coverage_reviewer_prompt_fn(base, _LEDGER)(source, draft)
            reviewer_prompts.append(p)
            return p

        result = await review_and_refine(
            source="src",
            draft=_DRAFT_WITHOUT_EDTECH,
            generator_prompt_fn=lambda draft, feedback, source: "retry",
            generator_system="gen-sys",
            reviewer_prompt_fn=capturing_fn,
            reviewer_system="rev-sys",
            provider=provider,
            max_retries=2,
        )
        assert result == _DRAFT_WITH_EDTECH
        assert "VERIFIED" in reviewer_prompts[0] and "EdTech" in reviewer_prompts[0]
        assert "VERIFIED" not in reviewer_prompts[1]


class TestReviewerSystemPromptsArbitrateNotDetect:
    """US213: detection retires from the LLM prompts; both reviewers are instructed
    to act on the VERIFIED coverage block and may only waive on grounding."""

    def test_cv_reviewer_system_prompt(self):
        from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "verified" in low
        assert "waive" in low

    def test_cover_letter_reviewer_system_prompt(self):
        from applire.prompts.review_cover_letter import REVIEW_SYSTEM_PROMPT as p
        low = p.lower()
        assert "verified" in low
        assert "waive" in low


# --- #122 follow-up (UAT 2026-07-04): the language pass is the last writer ---------
#
# The cv_language chain runs AFTER the gated tailoring loop and rewrites wording
# (translation). It regressed coverage: "efficiency improvement" (a surface form)
# was translated to "Effizienzsteigerung", which matches no surface form — the
# tailoring gate had passed, the panel then honestly reported the term missing.
# Fix: the same coverage wrapper feeds the language reviewer, whose boundary is
# WORD CHOICE — use the exact required-language surface form when rewording, never
# insert new content.

_LEDGER_DE = [
    {"concept": "Produktivitätsgewinne", "claimable": True, "status": "direct",
     "sources": ["keyword"], "fit_weight": 0.0,
     "surface_forms": ["Produktivitätsgewinne", "Productivity Gains", "Efficiency Improvements"],
     "evidence": "60% efficiency improvement in AI automation project"},
]

_DRAFT_DE_SYNONYM = {
    "summary": "IT-Führungskraft mit Fokus auf Effizienzsteigerung.",
    "work_history": [
        {"company": "BioNTech SE", "role": "Associate Director",
         "bullets": ["KI-Projekt zur Effizienzsteigerung der Dokumentenerstellung"]},
    ],
    "skills": ["Produktivitätssteigerung"],
}

_DRAFT_DE_EXACT = {
    "summary": "IT-Führungskraft mit Fokus auf Produktivitätsgewinne.",
    "work_history": [
        {"company": "BioNTech SE", "role": "Associate Director",
         "bullets": ["KI-Projekt zur Effizienzsteigerung der Dokumentenerstellung"]},
    ],
    "skills": ["Produktivitätsgewinne"],
}


class TestLanguagePassCoverage:
    @pytest.mark.asyncio
    async def test_language_review_prompt_carries_verified_block(self):
        """The language reviewer sees the deterministic coverage state of the draft
        it is reviewing — the German synonym does not satisfy the surface forms."""
        from applire.services.cv import _review_cv_language

        provider = AsyncMock()
        provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}
        await _review_cv_language(
            _DRAFT_DE_SYNONYM, "de", provider, keyword_ledger=_LEDGER_DE
        )
        prompt = provider.aparse_json.call_args_list[0].args[0]
        assert "VERIFIED COVERAGE CHECK" in prompt
        assert "Produktivitätsgewinne" in prompt

    @pytest.mark.asyncio
    async def test_language_review_without_ledger_has_no_block(self):
        from applire.services.cv import _review_cv_language

        provider = AsyncMock()
        provider.aparse_json.return_value = {"approved": True, "issues": [], "feedback": ""}
        await _review_cv_language(_DRAFT_DE_SYNONYM, "de", provider)
        prompt = provider.aparse_json.call_args_list[0].args[0]
        assert "VERIFIED COVERAGE CHECK" not in prompt

    @pytest.mark.asyncio
    async def test_language_loop_recovers_dropped_keyword(self):
        """Reject → reword with the exact surface form; the refined draft satisfies
        the same predicate the panel grades with (CV_LANGUAGE_REVIEW_MAX_RETRIES=1,
        so the loop returns the refined draft — the predicate is the arbiter)."""
        from applire.services.cv import _review_cv_language

        provider = AsyncMock()
        provider.aparse_json.side_effect = [
            {"approved": False, "issues": ["use 'Produktivitätsgewinne'"],
             "feedback": "reword the efficiency skill to the exact JD term"},
            _DRAFT_DE_EXACT,
            {"approved": True, "issues": [], "feedback": ""},
        ]
        result = await _review_cv_language(
            _DRAFT_DE_SYNONYM, "de", provider, keyword_ledger=_LEDGER_DE
        )
        assert result == _DRAFT_DE_EXACT
        assert verified_missing_claimable(result, _LEDGER_DE) == []
        first_review_prompt = provider.aparse_json.call_args_list[0].args[0]
        assert "VERIFIED COVERAGE CHECK" in first_review_prompt

    @pytest.mark.asyncio
    async def test_language_pass_rereviews_post_translation_draft(self):
        """The UAT failure shape: the FIRST review sees a draft still covered via an
        English surface form (no block); the refiner then translates that form away.
        The gate is only effective if the translated draft is re-reviewed — the
        second review prompt must carry the recomputed VERIFIED block (requires
        CV_LANGUAGE_REVIEW_MAX_RETRIES >= 2; a lone refine ships unreviewed)."""
        from applire.services.cv import _review_cv_language

        draft_en_covered = {
            "summary": "IT leader focused on efficiency improvements.",
            "work_history": [
                {"company": "BioNTech SE", "role": "Associate Director",
                 "bullets": ["AI project delivering a 60% efficiency improvement"]},
            ],
            "skills": ["Productivity Gains"],
        }
        provider = AsyncMock()
        provider.aparse_json.side_effect = [
            {"approved": False, "issues": ["translate 'Productivity Gains'"],
             "feedback": "translate skills into German"},
            _DRAFT_DE_SYNONYM,          # refine 1: translation drops every surface form
            {"approved": False, "issues": ["use 'Produktivitätsgewinne'"],
             "feedback": "use the exact JD term"},
            _DRAFT_DE_EXACT,            # refine 2: exact surface form restored
        ]
        result = await _review_cv_language(
            draft_en_covered, "de", provider, keyword_ledger=_LEDGER_DE
        )
        first_prompt = provider.aparse_json.call_args_list[0].args[0]
        second_review_prompt = provider.aparse_json.call_args_list[2].args[0]
        assert "VERIFIED COVERAGE CHECK" not in first_prompt
        assert "VERIFIED COVERAGE CHECK" in second_review_prompt
        assert result == _DRAFT_DE_EXACT
        assert verified_missing_claimable(result, _LEDGER_DE) == []

    def test_language_system_prompts_define_coverage_boundary(self):
        """The reviewer keeps the mock-keying phrase and both prompts carry the
        word-choice boundary: exact surface forms, no invented content."""
        from applire.prompts.review_cv_language import (
            CV_LANGUAGE_REFINEMENT_PROMPT,
            CV_LANGUAGE_REVIEW_SYSTEM_PROMPT,
        )

        low = CV_LANGUAGE_REVIEW_SYSTEM_PROMPT.lower()
        assert "language reviewer" in low  # MockLLMProvider keys this chain off it
        assert "verified coverage check" in low
        assert "waive" in low
        assert "exact" in CV_LANGUAGE_REFINEMENT_PROMPT.lower()
