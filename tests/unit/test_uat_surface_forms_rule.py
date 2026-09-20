# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ruling B-3/B-3b (2026-09-20) — the `surface_forms` rule's CONTRADICTION guard.

Read this before adding to it: these are **not** evidence that the prompt rule
works. `applire-prompt-first` is explicit that a prompt-text test asserting
wording proves nothing about model compliance — the evidence for that is the
real-provider replay in `Documents/Runs/Nougat/founder-uat-fixes/b/report.md`
(n=8 per arm, `openai/gpt-5.6-luna`).

What these tests DO guard is the specific regression that the measurement
identified as the rule's failure mode: the v4 prompt told the model that a
component technology must NOT appear among a claimable concept's surface forms,
while the fix requires exactly the opposite for the SAME strings. Measured: with
the first v5 draft, which only said components "belong" in the forms, the RAG row
carried a component in 1 of 4 runs — identical to the v4 baseline, and the
captured rows showed why (the model listed the components that were NOT separate
requirements and omitted `vector databases`, which was). Re-introducing either
half of that contradiction is therefore a silent revert of the whole change, and
a text test is the only thing that can catch it, because the effect is
probabilistic and a green mock suite never touches the prompt.

The deterministic consequence the rule exists to enable is tested for real
against the live floor in `test_a_component_surface_form_floors_its_parent`.
"""

import pytest

from applire.prompts.gap_analysis import SYSTEM_PROMPT


def test_the_rule_states_what_a_surface_form_is():
    assert "SURFACE FORMS" in SYSTEM_PROMPT
    assert "other NAMES for the same competence" in SYSTEM_PROMPT
    # The fragments WP-D measured reaching the delivered CV as skill chips are
    # named as counter-examples, with the competences they came from.
    for fragment in ("roadmap", "budget estimation", "vendor selection",
                     "application processes", "AI automation use case"):
        assert fragment in SYSTEM_PROMPT, fragment


def test_the_v4_mutual_exclusion_is_gone():
    """The sentence whose removal the whole change depends on.

    v4: "A technology with no profile signal — or an explicit denial — must NOT
    appear among a claimable concept's surface forms". That is the instruction
    that made a candidate's denial of "vector databases" unable to floor "RAG
    methods" (#731 / founder-UAT F-2), because `_enforce_denial_stance` can only
    reach the parent through the parent's own forms.
    """
    assert "must NOT\n    appear among a claimable concept's surface forms" not in SYSTEM_PROMPT
    assert "list in surface_forms ONLY the technologies the profile" not in SYSTEM_PROMPT


def test_the_rule_says_the_two_places_are_not_alternatives():
    """The measured cause of the first draft's non-compliance (1 of 4 runs).

    The model read "the part gets its own entry" (COMPOUND) and "the part belongs
    in the parent's surface forms" (v5 draft 1) as mutually exclusive, and chose
    the own entry. Both must be stated as required.
    """
    assert "NOT alternatives" in SYSTEM_PROMPT
    assert "EVEN IF the part is also a requirement of its own" in SYSTEM_PROMPT


def test_the_candidates_standing_is_still_kept_out_of_membership():
    """The F4 protection (blind PQ 2026-07-02) the reconciliation must preserve.

    Membership describes the requirement; what the candidate has stays in
    `status`, `reason` and the component's own entry. Measured on 20 real runs:
    0 still-claimable rows carried a component the synthetic profile does not
    support, and the ATS `keywords.present` count did not move.
    """
    assert "never a claim that the candidate has the part" in SYSTEM_PROMPT
    assert 'Never let the COMPOUND concept\'s own "status" or "reason" claim a' in SYSTEM_PROMPT


def test_a_component_surface_form_floors_its_parent():
    """The deterministic payoff, against the live floor — not a text assertion.

    This is WP-A's `NOTE A-1` probe as a regression test: with the component term
    among the parent's forms the candidate's denial reaches the parent, and the
    DO-NOT-CLAIM presence check can name it in a draft. Without it, nothing fires
    — which is what the founder's letter shipped.
    """
    from applire.services.keyword_ledger import (
        _enforce_denial_stance,
        forbidden_terms_in_draft,
        split_ledger_for_prompt,
    )

    denials = ["vector databases", "embedding and reranking", "retrieval pipeline"]
    vault = "\n".join(["databricks", "large language models", "ai automation use case"])
    draft = {
        "work_experience": [
            {
                "responsibilities": [
                    "I manage an AI automation use case using Databricks, large language "
                    "models and RAG methods."
                ]
            }
        ]
    }

    def run(forms):
        row = {
            "concept": "RAG methods",
            "surface_forms": list(forms),
            "status": "direct",
            "claimable": True,
            "evidence": "we[0].r[0]",
        }
        floored = _enforce_denial_stance([row], denials, vault)
        _, forbidden = split_ledger_for_prompt(floored)
        return floored[0]["status"], forbidden, forbidden_terms_in_draft(draft, floored)

    # What the founder's run had: the denial reaches nothing.
    status, forbidden, present = run(["RAG", "RAG methods"])
    assert status == "direct"
    assert forbidden == []
    assert present == []

    # With the component term the v5 rule asks for, all three fire.
    status, forbidden, present = run(["RAG", "RAG methods", "retrieval pipeline"])
    assert status == "denied"
    assert "RAG methods" in forbidden
    assert "RAG methods" in present


@pytest.mark.parametrize("component", ["vector databases", "retrieval pipeline"])
def test_either_denied_component_is_enough_to_floor_the_parent(component):
    from applire.services.keyword_ledger import _enforce_denial_stance

    floored = _enforce_denial_stance(
        [
            {
                "concept": "RAG methods",
                "surface_forms": ["RAG", "RAG methods", component],
                "status": "direct",
                "claimable": True,
                "evidence": "we[0].r[0]",
            }
        ],
        ["vector databases", "embedding and reranking", "retrieval pipeline"],
        "databricks\nlarge language models",
    )
    assert floored[0]["status"] == "denied"
    assert floored[0]["claimable"] is False
