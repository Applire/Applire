# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#731 — the STATED-LIMIT ADJUDICATION fact, and the #732 positioning rules.

Why the fact exists at all, in one paragraph, because the next agent will be
tempted to delete it: a candidate denial of "vector databases" / "embedding and
reranking" / "retrieval pipeline" cannot reach a ledger row labelled "RAG
methods" — measured with the real functions in
``Documents/Runs/Nougat/founder-uat-fixes/c/probe_lemma.py``,
``declared_denial_matches`` returns ``[]`` and ``is_denied_concept`` returns
False for every plausible denial label. And it must not reach it: pairing a
limit with a concept is a JUDGEMENT (ADR-062 clause 1), and the matcher that
tried it (``find_scoped_boundaries``) answered it backwards on real data, 4 of 4
false, and was deleted. So the row stays CLAIMABLE, it is absent from the
forbidden list, ``forbidden_terms_in_draft`` cannot return it, and the
DO-NOT-CLAIM PRESENCE block correctly says nothing about it — while the
delivered letter of the 2026-09-20 founder UAT claimed the retrieval work the
candidate had attributed to a colleague.

This block supplies the PRESENCE half of that question so the judgement half can
be asked at all, which is ``forbidden_terms_in_draft``'s own lesson (#531: "a
prohibition is not a substitute for supplying the answer"), applied to the
claimable side of the ledger.
"""
from __future__ import annotations

import pytest

from applire.services.cross_document import (
    claimed_concepts_in_draft,
    render_stated_limit_adjudication_block,
    stated_limit_adjudication_reviewer_prompt_fn,
)

# ── fixtures: the #731 configuration, synthetic throughout ──────────────────

_STATEMENT = (
    "the embedding and reranking work in that project was my system engineer's, not "
    "mine, and I have not personally built a retrieval pipeline. Please do not credit "
    "me with vector databases as a skill."
)

_DENIED_CONCEPTS = [
    {"concept": "vector databases", "statement": _STATEMENT,
     "source": "agent_interview", "date": "2026-09-20"},
]

#: `RAG methods` is CLAIMABLE — the vault's own bullet names RAG — while
#: `vector databases` is floored to `denied`. That asymmetry is the defect's
#: whole mechanism, so the fixture asserts it rather than assuming it.
_LEDGER = [
    {"concept": "RAG methods", "surface_forms": ["RAG", "RAG methods"],
     "status": "direct", "claimable": True, "sources": ["required"],
     "fit_weight": 1.0, "evidence": "Owns an AI use case built on LLMs and RAG."},
    {"concept": "team leadership", "surface_forms": ["team leadership"],
     "status": "direct", "claimable": True, "sources": ["required"],
     "fit_weight": 1.0, "evidence": "Leads five tech leads."},
    {"concept": "vector databases", "surface_forms": ["vector databases"],
     "status": "denied", "claimable": False, "sources": ["required"],
     "fit_weight": 1.0, "evidence": "The candidate was asked and said no."},
]

_DRAFT_CLAIMING_RAG = {"body": {"paragraphs": [
    "Dear Sir or Madam,",
    "I manage an AI automation use case using Databricks, large language models and "
    "RAG methods, and team leadership is where I spend most of my week.",
]}}

_DRAFT_WITHOUT_RAG = {"body": {"paragraphs": [
    "Dear Sir or Madam,",
    "I manage an AI automation use case built on Databricks, and team leadership is "
    "where I spend most of my week.",
]}}


class TestTheFixtureReallyHasThePropertyItClaims:
    """`feedback_mutation_test_the_guard`: assert the fixture's property with the
    real predicate before building anything on it."""

    def test_the_rag_row_is_claimable_and_not_forbidden(self):
        from applire.services.keyword_ledger import split_ledger_for_prompt

        claimable, forbidden = split_ledger_for_prompt(_LEDGER)
        assert "RAG methods" in [e["concept"] for e in claimable]
        assert "RAG methods" not in forbidden
        assert forbidden == ["vector databases"]

    def test_the_production_presence_instrument_cannot_see_the_rag_claim(self):
        """The reason this whole module exists: the shipped DO-NOT-CLAIM presence
        fact is silent on the concept the delivered letter claimed."""
        from applire.services.keyword_ledger import forbidden_terms_in_draft

        assert forbidden_terms_in_draft(_DRAFT_CLAIMING_RAG, _LEDGER) == []

    def test_neither_half_of_the_denial_machinery_reaches_the_rag_row(self):
        from applire.services.profile.reconcile.stance import (
            declared_denial_matches,
            is_denied_concept,
        )

        labels = ["vector databases", "embedding and reranking", "retrieval pipeline"]
        assert declared_denial_matches("RAG methods", labels) == []
        assert is_denied_concept("RAG methods", labels, None) is False
        # and the control: the label the denial DOES name is reached by both halves
        assert declared_denial_matches("vector databases", labels) == ["vector databases"]
        assert is_denied_concept("vector databases", labels, None) is True


class TestClaimedConceptsInDraft:
    def test_it_returns_the_claimable_concepts_the_draft_surfaces(self):
        got = claimed_concepts_in_draft(_DRAFT_CLAIMING_RAG, _LEDGER)
        assert "RAG methods" in got
        assert "team leadership" in got

    def test_it_never_returns_a_forbidden_concept(self):
        """The mutation this kills: reading the FORBIDDEN half of the ledger split
        instead of the claimable one. That would duplicate the DO-NOT-CLAIM
        PRESENCE block and say nothing about the concept #731 is about."""
        draft = {"body": {"paragraphs": [
            "Dear Sir or Madam,",
            "I have built vector databases, and team leadership is my daily work.",
        ]}}
        got = claimed_concepts_in_draft(draft, _LEDGER)
        assert "vector databases" not in got
        assert "team leadership" in got

    def test_a_concept_absent_from_the_draft_is_not_returned(self):
        got = claimed_concepts_in_draft(_DRAFT_WITHOUT_RAG, _LEDGER)
        assert "RAG methods" not in got
        assert "team leadership" in got

    def test_no_ledger_is_tolerated(self):
        assert claimed_concepts_in_draft(_DRAFT_CLAIMING_RAG, None) == []
        assert claimed_concepts_in_draft(_DRAFT_CLAIMING_RAG, []) == []

    def test_it_uses_the_shared_presence_predicate(self):
        """ADR-066: one instrument. A surface form the shared predicate finds must
        be found here too — this is the seam that keeps the adjudication block and
        the ATS panel from disagreeing about presence."""
        from applire.services.ats_audit import _norm, surface_present

        text = _norm(" ".join(_DRAFT_CLAIMING_RAG["body"]["paragraphs"]))
        assert surface_present("RAG", text) is True
        assert "RAG methods" in claimed_concepts_in_draft(_DRAFT_CLAIMING_RAG, _LEDGER)


