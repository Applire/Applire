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


class TestCvSegmentedSummaryConsumesLedger:
    """#235 (Tiramisu founder-acceptance F3) — the segmented path's summary section had
    its own prompt builder with NO keyword_ledger parameter at all, so a Lead AI Engineer
    JD's tailored summary could contain zero "AI" even though the vault (post-interview)
    truthfully supported the JD's top concepts. build_summary_prompt must thread the
    ledger like its sibling section builders (outline/work/skills)."""

    def _prompt(self, **kwargs):
        from applire.prompts.cv_segmented import build_summary_prompt

        return build_summary_prompt(
            directive={"summary_angle": "AI leadership"},
            job_analysis={"role_title": "Lead AI Engineer"},
            profile={"name": "Test"},
            critical_gaps=[],
            output_language="en",
            keyword_ledger=kwargs.get("keyword_ledger", LEDGER),
        )

    def test_claimable_terms_and_evidence_present(self):
        prompt = self._prompt()
        assert "Python" in prompt
        assert "5 years building FastAPI services" in prompt
        assert "managed K8s cluster" in prompt

    def test_honest_gap_term_is_in_do_not_claim_section_not_as_a_strength(self):
        prompt = self._prompt()
        low = prompt.lower()
        claimable_block, sep, forbidden_block = low.partition("do not claim (honest gaps")
        assert sep, "expected an explicit do-not-claim (honest gaps) heading"
        assert "rust" in forbidden_block
        assert "rust" not in claimable_block

    def test_backward_compatible_without_ledger(self):
        from applire.prompts.cv_segmented import build_summary_prompt

        prompt = build_summary_prompt(
            directive={"summary_angle": "x"},
            job_analysis={},
            profile={},
            critical_gaps=[],
            output_language="de",
        )
        assert "OUTPUT LANGUAGE: GERMAN" in prompt

    def test_system_prompt_instructs_leading_with_claimable_concepts(self):
        from applire.prompts.cv_segmented import SUMMARY_SECTION_SYSTEM_PROMPT

        low = SUMMARY_SECTION_SYSTEM_PROMPT.lower()
        assert "claimable" in low
        assert "lead" in low


class TestCvSingleCallSummaryRuleLeadsWithLedger:
    """#235 — the single-call path already threads the ledger block into the prompt,
    but SYSTEM_PROMPT Rule 4 never told the writer to prioritise it for the summary.
    Strengthened wording, not a new feature."""

    def test_system_prompt_rule_4_leads_with_claimable_concepts(self):
        from applire.prompts.cv_tailoring import SYSTEM_PROMPT

        low = SYSTEM_PROMPT.lower()
        assert "claimable" in low
        # Rule 4 (summary) must reference leading with ledger concepts, not just Rule 3 (skills).
        idx_rule4 = low.find("write a concise professional summary")
        assert idx_rule4 != -1
        window = low[idx_rule4: idx_rule4 + 500]
        assert "claimable" in window
        assert "lead" in window


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


