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

"""ADR-021 amended 2026-08-13, clause 4 (#531): the DO-NOT-CLAIM judgement is
handed the SAFE DIRECTION of the presence predicate.

Gate charter run 1's #531 measurement: 2 of the 3 DO-NOT-CLAIM findings named a
term that appears **nowhere in the graded draft**. The reviewer was asked a
usage-honesty question that silently presupposes a presence determination, and
was forbidden from performing literal string matching to answer it — a
prohibition is not a substitute for supplying the answer.

The block below states which forbidden terms the shared predicate
(``ats_audit.surface_present``, ADR-048) actually FINDS in this draft. Presence
stops being the model's to determine; only honesty of use remains. The direction
matters: the fold is English-only and German morphology defeats it, so a term the
scan did not find is NOT declared absent — a finding about it stays sayable at
the price of quoting the exact draft text (ADR-062 clause 1 read the other way:
a judgement pass may not be asked for a fact, and a fact-grade block may only
speak where it is fact-grade).
"""

from applire.services.ats_audit import _norm, surface_present
from applire.services.keyword_ledger import (
    forbidden_presence_reviewer_prompt_fn,
    forbidden_terms_in_draft,
    render_forbidden_presence_block,
)

# ``Digitalisierung`` and ``Investitionsverantwortung`` are gate run 1's own two
# honest-gap concepts (ADR-048 amended 2026-08-13); ``LegalTech`` carries the
# #255 vocabulary collision.
_LEDGER = [
    {"concept": "Shopfloor-Management", "surface_forms": ["Shopfloor"], "claimable": True,
     "status": "direct", "sources": ["keyword"], "fit_weight": 0.0,
     "evidence": "daily shopfloor rounds at Weberit"},
    {"concept": "Digitalisierung", "surface_forms": ["Digitalisierung"], "claimable": False,
     "status": "gap", "sources": ["keyword"], "fit_weight": 0.0, "evidence": ""},
    {"concept": "Investitionsverantwortung", "surface_forms": ["Investitionsverantwortung"],
     "claimable": False, "status": "gap", "sources": ["keyword"], "fit_weight": 0.0,
     "evidence": ""},
]

_DRAFT_NAMING_ONE = {
    "body": {
        "paragraphs": [
            "Die Digitalisierung Ihrer Fertigung ist genau das Umfeld, das mich reizt.",
            "Bei Weberit habe ich das MES-Projekt auf 14 Spritzgussmaschinen ausgerollt.",
        ]
    }
}

_DRAFT_NAMING_NEITHER = {
    "body": {
        "paragraphs": [
            "Bei Weberit habe ich das MES-Projekt auf 14 Spritzgussmaschinen ausgerollt.",
            "Mein Eintrittstermin kann flexibel vereinbart werden.",
        ]
    }
}


def _draft_norm(draft: dict) -> str:
    from applire.services.keyword_ledger import _draft_strings

    return _norm("\n".join(_draft_strings(draft)))


class TestFixturePremises:
    """The fixtures claim a presence property. Check it against the REAL
    predicate — a fixture that merely looks right has passed a guard that did
    nothing before (2026-08-02, the ``verpackung[s|en]`` lesson)."""

    def test_the_present_term_really_is_present_by_the_shared_predicate(self):
        assert surface_present("Digitalisierung", _draft_norm(_DRAFT_NAMING_ONE))

    def test_the_absent_terms_really_are_absent_by_the_shared_predicate(self):
        norm = _draft_norm(_DRAFT_NAMING_NEITHER)
        assert not surface_present("Digitalisierung", norm)
        assert not surface_present("Investitionsverantwortung", norm)

    def test_the_second_term_is_absent_even_from_the_draft_naming_the_first(self):
        assert not surface_present(
            "Investitionsverantwortung", _draft_norm(_DRAFT_NAMING_ONE)
        )