class TestTheRenderedBlock:
    def test_it_states_the_presence_fact_and_the_limits(self):
        block = render_stated_limit_adjudication_block(["RAG methods"], [_STATEMENT])
        assert "STATED-LIMIT ADJUDICATION" in block
        assert "RAG methods" in block
        assert _STATEMENT in block
        assert "do not re-derive" in block

    def test_it_says_the_ledger_cannot_answer_the_question(self):
        """Without this sentence the reviewer reads a CLAIMABLE status as a
        defence — the reading the 0/5 arms measured."""
        block = render_stated_limit_adjudication_block(["RAG methods"], [_STATEMENT])
        low = block.lower()
        assert "claimable" in low
        assert "not a defence" in low
        assert "never from these statements" in low

    def test_it_closes_the_ownership_reading(self):
        """FOUNDER-QUESTION C-4 option A: a sentence naming the concept inside a
        system the candidate owns is a sentence about the candidate."""
        block = render_stated_limit_adjudication_block(["RAG methods"], [_STATEMENT])
        low = block.lower()
        assert "owns, manages or runs" in low
        assert "target employer" in low

    def test_it_keeps_a_missed_surface_form_raisable(self):
        block = render_stated_limit_adjudication_block(["RAG methods"], [_STATEMENT])
        assert "english verb forms only" in block.lower()
        assert "quote the exact words" in block.lower()

    def test_nothing_is_rendered_without_limits_or_without_claims(self):
        assert render_stated_limit_adjudication_block(["RAG methods"], []) == ""
        assert render_stated_limit_adjudication_block([], [_STATEMENT]) == ""
        assert render_stated_limit_adjudication_block([], []) == ""


