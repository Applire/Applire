# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#427 / ADR-061 amended 2026-08-02 — the classifier's internal rationale must
never become the ledger's publishable evidence.

Charter run 15 delivered a cover letter reading "…habe ich Grundkenntnisse in
SAP PP/MM" against a vault holding daily PP/MM use and a Key-User role during a
SAP rollout — both blind reviewers' only named inconsistency. No model
misbehaved: the gap prompt asked the classifier to name the declared
proficiency ceiling inside ``reason``, ``reason`` was copied verbatim onto
ledger ``evidence``, and four generation-facing renderers emit ``evidence`` to
the writers and reviewers of BOTH document chains. The reviewer duly demanded
the term be surfaced "(basic proficiency)" and the corrector complied.

The fix is structural rather than a rule: the ceiling moves to an internal
field that has no path into the ledger.
"""
from applire.prompts.gap_analysis import SYSTEM_PROMPT
from applire.services.gap import (
    _LEDGER_PUBLISHABLE_KEYS,
    ledger_input_from_classification,
)

# The run-15 shape, verbatim in structure.
RUN15_SAP = {
    "requirement": "SAP",
    "status": "partial",
    "reason": "Tägliche Arbeit mit SAP PP und MM sowie Key-User beim SAP-Rollout",
    "classification_note": "SAP (basic proficiency) — unter der geforderten Tiefe für 'sicheren Umgang'",
    "surface_forms": ["SAP"],
}


def test_internal_note_never_reaches_the_ledger():
    row = ledger_input_from_classification(RUN15_SAP)
    assert row["evidence"] == RUN15_SAP["reason"]
    blob = repr(row)
    assert "classification_note" not in row
    assert "basic proficiency" not in blob, (
        f"the declared-proficiency ceiling reached a publishable field: {blob}"
    )
    assert "Grundkenntnisse" not in blob


def test_ledger_input_emits_only_publishable_keys():
    """A future field added to the classifier response must not silently become
    publishable — the whitelist is the control, not reviewer vigilance."""
    row = ledger_input_from_classification(
        {**RUN15_SAP, "some_future_internal_field": "declared: basic"}
    )
    assert set(row) == set(_LEDGER_PUBLISHABLE_KEYS)
    assert "declared: basic" not in repr(row)


def test_prompt_routes_the_ceiling_away_from_reason():
    """The rule and the schema must agree — the run-15 defect was that they
    contradicted each other inside one prompt."""
    assert "classification_note" in SYSTEM_PROMPT, "the internal field must exist in the schema"
    # The ceiling rule must not send the proficiency into `reason` any more.
    assert 'proficiency in "reason"' not in SYSTEM_PROMPT
    assert 'proficiency in `reason`' not in SYSTEM_PROMPT


def test_reason_is_described_as_what_the_candidate_can_claim():
    """`reason` doubling as 'the evidence' AND the classification rationale is
    the defect itself; the schema must no longer say the two are the same."""
    assert "(this is the evidence)" not in SYSTEM_PROMPT
