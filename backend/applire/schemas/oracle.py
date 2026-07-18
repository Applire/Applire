# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Truthfulness Oracle contracts (ADR-052, E043).

The Oracle verifies a document against the master profile (the vault) and
returns a typed, receipt-carrying report. Verdict taxonomy v1 (ADR-052 §3):

- ``grounded``     — the claim traces to vault evidence (refs attached)
- ``inflated``     — target-vs-achieved stance mismatch (aspirational evidence
                     rendered as an achieved outcome)
- ``unbacked``     — no vault evidence for the claim (or a figure in it)
- ``unverifiable`` — subjective/soft claim the vault cannot speak to

``misattributed`` (role attribution) is deliberately v2 (ADR-052 §6).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# ADR-052 §5 — every report carries this stated limit, verbatim.
ORACLE_STATED_LIMIT = (
    "This report verifies consistency between the document and your profile "
    "(the vault). It cannot prove the vault itself: profile claims are "
    "self-attested; interview provenance mitigates but does not eliminate this."
)

# The current report schema version (bump on breaking shape changes; the
# marker data files carry their own version field).
ORACLE_REPORT_VERSION = "1.0"

Verdict = Literal["grounded", "inflated", "unbacked", "unverifiable"]

# Checker ids, carried on every verdict so a report line is attributable to
# the code (or the narrow entailment call) that produced it.
CheckerId = Literal["numbers", "grounding", "stance", "entailment", "extraction"]

Stance = Literal["aspirational", "achieved"]

DocumentKind = Literal["cv", "cover_letter", "external"]


class EvidenceRef(BaseModel):
    """A pointer into the vault backing (or contradicting) a claim.

    ``kind=profile_path`` refs address the profile JSONB by dotted path
    (e.g. ``work_experience[1].achievements[0]``). ``kind=enrichment_record``
    refs carry an ADR-046 receipt id from ``metadata.enrichment_history`` —
    the provenance trail for *how* that evidence entered the vault.
    """

    kind: Literal["profile_path", "enrichment_record"]
    ref: str
    excerpt: str = ""


class Claim(BaseModel):
    """One checkable statement extracted from the document."""

    text: str
    # Dotted location inside the source document, e.g. "summary[0]",
    # "work_history[1].bullets[2]", "body.paragraphs[0][1]", "text[4]".
    location: str
    kind: Literal["sentence", "bullet", "skill"] = "sentence"


class ClaimVerdict(BaseModel):
    verdict: Verdict
    checker: CheckerId
    evidence: list[EvidenceRef] = Field(default_factory=list)
    # Short English audit note (the UI localizes verdict labels; the detail is
    # shown verbatim as supporting context, like ATS check details).
    detail: str | None = None


class ClaimResult(BaseModel):
    claim: Claim
    verdict: ClaimVerdict


class TruthfulnessReport(BaseModel):
    version: str = ORACLE_REPORT_VERSION
    document_kind: DocumentKind
    claims: list[ClaimResult] = Field(default_factory=list)
    # Verdict -> count, always carrying all four keys.
    counts: dict[str, int] = Field(default_factory=dict)
    # ADR-052 §5 — never omitted, never blocks delivery in v1 (ADR-040
    # attestation remains the delivery gate).
    stated_limit: str = ORACLE_STATED_LIMIT
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @classmethod
    def from_results(
        cls, document_kind: DocumentKind, results: list[ClaimResult]
    ) -> "TruthfulnessReport":
        counts = {"grounded": 0, "inflated": 0, "unbacked": 0, "unverifiable": 0}
        for r in results:
            counts[r.verdict.verdict] += 1
        return cls(document_kind=document_kind, claims=results, counts=counts)


class TruthfulnessReportResponse(BaseModel):
    """US246 API envelope — mirror of ``ATSReportResponse`` (ADR-039 pattern):
    ``report`` is null until generation + self-audit complete (or when the
    audit failed / the row predates Tiramisu)."""

    document_id: uuid.UUID
    status: str
    report: TruthfulnessReport | None = None