class TestTheReviewerWrapper:
    def test_it_composes_onto_the_base_prompt_and_appends(self):
        base = lambda source, draft: "BASE"  # noqa: E731
        fn = stated_limit_adjudication_reviewer_prompt_fn(
            base, keyword_ledger=_LEDGER, denied_concepts=_DENIED_CONCEPTS)
        out = fn("src", _DRAFT_CLAIMING_RAG)
        assert out.startswith("BASE")
        assert "STATED-LIMIT ADJUDICATION" in out

    def test_it_is_silent_when_the_vault_holds_no_limit(self):
        base = lambda source, draft: "BASE"  # noqa: E731
        fn = stated_limit_adjudication_reviewer_prompt_fn(
            base, keyword_ledger=_LEDGER, denied_concepts=[])
        assert fn("src", _DRAFT_CLAIMING_RAG) == "BASE"

    def test_it_is_silent_when_the_draft_claims_nothing_the_ledger_marks_claimable(self):
        base = lambda source, draft: "BASE"  # noqa: E731
        fn = stated_limit_adjudication_reviewer_prompt_fn(
            base, keyword_ledger=[_LEDGER[2]], denied_concepts=_DENIED_CONCEPTS)
        assert fn("src", _DRAFT_CLAIMING_RAG) == "BASE"

    def test_it_tracks_the_CURRENT_draft_per_round(self):
        """The per-round property: `review_and_refine` calls
        `reviewer_prompt_fn(source, draft)` fresh each round, so a corrector that
        removes the claim must make the block go quiet about it."""
        base = lambda source, draft: "BASE"  # noqa: E731
        fn = stated_limit_adjudication_reviewer_prompt_fn(
            base, keyword_ledger=_LEDGER, denied_concepts=_DENIED_CONCEPTS)
        assert "RAG methods" in fn("src", _DRAFT_CLAIMING_RAG)
        assert "RAG methods" not in fn("src", _DRAFT_WITHOUT_RAG)

    def test_it_logs_the_fact_it_supplied(self, caplog):
        import logging

        base = lambda source, draft: "BASE"  # noqa: E731
        fn = stated_limit_adjudication_reviewer_prompt_fn(
            base, keyword_ledger=_LEDGER, denied_concepts=_DENIED_CONCEPTS)
        with caplog.at_level(logging.INFO, logger="applire.services.cross_document"):
            fn("src", _DRAFT_CLAIMING_RAG)
        assert "stated-limit adjudication (#731)" in caplog.text

    def test_it_falls_back_to_every_limit_when_none_is_jd_relevant(self):
        """`select_jd_relevant_limits` needs a ledger row with status `denied`; a
        denial this posting never asked about has none. The adjudication question
        is about the DRAFT's claims, so such a limit must still reach the
        reviewer."""
        ledger_without_the_denied_row = [_LEDGER[0], _LEDGER[1]]
        base = lambda source, draft: "BASE"  # noqa: E731
        fn = stated_limit_adjudication_reviewer_prompt_fn(
            base, keyword_ledger=ledger_without_the_denied_row,
            denied_concepts=_DENIED_CONCEPTS)
        out = fn("src", _DRAFT_CLAIMING_RAG)
        assert "STATED-LIMIT ADJUDICATION" in out
        assert _STATEMENT in out


class TestTheLetterWiresItAsTheSixthWrapper:
    def test_the_letter_reviewer_stack_returns_this_wrapper_outermost(self):
        """Seam: `_wrap_reviewer` in `services/cover_letter.py` builds ONE closure
        that both letter doors reuse (ADR-076 clause 3), so a single wiring point
        covers the drafting rounds, the condense loop and the terminal round. The
        wrapper must be the one returned, i.e. the outermost."""
        import inspect

        from applire.services import cover_letter as cl

        src = inspect.getsource(cl)
        assert "stated_limit_adjudication_reviewer_prompt_fn" in src
        wrap = src[src.index("def _wrap_reviewer"):]
        wrap = wrap[:wrap.index("reviewer_prompt_fn = _wrap_reviewer")]
        ret = wrap[wrap.rindex("return "):]
        assert "stated_limit_adjudication_reviewer_prompt_fn" in ret, ret[:200]
        # and it composes on top of the do-not-claim presence wrapper rather than
        # replacing it
        assert "fn = forbidden_presence_reviewer_prompt_fn(fn, keyword_ledger)" in wrap