class TestUpgradeLedgerForConcepts:
    """#188 — an interview-confirmed honest gap is upgraded IN PLACE so the split the
    generators consume moves it from `forbidden` to `claimable` with evidence."""

    def test_confirmed_gap_moves_from_forbidden_to_claimable(self):
        from applire.services.keyword_ledger import (
            split_ledger_for_prompt,
            upgrade_ledger_for_concepts,
        )

        # Baseline: Rust is an honest gap → it lands in `forbidden`, never claimable.
        _, forbidden_before = split_ledger_for_prompt(LEDGER)
        assert "Rust" in forbidden_before

        upgraded, changed = upgrade_ledger_for_concepts(
            LEDGER, ["Rust"], "Shipped a production Rust CLI at Acme."
        )
        assert changed is True

        claimable, forbidden = split_ledger_for_prompt(upgraded)
        claim_concepts = {c["concept"] for c in claimable}
        assert "Rust" in claim_concepts
        assert "Rust" not in forbidden
        rust = next(c for c in claimable if c["concept"] == "Rust")
        assert rust["status"] in ("direct", "partial")
        assert rust["evidence"] == "Shipped a production Rust CLI at Acme."

    def test_no_match_is_a_noop(self):
        from applire.services.keyword_ledger import upgrade_ledger_for_concepts

        upgraded, changed = upgrade_ledger_for_concepts(
            LEDGER, ["Haskell"], "Some evidence."
        )
        assert changed is False
        assert {c["concept"] for c in upgraded if c["claimable"]} == {"Python", "Kubernetes"}

    def test_never_creates_a_new_entry(self):
        from applire.services.keyword_ledger import upgrade_ledger_for_concepts

        upgraded, _ = upgrade_ledger_for_concepts(LEDGER, ["Haskell"], "x")
        assert len(upgraded) == len(LEDGER)
        assert all(c["concept"] != "Haskell" for c in upgraded)

    def test_already_claimable_entry_is_untouched(self):
        from applire.services.keyword_ledger import upgrade_ledger_for_concepts

        # Python is already claimable with its own evidence — upgrading its concept
        # must not overwrite that evidence or change the entry.
        upgraded, changed = upgrade_ledger_for_concepts(LEDGER, ["Python"], "new ev")
        assert changed is False
        python = next(c for c in upgraded if c["concept"] == "Python")
        assert python["evidence"] == "5 years building FastAPI services at Acme GmbH"

    def test_tolerates_none_and_empty(self):
        from applire.services.keyword_ledger import upgrade_ledger_for_concepts

        assert upgrade_ledger_for_concepts(None, ["Rust"], "x") == ([], False)
        assert upgrade_ledger_for_concepts([], ["Rust"], "x") == ([], False)
        assert upgrade_ledger_for_concepts(LEDGER, [], "x")[1] is False


# ---------------------------------------------------------------------------
# #274/#284/#273 (PO reframing 2026-07-26) — reevaluate_gap_ledger_against_vault
# ---------------------------------------------------------------------------
# Run-6 ground truth: cluster-technical-leadership's five concepts were ALL
# already status=="direct" in the SAME GapAnalysis row the interview loaded —
# evidence had entered the vault ~24 minutes earlier via testimony intake, a
# door #188's addressed-gate (bool(applied.changes) on THIS turn) never sees.
# A requirement's status must reflect whether the vault answers it, not
# whether one particular turn happened to write something. Invented
# (Northwind Labs-style) fixture data throughout — never real profile content.

_TM_DENIAL_STATEMENT = (
    "I have not done hands-on team management at Northwind Labs — I was an "
    "individual contributor there, not a people manager."
)


def _reeval_ledger():
    return [
        {
            "concept": "Team management",
            "surface_forms": ["Team management"],
            "sources": ["required"],
            "fit_weight": 1.0,
            "status": "gap",
            "evidence": "",
            "claimable": False,
        },
        {
            "concept": "Kubernetes orchestration",
            "surface_forms": ["Kubernetes orchestration", "K8s orchestration"],
            "sources": ["required"],
            "fit_weight": 1.0,
            "status": "gap",
            "evidence": "",
            "claimable": False,
        },
        {
            "concept": "Data warehousing",
            "surface_forms": ["Data warehousing"],
            "sources": ["nice_to_have"],
            "fit_weight": 0.5,
            "status": "partial",
            "evidence": "Built a small ETL pipeline once.",
            "claimable": True,
        },
    ]


def _reeval_profile(*, denied=False, extra_work_experience=None):
    metadata = {"denied_concepts": [], "enrichment_history": []}
    if denied:
        metadata["denied_concepts"] = [
            {
                "concept": "Team management",
                "statement": _TM_DENIAL_STATEMENT,
                "source": "interview",
                "date": "2026-07-20",
            }
        ]
        metadata["enrichment_history"] = [
            {
                "id": "e1",
                "timestamp": "2026-07-20T00:00:00",
                "source": "interview",
                "changes": [
                    {
                        "section": "metadata",
                        "field": "denied_concepts",
                        "action": "added",
                        "old_value": None,
                        "new_value": "Team management",
                        "rationale": (
                            "Noted limit: no hands-on Team management "
                            "(candidate's own testimony)"
                        ),
                    },
                ],
            }
        ]
    return {
        "metadata": metadata,
        "work_experience": extra_work_experience or [],
    }


