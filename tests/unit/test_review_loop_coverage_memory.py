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

"""#306 — the cover-letter review loop must not spend its retry budget
re-winning coverage it had already won.

**The recorded mechanism** (``backend/logs/llm/2026-08-06.jsonl``, chain
``cover_letter``, 13:57–14:05 UTC, real provider, ``operations_marcus_de`` DE).
The loop's own deterministic coverage scan
(:func:`applire.services.keyword_ledger.verified_missing_claimable`, injected into
the REVIEWER prompt each round by ``coverage_reviewer_prompt_fn``) reports, round
by round:

    round 1: Shopfloor-Management, Deutsch, SAP MM, Englisch
    round 2: Deutsch, Englisch              <- round 1's two demands delivered
    round 3: SMED, KVP                      <- NEITHER was ever demanded before;
                                               both were present in drafts 0 AND 1
    round 4: (none)
    round 5: (none)

Round 3's demands are a regression the loop caused itself. Round 2's reviewer had
asked for an employer anchor on one sentence; the corrector rewrote that sentence
and, in the same edit, deleted the clause and the following sentence that carried
``KVP`` (with its ``4,1 % -> 2,3 %`` arc) and ``SMED`` (with ``87 % -> 96 %``).
Nothing in the corrector's prompt said those had to survive: ``build_retry_prompt``
carries the reviewer's feedback, the source, and the previous draft, and the
per-round coverage state is computed for the REVIEWER only. Rounds 3 and 4 were
then spent putting back what draft 1 already had, and the loop exhausted at 5/5.

The same signature appears in five further chains — ``cover_letter`` chain 1 and
``cover_letter_condense`` on 2026-08-06, and ``cover_letter`` chain 0 on
2026-08-02 (``Digitalisierung Fertigung`` satisfied at round 2, absent again at
round 3).

**What the test asserts** is the outcome, not the mechanism: given a corrector
that acts on the *instructions* in its prompt (and not on content it merely sees
quoted back at it — precisely what the real model did), the loop must deliver a
draft that covers every claimable term, within budget.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from applire.prompts.review_cover_letter import build_retry_prompt
from applire.services.keyword_ledger import verified_missing_claimable
from applire.services.reviewer import review_and_refine

# Four claimable, ``direct`` ledger entries; no ``bar`` facet and no
# ``adjacent_evidence``, so none is scope- or positioning-exempt.
LEDGER: list[dict[str, Any]] = [
    {
        "concept": "SMED",
        "surface_forms": ["SMED"],
        "claimable": True,
        "status": "direct",
        "sources": ["keyword"],
        "fit_weight": 1.0,
        "evidence": "9 Jahre SMED, Ruestworkshops zur Verbesserung der Termintreue",
    },
    {
        "concept": "KVP",
        "surface_forms": ["KVP"],
        "claimable": True,
        "status": "direct",
        "sources": ["keyword"],
        "fit_weight": 1.0,
        "evidence": "9 Jahre KVP, Einfuehrung von KVP-Routinen im Shopfloor",
    },
    {
        "concept": "Shopfloor-Management",
        "surface_forms": ["Shopfloor-Management"],
        "claimable": True,
        "status": "direct",
        "sources": ["keyword"],
        "fit_weight": 1.0,
        "evidence": "9 Jahre Shopfloor-Management, taegliche Anwendung",
    },
    {
        "concept": "Englisch",
        "surface_forms": ["Englisch"],
        "claimable": True,
        "status": "direct",
        "sources": ["keyword"],
        "fit_weight": 1.0,
        "evidence": "Englisch auf B2-Niveau",
    },
]

ALL_TERMS = ["SMED", "KVP", "Shopfloor-Management", "Englisch"]

SOURCE = json.dumps(
    {
        "profile": {
            "skills": ALL_TERMS,
            "note": "every term above is backed by the vault, verbatim",
        }
    },
    ensure_ascii=False,
    indent=2,
)


def _letter(terms: list[str], round_no: int) -> dict[str, Any]:
    """A cover-letter-shaped draft surfacing exactly ``terms``.

    ``round_no`` rides along in a filler sentence so no two rounds produce a
    byte-identical draft — the loop's cycle detector never fired in any of the
    recorded runs either, and this test is about convergence, not cycle-stop.
    """
    return {
        "header": {"name": "Stefan Brandt"},
        "recipient": {"company": "Rheinwerk Verpackungen GmbH"},
        "body": {
            "paragraphs": [
                "Sehr geehrte Damen und Herren, mit grossem Interesse habe ich "
                "Ihre Stellenausschreibung gelesen.",
                "Meine Erfahrung umfasst " + ", ".join(terms) + "."
                if terms
                else "Meine Erfahrung umfasst Fuehrungsaufgaben.",
                f"Ich freue mich auf ein Gespraech. (Fassung {round_no}.)",
            ]
        },
        "signature": {"name": "Stefan Brandt"},
    }


class _ScriptedLoop:
    """Replays the recorded round pattern deterministically.

    * **reviewer** — the pipeline's own deterministic ground truth: reject while
      :func:`verified_missing_claimable` is non-empty, naming the absent terms
      (this is what ``coverage_reviewer_prompt_fn`` tells the real reviewer to do,
      and what it did on every round of the recorded runs).
    * **corrector** — writes a letter carrying exactly the claimable terms its
      prompt *instructs* it about. Content it merely sees quoted back (the
      ``PREVIOUS OUTPUT`` block, the source) does not survive a rewrite: on
      2026-08-06 ``SMED`` and ``KVP`` were in both, verbatim, and were deleted
      anyway when the corrector rewrote the sentence that held them.
    """

    def __init__(self) -> None:
        self.review_rounds = 0
        self.current_draft: dict[str, Any] | None = None
        self.gen_args: tuple[dict[str, Any], str, str] | None = None
        self._expect_reviewer = True

    # -- prompt builders handed to review_and_refine ------------------------
    def reviewer_prompt_fn(self, source: str, draft: dict[str, Any]) -> str:
        self.current_draft = draft
        return f"REVIEW THIS DRAFT\n{json.dumps(draft, ensure_ascii=False)}"

    def generator_base_fn(
        self, previous_draft: dict[str, Any], feedback: str, source: str
    ) -> str:
        self.gen_args = (previous_draft, feedback, source)
        return build_retry_prompt(previous_draft, feedback, source)

    # -- the scripted provider ---------------------------------------------
    def _instruction_text(self, prompt: str) -> str:
        """The part of the retry prompt that INSTRUCTS, with the quoted source
        and the quoted previous draft removed. Deliberately independent of any
        particular fix's wording: any change that names a term as something to
        do is visible here; merely echoing the draft is not."""
        assert self.gen_args is not None
        previous_draft, _feedback, source = self.gen_args
        text = prompt.replace(source, " ")
        text = text.replace(json.dumps(previous_draft, ensure_ascii=False, indent=2), " ")
        return text

    async def aparse_json(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        if self._expect_reviewer:
            self._expect_reviewer = False
            self.review_rounds += 1
            missing = [
                e["concept"] for e in verified_missing_claimable(self.current_draft or {}, LEDGER)
            ]
            if not missing:
                return {"approved": True, "issues": [], "feedback": ""}
            return {
                "approved": False,
                "issues": [
                    {
                        "severity": "blocking",
                        "issue": f"VERIFIED COVERAGE CHECK: claimable keyword "
                        f"'{term}' is absent from the draft.",
                    }
                    for term in missing
                ],
                "feedback": "Surface these claimable keywords: " + ", ".join(missing),
            }
        self._expect_reviewer = True
        instructions = self._instruction_text(prompt)
        terms = [t for t in ALL_TERMS if t in instructions]
        return _letter(terms, self.review_rounds)


async def _run(generator_prompt_fn, scripted: _ScriptedLoop, initial: dict[str, Any]):
    return await review_and_refine(
        source=SOURCE,
        draft=initial,
        generator_prompt_fn=generator_prompt_fn,
        generator_system="gen-sys",
        reviewer_prompt_fn=scripted.reviewer_prompt_fn,
        reviewer_system="rev-sys",
        provider=scripted,  # type: ignore[arg-type]
        max_retries=5,
        chain_id="cover_letter",
    )


class TestCoverageIsNotRelitigated:
    @pytest.mark.asyncio
    async def test_loop_converges_and_delivers_full_coverage(self):
        """The delivered letter covers every claimable term, within budget.

        On the unfixed loop the corrector is told only what is MISSING, so each
        round trades the terms it just added for the ones it was last asked
        about — the recorded ``Shopfloor-Management/SAP MM`` then ``SMED/KVP``
        flip — and the loop exhausts with claimable terms still absent.
        """
        from applire.services.keyword_ledger import coverage_corrector_prompt_fn

        scripted = _ScriptedLoop()
        initial = _letter(["SMED", "KVP"], 0)
        result = await _run(
            coverage_corrector_prompt_fn(scripted.generator_base_fn, LEDGER),
            scripted,
            initial,
        )

        assert verified_missing_claimable(result, LEDGER) == []
        # Converged, rather than shipping an unapproved draft at exhaustion.
        assert scripted.review_rounds < 5

    @pytest.mark.asyncio
    async def test_unfixed_corrector_prompt_reproduces_the_non_convergence(self):
        """The control: the same scripted rounds against the bare retry prompt
        do NOT converge — the regression this issue is about, pinned so a later
        change cannot quietly remove the fix and stay green."""
        scripted = _ScriptedLoop()
        initial = _letter(["SMED", "KVP"], 0)
        result = await _run(scripted.generator_base_fn, scripted, initial)

        assert scripted.review_rounds == 5  # exhausted
        assert verified_missing_claimable(result, LEDGER) != []


class TestCoverageCorrectorPromptFn:
    def test_names_the_terms_the_draft_already_surfaces(self):
        from applire.services.keyword_ledger import coverage_corrector_prompt_fn

        base = lambda draft, feedback, source: "BASE"  # noqa: E731
        fn = coverage_corrector_prompt_fn(base, LEDGER)
        prompt = fn(_letter(["SMED", "KVP"], 1), "fix the anchor", SOURCE)
        assert prompt.startswith("BASE")
        assert "SMED" in prompt and "KVP" in prompt
        # Terms the draft does NOT carry are the reviewer's business, not a
        # retention instruction — the corrector must not be told to "keep" them.
        assert "Englisch" not in prompt

    def test_no_block_when_draft_surfaces_nothing_claimable(self):
        from applire.services.keyword_ledger import coverage_corrector_prompt_fn

        base = lambda draft, feedback, source: "BASE"  # noqa: E731
        fn = coverage_corrector_prompt_fn(base, LEDGER)
        assert fn(_letter([], 1), "fb", SOURCE) == "BASE"

    def test_no_ledger_is_a_pure_pass_through(self):
        from applire.services.keyword_ledger import coverage_corrector_prompt_fn

        base = lambda draft, feedback, source: "BASE"  # noqa: E731
        assert coverage_corrector_prompt_fn(base, None)(_letter(ALL_TERMS, 1), "f", "s") == "BASE"
        assert coverage_corrector_prompt_fn(base, [])(_letter(ALL_TERMS, 1), "f", "s") == "BASE"

    def test_states_the_grounding_outranks_coverage_precedence(self):
        """ADR-062 clause 4: this block and the reviewer's verified-coverage
        block reach the same loop, so they must not contradict each other about
        one concept. Both carry the SAME precedence — grounding outranks
        coverage — so a term is never kept by writing something untrue."""
        from applire.services.keyword_ledger import (
            coverage_corrector_prompt_fn,
            render_verified_coverage_block,
            verified_missing_claimable as vmc,
        )

        base = lambda draft, feedback, source: "BASE"  # noqa: E731
        block = coverage_corrector_prompt_fn(base, LEDGER)(_letter(["SMED"], 1), "f", "s")
        reviewer_block = render_verified_coverage_block(vmc(_letter(["SMED"], 1), LEDGER))
        for text in (block, reviewer_block):
            low = text.lower()
            assert "grounding" in low and "coverage" in low
