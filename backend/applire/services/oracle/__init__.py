# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Truthfulness Oracle service (ADR-052, E043).

Deterministic-first verification of application documents against the master
profile ("the vault"). Public API: :func:`audit_document` / :func:`verify_claim`.
"""
from applire.schemas.oracle import (
    ORACLE_STATED_LIMIT,
    Claim,
    ClaimResult,
    ClaimVerdict,
    TruthfulnessReport,
)
from applire.services.oracle.audit import audit_document, verify_claim

#: The audit seam's reason code when a report is incomplete (MD-G13).
PLACEMENT_UNAVAILABLE = "placement_unavailable"


def incomplete_reason(report: "TruthfulnessReport | dict | None") -> str | None:
    """MD-G13 — why a finished truth audit is nevertheless INCOMPLETE, or None.

    A letter's placement judgement that was asked and gave no usable answer leaves
    the wrong-employer check undone for that claim: the audit seam then records the
    audit as not run (``audit_state``) and the document is held as "not
    re-checked" (MD-G9) until a good re-check or the person's decision. Reads a
    report model or its stored dict; anything else (None, a legacy row without the
    field, a value that is not a count) is not incomplete for this reason."""
    if isinstance(report, TruthfulnessReport):
        count = report.placement_unavailable
    elif isinstance(report, dict):
        count = report.get("placement_unavailable")
    else:
        return None
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return PLACEMENT_UNAVAILABLE
    return None


__all__ = [
    "ORACLE_STATED_LIMIT",
    "PLACEMENT_UNAVAILABLE",
    "Claim",
    "ClaimResult",
    "ClaimVerdict",
    "TruthfulnessReport",
    "audit_document",
    "incomplete_reason",
    "verify_claim",
]