class TestReevaluateGapLedgerAgainstVault:
    """The loop that heals must reuse the loop that grades (#122): presence
    is decided by `ats_audit.surface_present`; the write is
    `upgrade_ledger_for_concepts` (#188) — never a second matcher/write path.
    """

    def test_requirement_answered_via_a_different_door_is_upgraded(self):
        """The run-6 shape: evidence entered the vault through testimony/CV
        import (or an earlier session), never THIS turn — the ledger must
        still catch up, with the actual vault text as evidence."""
        from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault

        ledger = _reeval_ledger()
        profile = _reeval_profile(
            extra_work_experience=[
                {
                    "company": "Northwind Labs",
                    "role": "Engineering Lead",
                    "responsibilities": [
                        "Restructured the team and owned team management "
                        "across two firmware squads."
                    ],
                    "achievements": [],
                }
            ]
        )

        new_ledger, changed = reevaluate_gap_ledger_against_vault(ledger, profile)

        assert changed is True
        entry = next(e for e in new_ledger if e["concept"] == "Team management")
        assert entry["claimable"] is True
        assert entry["status"] == "direct"
        # Evidence is REAL vault text — the exact bullet — never a
        # synthesized marker.
        assert entry["evidence"] == (
            "Restructured the team and owned team management across two "
            "firmware squads."
        )

        # A concept the vault genuinely does not mention stays open.
        k8s = next(e for e in new_ledger if e["concept"] == "Kubernetes orchestration")
        assert k8s["claimable"] is False
        assert k8s["status"] == "gap"

        # Already-claimable "partial" entries are out of scope for this
        # function (conservative: only status=='gap' is eligible) — untouched.
        dw = next(e for e in new_ledger if e["concept"] == "Data warehousing")
        assert dw == ledger[2]

    def test_requirement_the_vault_does_not_answer_stays_open(self):
        from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault

        ledger = _reeval_ledger()
        profile = _reeval_profile(
            extra_work_experience=[
                {"company": "Northwind Labs", "role": "Engineer", "responsibilities": []}
            ]
        )

        new_ledger, changed = reevaluate_gap_ledger_against_vault(ledger, profile)

        assert changed is False
        assert new_ledger == ledger

    def test_denied_concept_never_upgraded_via_its_own_denial_receipt_text(self):
        """The exact prior-art trap: a denial's own statement/receipt text
        must never satisfy the presence check and thereby upgrade the very
        concept it denies. No OTHER vault mention exists here — the ONLY
        occurrence of "team management" anywhere in the profile is inside
        the denial testimony and its enrichment-history receipt."""
        from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault

        ledger = _reeval_ledger()
        profile = _reeval_profile(denied=True)

        new_ledger, changed = reevaluate_gap_ledger_against_vault(ledger, profile)

        entry = next(e for e in new_ledger if e["concept"] == "Team management")
        assert entry["claimable"] is False
        assert entry["status"] == "gap"
        assert changed is False

    def test_denied_concept_never_upgraded_even_with_independent_vault_evidence(self):
        """ADR-059 / ADR-040 (never-claim beats claim): once denied, a
        concept must stay a gap even when a genuinely independent OTHER
        vault mention would otherwise satisfy the presence check on its
        own — the denial floor outranks presence, not just corpus-stripping.
        """
        from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault

        ledger = _reeval_ledger()
        profile = _reeval_profile(
            denied=True,
            extra_work_experience=[
                {
                    "company": "Northwind Labs",
                    "role": "Rotational Engineer",
                    "responsibilities": [
                        "Supported team management activities during a "
                        "cross-functional rotation."
                    ],
                }
            ],
        )

        new_ledger, changed = reevaluate_gap_ledger_against_vault(ledger, profile)

        entry = next(e for e in new_ledger if e["concept"] == "Team management")
        assert entry["claimable"] is False
        assert entry["status"] == "gap"

    def test_makes_no_llm_call(self):
        """Deterministic: no provider/LLM argument exists on the signature at
        all, and the function runs to completion with none in scope."""
        import inspect

        from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault

        sig = inspect.signature(reevaluate_gap_ledger_against_vault)
        assert list(sig.parameters) == ["keyword_ledger", "profile_json"]

        ledger = _reeval_ledger()
        result = reevaluate_gap_ledger_against_vault(ledger, {"work_experience": []})
        assert result == (ledger, False)

    def test_tolerates_none_and_empty(self):
        from applire.services.keyword_ledger import reevaluate_gap_ledger_against_vault

        assert reevaluate_gap_ledger_against_vault(None, {}) == ([], False)
        assert reevaluate_gap_ledger_against_vault([], {"work_experience": []}) == ([], False)
