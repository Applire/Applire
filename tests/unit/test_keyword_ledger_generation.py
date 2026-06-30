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

"""E037 US200/US201 — the CV + cover-letter generators consume the Keyword Ledger.

ADR-048 §8 / ADR-040 standing principle: grounding strictly outranks coverage. The
prompts must surface CLAIMABLE keywords (status direct/partial) only where the profile
evidence supports them, carry that evidence, and present the HONEST-GAP terms
(claimable False) as an explicit do-not-claim list — never as something the candidate
has. Pure prompt-builder tests with a hand-built ledger (no LLM, no DB).
"""

import pytest


# A hand-built ledger covering all three statuses.
LEDGER = [
    {
        "concept": "Python",
        "surface_forms": ["Python", "Py"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "direct",
        "evidence": "5 years building FastAPI services at Acme GmbH",
        "claimable": True,
    },
    {
        "concept": "Kubernetes",
        "surface_forms": ["Kubernetes", "K8s"],
        "sources": ["nice_to_have"],
        "fit_weight": 0.5,
        "status": "partial",
        "evidence": "Deployed containers to a managed K8s cluster on one project",
        "claimable": True,
    },
    {
        "concept": "Rust",
        "surface_forms": ["Rust"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    },
]


class TestCvTailoringConsumesLedger:
    def _prompt(self):
        from applire.prompts.cv_tailoring import build_user_prompt

        return build_user_prompt(
            {"role_title": "Backend Engineer"},
            {"name": "Test"},
            keyword_gaps=[],
            critical_gaps=[],
            output_language="en",
            keyword_ledger=LEDGER,
        )

    def test_claimable_terms_and_evidence_present(self):
        prompt = self._prompt()
        # surface forms of claimable concepts must be offered
        assert "Python" in prompt
        assert "K8s" in prompt or "Kubernetes" in prompt
        # their profile evidence must be carried so the model knows the support
        assert "5 years building FastAPI services" in prompt
        assert "managed K8s cluster" in prompt

    def test_honest_gap_term_is_in_do_not_claim_section_not_as_a_strength(self):
        prompt = self._prompt()
        # Rust is an honest gap — it must appear in a forbidden / do-not-claim list
        assert "Rust" in prompt
        low = prompt.lower()
        assert "do not claim" in low or "do-not-claim" in low or "never claim" in low
        # Rust must live under the do-not-claim heading, never in the claimable block.
        claimable_block, sep, forbidden_block = low.partition("do not claim (honest gaps")
        assert sep, "expected an explicit do-not-claim (honest gaps) heading"
        assert "rust" in forbidden_block
        assert "rust" not in claimable_block

    def test_grounding_outranks_coverage_stated(self):
        prompt = self._prompt()
        low = prompt.lower()
        assert "grounding" in low and ("outrank" in low or "over coverage" in low or "before coverage" in low)

    def test_backward_compatible_without_ledger(self):
        from applire.prompts.cv_tailoring import build_user_prompt

        # Existing callers pass no ledger — must still build a valid prompt.
        prompt = build_user_prompt({}, {}, [], [], output_language="de")
        assert "OUTPUT LANGUAGE: GERMAN" in prompt


class TestCoverLetterConsumesLedger:
    def _prompt(self):
        from applire.prompts.cover_letter import build_cover_letter_prompt

        return build_cover_letter_prompt(
            cv_data={"contact": {"name": "Test"}, "summary": "Engineer"},
            jd_text="We need backend engineers.",
            pre_gen_inputs={"tone": "formal"},
            detected_language="en",
            keyword_ledger=LEDGER,
        )

    def test_claimable_terms_and_evidence_present(self):
        prompt = self._prompt()
        assert "Python" in prompt
        assert "5 years building FastAPI services" in prompt
        assert "managed K8s cluster" in prompt

    def test_honest_gap_in_do_not_claim_section(self):
        prompt = self._prompt()
        low = prompt.lower()
        assert "do not claim" in low or "do-not-claim" in low or "never claim" in low
        claimable_block, sep, forbidden_block = low.partition("do not claim (honest gaps")
        assert sep, "expected an explicit do-not-claim (honest gaps) heading"
        assert "rust" in forbidden_block
        assert "rust" not in claimable_block

    def test_grounding_outranks_coverage_stated(self):
        prompt = self._prompt()
        low = prompt.lower()
        assert "grounding" in low and ("outrank" in low or "over coverage" in low or "before coverage" in low)

    def test_backward_compatible_without_ledger(self):
        from applire.prompts.cover_letter import build_cover_letter_prompt

        prompt = build_cover_letter_prompt(
            cv_data={"contact": {"name": "A"}, "summary": "x"},
            jd_text="JD",
            pre_gen_inputs={"tone": "formal"},
            detected_language="de",
        )
        assert "LANGUAGE: DE" in prompt


class TestCoverLetterSystemPromptSurfacesClaimable:
    """US201 amends the grounding contract: claimable terms (which arrive carrying
    profile evidence) ARE surfaced from the profile, while honest gaps remain
    forbidden and the JD is still not a source of NEW facts."""

    def test_system_prompt_allows_surfacing_claimable_keywords(self):
        from applire.prompts.cover_letter import SYSTEM_PROMPT

        low = SYSTEM_PROMPT.lower()
        # The JD must still not be a source of *new* facts.
        assert "new fact" in low or "not a source of facts" in low or "source of *new*" in low
        # But claimable keywords are now to be surfaced where the profile supports them.
        assert "claimable" in low
        assert "surface" in low


class TestLedgerPromptSplitHelper:
    """The shared split helper that both generators use (pure, ledger-shaped input)."""

    def test_splits_claimable_from_forbidden(self):
        from applire.services.keyword_ledger import split_ledger_for_prompt

        claimable, forbidden = split_ledger_for_prompt(LEDGER)
        claim_concepts = {c["concept"] for c in claimable}
        forbid_concepts = set(forbidden)
        assert claim_concepts == {"Python", "Kubernetes"}
        assert forbid_concepts == {"Rust"}

    def test_handles_empty_and_none(self):
        from applire.services.keyword_ledger import split_ledger_for_prompt

        assert split_ledger_for_prompt([]) == ([], [])
        assert split_ledger_for_prompt(None) == ([], [])
