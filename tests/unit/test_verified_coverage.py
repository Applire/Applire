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
        {"company": "NordPharm SE", "role": "Associate Director",
         "bullets": ["KI-Projekt zur Effizienzsteigerung der Dokumentenerstellung"]},
    ],
    "skills": ["Produktivitätssteigerung"],
}

_DRAFT_DE_EXACT = {
    "summary": "IT-Führungskraft mit Fokus auf Produktivitätsgewinne.",
    "work_history": [
        {"company": "NordPharm SE", "role": "Associate Director",
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
                {"company": "NordPharm SE", "role": "Associate Director",
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


# --- ADR-076 clause 6 (#543): coverage yields to rank under the length budget -----
#
# Run A of the 2026-08-14 model comparison (letter chain, real provider): the
# coverage check demanded two NEW claimable keywords every round while the
# corrector's insertions displaced earlier ones (SAP+Shopfloor -> 5S+Arbeits-
# sicherheit -> Arbeitsvorbereitung+Fuehrungserfahrung -> Budgetplanung+Supply
# Chain), exhausting 5/5. The fix: the coverage demand reads the ledger's own
# fit_weight (required=1.0 > nice_to_have=0.5 > keyword-only=0.0, ADR-048 §1)
# under the ADR-042/051 length budget — a below-rank absence stops being
# raised as blocking once the draft has reached its budget.

_LEDGER_RANKED = [
    {"concept": "Required Thing", "surface_forms": ["Required Thing"], "claimable": True,
     "status": "direct", "sources": ["required"], "fit_weight": 1.0,
     "evidence": "led the Required Thing initiative"},
    {"concept": "Nice Thing", "surface_forms": ["Nice Thing"], "claimable": True,
     "status": "direct", "sources": ["nice_to_have"], "fit_weight": 0.5,
     "evidence": "worked with Nice Thing"},
    {"concept": "Keyword Thing", "surface_forms": ["Keyword Thing"], "claimable": True,
     "status": "direct", "sources": ["keyword"], "fit_weight": 0.0,
     "evidence": "familiar with Keyword Thing"},
]


def _cv_draft(n_bullets: int) -> dict:
    """A CV draft whose work-history narrative carries exactly ``n_bullets``
    bullets and none of the ranked ledger's terms — every entry is a verified
    miss, and occupancy is precisely controlled for the pressure test."""
    return {
        "summary": "Experienced professional.",
        "work_history": [
            {"company": "ACME", "role": "Engineer",
             "bullets": [f"Delivered outcome {i}." for i in range(n_bullets)]},
        ],
        "skills": [],
    }


def _letter_draft(n_words: int) -> dict:
    """A letter draft whose body carries exactly ``n_words`` words and none of
    the ranked ledger's terms."""
    return {"body": {"paragraphs": [" ".join(["word"] * n_words)]}}


class TestCoverageBudget:
    def test_under_pressure_once_occupancy_reaches_capacity(self):
        from applire.services.keyword_ledger import CoverageBudget

        budget = CoverageBudget(capacity=2, measure=lambda d: d["n"])
        assert budget.under_pressure({"n": 2}) is True
        assert budget.under_pressure({"n": 3}) is True
        assert budget.under_pressure({"n": 1}) is False

    def test_zero_or_negative_capacity_is_always_under_pressure(self):
        from applire.services.keyword_ledger import CoverageBudget

        budget = CoverageBudget(capacity=0, measure=lambda d: 0)
        assert budget.under_pressure({}) is True


class TestCvCoverageBudget:
    def test_none_budget_returns_none(self):
        from applire.services.keyword_ledger import cv_coverage_budget

        assert cv_coverage_budget(None) is None

    def test_capacity_is_sum_of_role_ceilings(self):
        from applire.services.cv_budget import BudgetResult, RoleBudget
        from applire.services.keyword_ledger import cv_coverage_budget

        budget_result = BudgetResult(
            roles={
                "w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=3),
                "w2": RoleBudget(work_entry_id="w2", tier="mid", max_bullets=2),
            },
            tiers={},
            target_pages=2,
            region="DACH",
        )
        cb = cv_coverage_budget(budget_result)
        assert cb.capacity == 5
        assert cb.measure(_cv_draft(2)) == 2
        assert cb.measure(_cv_draft(0)) == 0


class TestLetterCoverageBudget:
    def test_none_or_zero_word_budget_returns_none(self):
        from applire.services.keyword_ledger import letter_coverage_budget

        assert letter_coverage_budget(None) is None
        assert letter_coverage_budget(0) is None

    def test_capacity_and_measure_match_word_budget(self):
        from applire.services.keyword_ledger import letter_coverage_budget

        cb = letter_coverage_budget(300)
        assert cb.capacity == 300
        assert cb.measure(_letter_draft(10)) == 10


class TestRankGateMissingClaimable:
    def test_no_budget_everything_stays_blocking(self):
        """Legacy callers (budget=None) get EXACTLY today's behaviour — the
        gate can only ever make a demand LESS aggressive, never more."""
        from applire.services.keyword_ledger import (
            rank_gate_missing_claimable,
            verified_missing_claimable,
        )

        draft = _cv_draft(5)  # would be "under pressure" against a tight budget
        missing = verified_missing_claimable(draft, _LEDGER_RANKED)
        blocking, below_rank = rank_gate_missing_claimable(missing, draft, None)
        assert {e["concept"] for e in blocking} == {
            "Required Thing", "Nice Thing", "Keyword Thing",
        }
        assert below_rank == []

    def test_no_pressure_everything_stays_blocking(self):
        """Room left in the budget: no rank-gating fires (today's behaviour)."""
        from applire.services.cv_budget import BudgetResult, RoleBudget
        from applire.services.keyword_ledger import (
            cv_coverage_budget,
            rank_gate_missing_claimable,
            verified_missing_claimable,
        )

        budget_result = BudgetResult(
            roles={"w1": RoleBudget(work_entry_id="w1", tier="top", max_bullets=5)},
            tiers={}, target_pages=2, region="DACH",
        )
        cb = cv_coverage_budget(budget_result)
        draft = _cv_draft(1)  # 1 of 5 bullet slots used — plenty of room
        missing = verified_missing_claimable(draft, _LEDGER_RANKED)
        blocking, below_rank = rank_gate_missing_claimable(missing, draft, cb)
        assert len(blocking) == 3
        assert below_rank == []

    def test_under_pressure_below_rank_yields_required_still_blocks(self):
        """THE displacement-churn case (#543): budget tight, low-fit_weight
        concepts absent -> NOT blocking; high-fit_weight (required) absence ->
        STILL blocking. This is the whole point of the issue."""
        from applire.services.cv_budget import BudgetResult, RoleBudget
        from applire.services.keyword_ledger import (
            cv_coverage_budget,
            rank_gate_missing_claimable,
            verified_missing_claimable,
        )

        budget_result = BudgetResult(
            roles={"w1": RoleBudget(work_entry_id="w1", tier="bottom", max_bullets=2)},
            tiers={}, target_pages=2, region="DACH",
        )
        cb = cv_coverage_budget(budget_result)
        draft = _cv_draft(2)  # occupancy == capacity -> under pressure
        missing = verified_missing_claimable(draft, _LEDGER_RANKED)
        blocking, below_rank = rank_gate_missing_claimable(missing, draft, cb)
        assert [e["concept"] for e in blocking] == ["Required Thing"]
        assert {e["concept"] for e in below_rank} == {"Nice Thing", "Keyword Thing"}

    def test_letter_under_pressure_same_split(self):
        """The letter's own budget unit (words) drives the identical rank
        split — one mechanism, two callers."""
        from applire.services.keyword_ledger import (
            letter_coverage_budget,
            rank_gate_missing_claimable,
            verified_missing_claimable,
        )

        cb = letter_coverage_budget(10)
        draft = _letter_draft(10)  # occupancy == capacity -> under pressure
        missing = verified_missing_claimable(draft, _LEDGER_RANKED)
        blocking, below_rank = rank_gate_missing_claimable(missing, draft, cb)
        assert [e["concept"] for e in blocking] == ["Required Thing"]
        assert {e["concept"] for e in below_rank} == {"Nice Thing", "Keyword Thing"}


class TestCoverageReviewerPromptFnRankGated:
    """The composed reviewer prompt: below-rank absences are not merely
    excluded from the RETURNED blocking list, they never reach the model as
    a demand — the block shrinks (or vanishes) rather than growing."""

    def test_block_shrinks_under_pressure_when_only_below_rank_missing(self):
        from applire.services.cv_budget import BudgetResult, RoleBudget
        from applire.services.keyword_ledger import (
            coverage_reviewer_prompt_fn,
            cv_coverage_budget,
        )

        base = lambda source, draft: f"BASE[{source}]"
        ledger = _LEDGER_RANKED[1:]  # Nice Thing + Keyword Thing only — no required
        budget_result = BudgetResult(
            roles={"w1": RoleBudget(work_entry_id="w1", tier="bottom", max_bullets=1)},
            tiers={}, target_pages=2, region="DACH",
        )
        draft = _cv_draft(1)  # occupancy == capacity -> under pressure

        ungated = coverage_reviewer_prompt_fn(base, ledger)(  # budget=None default
            "src", draft
        )
        gated = coverage_reviewer_prompt_fn(
            base, ledger, budget=cv_coverage_budget(budget_result)
        )("src", draft)

        assert "Nice Thing" in ungated and "Keyword Thing" in ungated
        # Under pressure, with nothing but below-rank absences: NOT a blocking
        # issue at all — the demand disappears rather than being softened.
        assert gated == "BASE[src]"
        assert len(gated) < len(ungated)

    def test_required_absence_still_demanded_under_pressure(self):
        from applire.services.cv_budget import BudgetResult, RoleBudget
        from applire.services.keyword_ledger import (
            coverage_reviewer_prompt_fn,
            cv_coverage_budget,
        )

        base = lambda source, draft: f"BASE[{source}]"
        budget_result = BudgetResult(
            roles={"w1": RoleBudget(work_entry_id="w1", tier="bottom", max_bullets=1)},
            tiers={}, target_pages=2, region="DACH",
        )
        draft = _cv_draft(1)

        gated = coverage_reviewer_prompt_fn(
            base, _LEDGER_RANKED, budget=cv_coverage_budget(budget_result)
        )("src", draft)
        assert "Required Thing" in gated
        assert "You MUST set approved=false" in gated
        # The below-rank terms are not smuggled back in as a demand either.
        assert "Nice Thing" not in gated
        assert "Keyword Thing" not in gated

    def test_block_never_larger_than_ungated_equivalent(self):
        """Rank-gating may only ever shrink or leave unchanged the coverage
        demand — never grow it (issue #543's explicit size discipline)."""
        from applire.services.cv_budget import BudgetResult, RoleBudget
        from applire.services.keyword_ledger import (
            coverage_reviewer_prompt_fn,
            cv_coverage_budget,
        )

        base = lambda source, draft: f"BASE[{source}]"
        budget_result = BudgetResult(
            roles={"w1": RoleBudget(work_entry_id="w1", tier="bottom", max_bullets=1)},
            tiers={}, target_pages=2, region="DACH",
        )
        for n in (0, 1, 2):
            draft = _cv_draft(n)
            ungated = coverage_reviewer_prompt_fn(base, _LEDGER_RANKED)("src", draft)
            gated = coverage_reviewer_prompt_fn(
                base, _LEDGER_RANKED, budget=cv_coverage_budget(budget_result)
            )("src", draft)
            assert len(gated) <= len(ungated)


def _cv_prose_draft(n_bullets: int) -> dict:
    """The CV DRAFTING loop's draft: the writer's own response schema
    (``prompts/cv_tailoring.py``), whose work list is named ``work``, not
    ``work_history``. Same occupancy as ``_cv_draft(n)``, different key —
    that difference is the whole of ruling 4's defect."""
    return {
        "summary": "Experienced professional.",
        "work": [
            {"id": "w1",
             "bullets": [f"Delivered outcome {i}." for i in range(n_bullets)]},
        ],
        "skills": [],
    }


class TestCoverageBudgetMeasureReadsBothDocumentShapes:
    """Founder ruling 4 (2026-09-05, v0.41.1-beta) — ADR-076 clause 6's rank
    gate never engaged on the CV drafting loop.

    ``cv_coverage_budget.measure`` read ``work_history`` directly. Two of the
    three CV reviewer chains (``cv_tailoring``, ``cv_language``) review the
    writer's PROSE draft, whose work list is named ``work``; only
    ``cv_terminal_review`` reviews the composed ``TailoredCVData``. So on the
    two prose loops the measure returned 0 for EVERY draft,
    ``under_pressure`` was permanently False, and every missing claimable
    entry — required or not — stayed blocking. A control structurally unable
    to fire.
    """

    def _budget(self, max_bullets: int):
        from applire.services.cv_budget import BudgetResult, RoleBudget
        from applire.services.keyword_ledger import cv_coverage_budget

        return cv_coverage_budget(
            BudgetResult(
                roles={"w1": RoleBudget(work_entry_id="w1", tier="top",
                                        max_bullets=max_bullets)},
                tiers={}, target_pages=2, region="DACH",
            )
        )

    def test_prose_shaped_draft_measures_its_bullets(self):
        """Was 0 for every N before the fix — the defect, pinned."""
        cb = self._budget(5)
        assert cb.measure(_cv_prose_draft(0)) == 0
        assert cb.measure(_cv_prose_draft(3)) == 3
        assert cb.measure(_cv_prose_draft(7)) == 7

    def test_composed_shaped_draft_still_measures_its_bullets(self):
        """The terminal loop's shape is untouched by the fix."""
        cb = self._budget(5)
        assert cb.measure(_cv_draft(3)) == 3
        assert cb.measure(_cv_draft(7)) == 7

    def test_both_shapes_agree_at_equal_occupancy(self):
        """One definition of narrative space (ADR-066): the same three bullets
        measure the same whichever key the document uses."""
        cb = self._budget(5)
        for n in (0, 1, 2, 3, 8):
            assert cb.measure(_cv_prose_draft(n)) == cb.measure(_cv_draft(n))

    def test_nested_project_bullets_count_on_the_prose_shape_too(self):
        """The corpus rule itself is unchanged and is not re-implemented by the
        adapter — nested project bullets count on both shapes."""
        cb = self._budget(5)
        prose = _cv_prose_draft(1)
        prose["work"][0]["projects"] = [{"name": "P", "bullets": ["p1", "p2"]}]
        assert cb.measure(prose) == 3

    def test_rank_gate_engages_on_a_prose_draft_at_capacity(self):
        """The consequence: at capacity, a below-rank absence stops being a
        blocking demand on the DRAFTING loop. Before the fix the gate returned
        all three as blocking here, because measure() said 0 of 2."""
        from applire.services.keyword_ledger import (
            rank_gate_missing_claimable,
            verified_missing_claimable,
        )

        cb = self._budget(2)
        draft = _cv_prose_draft(2)  # occupancy == capacity
        missing = verified_missing_claimable(draft, _LEDGER_RANKED)
        blocking, below_rank = rank_gate_missing_claimable(missing, draft, cb)
        assert [e["concept"] for e in blocking] == ["Required Thing"]
        assert {e["concept"] for e in below_rank} == {"Nice Thing", "Keyword Thing"}

    def test_rank_gate_still_open_on_a_prose_draft_below_capacity(self):
        """Room left: nothing is withheld — the gate did not become eager."""
        from applire.services.keyword_ledger import (
            rank_gate_missing_claimable,
            verified_missing_claimable,
        )

        cb = self._budget(5)
        draft = _cv_prose_draft(1)
        missing = verified_missing_claimable(draft, _LEDGER_RANKED)
        blocking, below_rank = rank_gate_missing_claimable(missing, draft, cb)
        assert len(blocking) == 3
        assert below_rank == []

    def test_reviewer_prompt_on_the_drafting_loop_drops_below_rank_demands(self):
        """End of the mechanism, at the seam the model actually reads: the
        VERIFIED COVERAGE block handed to the ``cv_tailoring`` reviewer no
        longer commands the below-rank terms once the prose draft is full."""
        from applire.services.keyword_ledger import coverage_reviewer_prompt_fn

        base = lambda source, draft: f"BASE[{source}]"
        draft = _cv_prose_draft(2)
        gated = coverage_reviewer_prompt_fn(
            base, _LEDGER_RANKED, budget=self._budget(2)
        )("src", draft)
        ungated = coverage_reviewer_prompt_fn(base, _LEDGER_RANKED)("src", draft)

        assert "Required Thing" in gated
        assert "Nice Thing" not in gated
        assert "Keyword Thing" not in gated
        assert "Nice Thing" in ungated and "Keyword Thing" in ungated

    def test_the_adapter_is_the_shared_one(self):
        """ADR-066: the under-claim signal and the coverage budget read ONE
        adapter, not two — ``cv_gap_hints`` re-exports the ledger's."""
        from applire.services.cv_gap_hints import narrative_corpus_view as hints_view
        from applire.services.keyword_ledger import narrative_corpus_view as ledger_view

        assert hints_view is ledger_view