class TestThe732PositioningRules:
    """The rules whose effect was measured BEFORE 0/5 -> AFTER 5/5 on the real
    provider (`c/runs/reviewer-{before,after}-n5.json`). A prompt-text assertion is
    NOT evidence the rule fires — these only pin the wording the measurement was
    taken on, so a later edit that silently drops it is caught."""

    def test_the_writer_ranks_the_three_responses(self):
        from applire.prompts.cover_letter import SYSTEM_PROMPT

        assert "RANKED" in SYSTEM_PROMPT
        assert "take the HIGHEST rung TRUE of the candidate" in SYSTEM_PROMPT
        # RULING C-3 (founder, 2026-09-20): rungs (1) and (2) DISCHARGE the
        # requirement — a letter delivering one owes no negation about it.
        assert "DISCHARGE the requirement" in SYSTEM_PROMPT
        assert "choose one of exactly three responses" not in SYSTEM_PROMPT

    def test_the_writer_states_the_fused_form_inside_the_rule_that_owns_ordering(self):
        """The form rule was first written as its own rule and then FOLDED into
        STATED LIMITS (b), which already owned gap ordering — the merge the
        prompt-size ratchet asks for before a ceiling may move. So the assertion
        is that the form is stated inside that rule, not that a second rule about
        the same subject exists."""
        from applire.prompts.cover_letter import SYSTEM_PROMPT

        assert "THE SHAPE OF A NAMED GAP" not in SYSTEM_PROMPT, (
            "the form rule regrew into a second rule about gap shape")
        i = SYSTEM_PROMPT.index("(b) OBLIGATION:")
        rule_b = " ".join(
            SYSTEM_PROMPT[i:SYSTEM_PROMPT.index("- EVERY UNMET JD HARD REQUIREMENT", i)].split()
        )
        assert "in the SAME SENTENCE" in rule_b, rule_b
        assert "never a standalone negative" in rule_b, rule_b
        assert "right after a sentence stating a strength" in rule_b, rule_b
        # RULING C-3: the ladder is stated ONCE, in the #270 rule; (b) points at it
        assert "ranked ladder of the next rule" in rule_b, rule_b
        assert "DISCHARGES the limit" in rule_b, rule_b

    @pytest.mark.parametrize("door", ["REVIEW_SYSTEM_PROMPT",
                                      "TERMINAL_REVIEW_SYSTEM_PROMPT"])
    def test_both_reviewer_doors_carry_the_half_delivery_finding(self, door):
        import applire.prompts.review_cover_letter as rc

        p = getattr(rc, door)
        assert "HALF-DELIVERY IS THE THIRD DIRECTION" in p
        assert "this is not `minor`" in p
        assert "paragraph order" in p

    @pytest.mark.parametrize("door", ["REVIEW_SYSTEM_PROMPT",
                                      "TERMINAL_REVIEW_SYSTEM_PROMPT"])
    def test_the_admit_and_affirm_protection_is_scoped_to_the_fused_sentence(self, door):
        """Without this scoping, check 5's "never flag, soften or split it" shields
        the very shape check 4 must now flag."""
        import applire.prompts.review_cover_letter as rc

        p = getattr(rc, door)
        assert "That covers the FUSED" in p
        assert "check 4's\n     half-delivery finding" in p

    @pytest.mark.parametrize("door", ["REVIEW_SYSTEM_PROMPT",
                                      "TERMINAL_REVIEW_SYSTEM_PROMPT"])
    def test_no_check_one_mirror_bullet_was_kept(self, door):
        """#731: a check-1 mirror bullet for the CLAIMING direction was written,
        measured at 0/5 on the real provider across three arms, and DELETED again
        rather than kept — ADR-062 "deletion over repair", and the 2026-08-31
        lesson that a rule the model demonstrably ignores is not worth its prompt
        budget. Its 1,245 characters are what paid for the ratchet move that
        #732's measured check-4 finding needed. The content survives where it is
        actionable: the STATED-LIMIT ADJUDICATION block, which supplies the fact
        the judgement presupposes. This test is the receipt, so the bullet is not
        re-added out of good intentions."""
        import applire.prompts.review_cover_letter as rc

        p = getattr(rc, door)
        assert "A CLAIM A STATED LIMIT CONTRADICTS IS UNGROUNDED TOO" not in p

    def test_the_writer_block_states_the_fused_form(self):
        from applire.services.cross_document import render_required_limits_block

        block = render_required_limits_block([_STATEMENT])
        assert "ONE sentence" in block
        assert "standalone negative sentence follow" in block

    def test_the_positioning_entry_states_the_fused_form(self):
        """This entry is what reaches the REVIEWER and the CORRECTOR every round
        through `source` — the corrector is the call that writes the sentence."""
        from applire.services.cover_letter import build_stated_limits_entry

        entry = build_stated_limits_entry(_DENIED_CONCEPTS, _LEDGER)
        assert entry is not None and entry["required"] is True
        assert "ONE sentence" in entry["instruction"]
        assert "HALF-delivered" in entry["instruction"]
