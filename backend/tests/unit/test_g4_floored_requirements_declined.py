# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""G-4 (founder, Hazelnut build 4): a requirement FLOORED by the candidate's own
"no" (ADR-059 containment floor, ``DENIAL_FLOOR_EVIDENCE``) must not raise the
open count. It goes into the EXISTING declined class, its card can reach
"Erledigt", and it is not asked again (R2-9). The floor is unchanged.

Shape from UX round 2 (Felix, job 6b9c28fb, GA 7ed899f2): "Payout flows" was
classified partial; Felix's answer "did not directly work on payment settlement
or payout rails" floored it to gap, and the counter rose 14 → 15.
"""

from applire.schemas.gap import KeywordLedgerEntry
from applire.services.gap_coverage import (
    classify_members,
    initialise_cluster_record,
    is_askable,
    member_statuses,
)
from applire.services.keyword_ledger import DENIAL_FLOOR_EVIDENCE, is_denial_floored

DENIALS = [{"concept": "settlement or payout rails"}]


def _row(concept, status, evidence="", fit=1.0):
    return {
        "concept": concept,
        "status": status,
        "evidence": evidence,
        "claimable": status in ("direct", "partial"),
        "fit_weight": fit,
        "sources": ["required"],
        "surface_forms": [concept],
    }


LEDGER = [
    _row("Payout flows", "gap", DENIAL_FLOOR_EVIDENCE),
    _row("Payment settlement", "gap", ""),
    _row("Stripe", "direct", "Stripe PSP integration at Acme."),
]
CLUSTER = {"id": "cluster-payment-settlement-payouts", "label": "Payment settlement", "gaps": ["Payout flows", "Payment settlement"]}


def test_the_predicate():
    assert is_denial_floored(LEDGER[0])
    assert not is_denial_floored(LEDGER[1])  # an ordinary gap stays open
    assert not is_denial_floored({**LEDGER[0], "status": "denied"})


def test_a_floored_member_is_declined_and_says_so_on_its_chip():
    facts = classify_members(["Payout flows", "Payment settlement"], LEDGER, DENIALS)
    assert facts == {"Payout flows": "declined", "Payment settlement": "open"}
    chips = {c["member"]: c for c in member_statuses(CLUSTER, LEDGER, DENIALS)}
    assert chips["Payout flows"]["status"] == "declined" and chips["Payout flows"]["floored"] is True
    assert "floored" not in chips["Payment settlement"]


def test_a_literal_denial_is_declined_but_not_floored():
    chips = member_statuses(CLUSTER, LEDGER, [{"concept": "Payout flows"}])
    assert chips[0]["status"] == "declined" and "floored" not in chips[0]


def test_the_split_moves_it_out_of_the_open_list():
    rec = initialise_cluster_record(CLUSTER, LEDGER, DENIALS)
    assert rec["gaps"] == ["Payment settlement"]
    assert rec["outcome"]["declined"] == ["Payout flows"]


def test_a_card_whose_last_open_member_is_floored_is_not_asked_again():
    stored = {**CLUSTER, "gaps": ["Payout flows"], "outcome": {"asked": 1, "covered": [], "declined": [], "session_ids": []}, "coverage": "open"}
    assert is_askable(stored, keyword_ledger=LEDGER) is False  # before any re-split
    assert is_askable({**stored, "gaps": ["Payment settlement"]}, keyword_ledger=LEDGER) is True


def test_the_response_row_carries_the_derived_flag():
    e = KeywordLedgerEntry(**LEDGER[0])
    assert e.floored_by_denial is True
    assert KeywordLedgerEntry(**LEDGER[1]).floored_by_denial is False
    # derived, never read from a stored value
    assert KeywordLedgerEntry(**{**LEDGER[1], "floored_by_denial": True}).floored_by_denial is False
