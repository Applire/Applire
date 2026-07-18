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

__all__ = [
    "ORACLE_STATED_LIMIT",
    "Claim",
    "ClaimResult",
    "ClaimVerdict",
    "TruthfulnessReport",
    "audit_document",
    "verify_claim",
]