class TestForbiddenTermsInDraft:
    def test_only_the_forbidden_term_the_draft_carries_is_reported(self):
        assert forbidden_terms_in_draft(_DRAFT_NAMING_ONE, _LEDGER) == [
            "Digitalisierung"
        ]

    def test_a_draft_carrying_none_of_them_reports_none(self):
        assert forbidden_terms_in_draft(_DRAFT_NAMING_NEITHER, _LEDGER) == []

    def test_a_claimable_term_is_never_reported_here(self):
        """The block answers the DO-NOT-CLAIM question only. A claimable term in
        the draft is the coverage check's business (US213), not this one."""
        draft = {"body": {"paragraphs": ["Shopfloor-Management war mein Alltag."]}}
        assert forbidden_terms_in_draft(draft, _LEDGER) == []

    def test_an_empty_or_legacy_ledger_is_tolerated(self):
        assert forbidden_terms_in_draft(_DRAFT_NAMING_ONE, None) == []
        assert forbidden_terms_in_draft(_DRAFT_NAMING_ONE, []) == []

    def test_a_surface_form_match_reports_the_concept(self):
        """The scan runs the concept AND its surface forms — the same instrument
        usage as the claimable half — but reports the CONCEPT, because that is
        the name the DO NOT CLAIM list gives the reviewer."""
        ledger = [
            {"concept": "Legal technology", "surface_forms": ["LegalTech"],
             "claimable": False, "status": "gap", "sources": ["keyword"],
             "fit_weight": 0.0, "evidence": ""},
        ]
        draft = {"body": {"paragraphs": ["Ihr LegalTech-Portfolio überzeugt mich."]}}
        assert forbidden_terms_in_draft(draft, ledger) == ["Legal technology"]


class TestRenderForbiddenPresenceBlock:
    def test_a_present_term_is_stated_as_ground_truth(self):
        block = render_forbidden_presence_block(["Digitalisierung"])
        assert "Digitalisierung" in block
        assert "ground truth" in block.lower()

    def test_the_none_case_states_the_absence_explicitly(self):
        """The #531 shape: two of three findings named a term appearing nowhere
        in the draft. Silence would leave the model to improvise; the block says
        the scan found nothing."""
        block = render_forbidden_presence_block([])
        assert block
        assert "none" in block.lower()

    def test_the_block_keeps_a_missed_form_sayable_at_the_price_of_a_quote(self):
        """The fold is English-only. A German inflection the scan missed must
        remain raisable — bounded by having to quote the draft."""
        block = render_forbidden_presence_block(["Digitalisierung"])
        low = block.lower()
        assert "quote" in low
        assert "german" in low

    def test_the_block_carries_the_possession_versus_aspiration_line(self):
        """ADR-021 clause 5: the rule belongs at the point of decision."""
        low = render_forbidden_presence_block(["Digitalisierung"]).lower()
        assert "aspiration" in low
        assert "possession" in low


class TestForbiddenPresenceReviewerPromptFn:
    def test_the_wrapper_appends_the_block_to_the_base_prompt(self):
        fn = forbidden_presence_reviewer_prompt_fn(
            lambda source, draft: "BASE", _LEDGER
        )
        prompt = fn("source", _DRAFT_NAMING_ONE)
        assert prompt.startswith("BASE")
        assert "Digitalisierung" in prompt

    def test_the_block_is_recomputed_per_round(self):
        """review_and_refine calls reviewer_prompt_fn(source, draft) fresh each
        round, so a corrector that removed the term changes the block."""
        fn = forbidden_presence_reviewer_prompt_fn(
            lambda source, draft: "BASE", _LEDGER
        )
        with_term = fn("source", _DRAFT_NAMING_ONE)
        without = fn("source", _DRAFT_NAMING_NEITHER)
        assert "Digitalisierung" in with_term
        assert "Digitalisierung" not in without.split("DO-NOT-CLAIM PRESENCE")[0]
        assert "none" in without.lower()

    def test_a_ledger_with_no_forbidden_terms_appends_nothing(self):
        fn = forbidden_presence_reviewer_prompt_fn(
            lambda source, draft: "BASE", [_LEDGER[0]]
        )
        assert fn("source", _DRAFT_NAMING_ONE) == "BASE"
