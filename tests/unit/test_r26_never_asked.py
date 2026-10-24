# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""R2-6 (5) (founder ruling 2026-10-24, Hazelnut build 4) — "Never asked" checks
what the interview did before it claims.

Round 2, felix-056: the cover-letter page said "you were never asked: Payment
settlement" after the interview asked twice on the "Payment Settlement &
Payouts" gap and Felix denied twice ("settlement or payout rails/flows"). The
fixture is that gap analysis row as persisted (round-2 DB, gap_analyses
7d8f1442, ledger and clusters cut to the settlement rows; ids replaced).
"""

from __future__ import annotations

import copy

from applire.schemas.gap import GapAnalysisResponse

FELIX_ROW = {
    "id": "7d8f1442-0000-4000-8000-000000000001",
    "job_analysis_id": "6b9c28fb-0000-4000-8000-000000000002",
    "profile_id": "00000000-0000-4000-8000-000000000003",
    "match_score": 0.75,
    "critical_gaps": [],
    "minor_gaps": [],
    "strengths": [],
    "keyword_gaps": [],
    "created_at": "2026-10-23T22:26:09.78424+00:00",
    "keyword_ledger": [
        {
            "status": "gap",
            "concept": "Payment settlement",
            "sources": [
                "required"
            ],
            "evidence": "",
            "claimable": False,
            "fit_weight": 1.0,
            "surface_forms": [
                "Payment settlement"
            ]
        },
        {
            "status": "gap",
            "concept": "Payout flows",
            "sources": [
                "required"
            ],
            "evidence": "Not claimable: no vault evidence for this concept outside a related limit the candidate stated (no statement was made about this term itself).",
            "claimable": False,
            "fit_weight": 1.0,
            "surface_forms": [
                "Payout flows"
            ]
        },
        {
            "status": "denied",
            "concept": "Crypto settlement",
            "sources": [
                "nice_to_have"
            ],
            "evidence": "Candidate explicitly stated a limit here (interview).",
            "claimable": False,
            "fit_weight": 0.5,
            "surface_forms": [
                "Crypto settlement"
            ]
        },
        {
            "status": "denied",
            "concept": "Blockchain settlement",
            "sources": [
                "nice_to_have"
            ],
            "evidence": "Candidate explicitly stated a limit here (interview).",
            "claimable": False,
            "fit_weight": 0.5,
            "surface_forms": [
                "Blockchain settlement"
            ]
        },
        {
            "status": "gap",
            "concept": "Payout rails",
            "sources": [
                "keyword"
            ],
            "evidence": "Not claimable: no vault evidence for this concept outside a related limit the candidate stated (no statement was made about this term itself).",
            "claimable": False,
            "fit_weight": 0.0,
            "surface_forms": [
                "Payout rails"
            ]
        },
        {
            "status": "partial",
            "concept": "Exactly-once processing",
            "sources": [
                "keyword"
            ],
            "evidence": "Implemented idempotent consumers with PostgreSQL offset bookkeeping so replays never double-bill.",
            "claimable": True,
            "fit_weight": 0.0,
            "surface_forms": [
                "Exactly-once processing",
                "exactly once processing"
            ],
            "adjacent_evidence": "idempotent consumers with PostgreSQL offset bookkeeping"
        }
    ],
    "gap_clusters": [
        {
            "id": "cluster-payment-settlement-payouts",
            "gaps": [
                "Payment settlement",
                "Payout flows",
                "Payout rails",
                "Exactly-once processing"
            ],
            "label": "Payment Settlement & Payouts",
            "outcome": {
                "asked": 2,
                "covered": [
                    "Idempotency"
                ],
                "declined": [],
                "left_open": False,
                "session_ids": [
                    "9c66c3a4-0000-4000-8000-000000000004"
                ]
            },
            "category": "C",
            "coverage": "partly_covered",
            "jd_skills": [
                "Payment settlement",
                "Payout flows"
            ],
            "jd_context": "The role expects reliable payment settlement and payout flows with exactly-once and idempotent processing because financial transactions must be completed accurately."
        },
        {
            "id": "cluster-crypto-blockchain",
            "gaps": [],
            "label": "Crypto Settlement",
            "outcome": {
                "asked": 1,
                "covered": [],
                "declined": [
                    "Crypto settlement",
                    "Blockchain settlement"
                ],
                "left_open": False,
                "session_ids": [
                    "9c66c3a4-0000-4000-8000-000000000004"
                ]
            },
            "category": "B",
            "coverage": "declined",
            "jd_skills": [
                "Crypto settlement",
                "Blockchain settlement"
            ],
            "jd_context": "The role values crypto and blockchain settlement experience because the product may support digitally native financial rails."
        }
    ]
}


def _concepts(resp: GapAnalysisResponse) -> list[str]:
    return [e.concept for e in resp.unasked_requirements]


def test_felix_payment_settlement_asked_twice_is_not_never_asked():
    resp = GapAnalysisResponse.model_validate(copy.deepcopy(FELIX_ROW))
    assert "Payment settlement" not in _concepts(resp)
    assert _concepts(resp) == []


def test_the_same_row_in_a_cluster_nobody_asked_about_stays_never_asked():
    row = copy.deepcopy(FELIX_ROW)
    for c in row["gap_clusters"]:
        c["outcome"] = {"asked": 0, "covered": [], "declined": [], "left_open": False, "session_ids": []}
    assert _concepts(GapAnalysisResponse.model_validate(row)) == ["Payment settlement"]


def test_a_declined_cluster_member_is_not_never_asked():
    row = copy.deepcopy(FELIX_ROW)
    pay = next(c for c in row["gap_clusters"] if c["label"].startswith("Payment"))
    pay["outcome"] = {"asked": 0, "covered": [], "declined": ["Payment settlement"], "left_open": False, "session_ids": []}
    pay["gaps"] = [g for g in pay["gaps"] if g != "Payment settlement"]
    assert "Payment settlement" not in _concepts(GapAnalysisResponse.model_validate(row))


def test_a_row_a_stated_limit_covers_leaves_by_the_ledger_floor():
    """The denial half of the ruling: "Payout flows" carries the floor evidence the
    reconcile wrote from Felix's denial, so the ledger predicate already excludes
    it — also with no cluster at all."""
    row = copy.deepcopy(FELIX_ROW)
    row["gap_clusters"] = []
    concepts = _concepts(GapAnalysisResponse.model_validate(row))
    assert "Payout flows" not in concepts and "Payout rails" not in concepts
    assert concepts == ["Payment settlement"]
