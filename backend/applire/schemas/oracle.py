# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Truthfulness Oracle contracts (ADR-052, E043).

The Oracle verifies a document against the master profile (the vault) and
returns a typed, receipt-carrying report. Verdict taxonomy v1 (ADR-052 §3):

- ``grounded``       — the claim traces to vault evidence (refs attached)
- ``inflated``       — target-vs-achieved stance mismatch (aspirational evidence
                       rendered as an achieved outcome)
- ``misattributed``  — role-attribution mismatch (v2, ADR-052 §6 / #196): the
                       claim's only backing evidence belongs to a different
                       position than the one it is rendered under
- ``unbacked``       — no vault evidence for the claim (or a figure in it)
- ``unverifiable``   — subjective/soft claim the vault cannot speak to
- ``not_applicable`` — (#237 round-3) a statement about the TARGET EMPLOYER,
                       not the candidate (e.g. a JD-sourced fact about the
                       recipient company) — structurally outside the vault's
                       domain (a different reviewer, ADR-021, validates these
                       against the JD). Also (#282, wave 7) a PURE denial or
                       third-party delegation ("I have not configured X
                       myself") — a negative statement with no positive claim
                       to ground, never a false-negative "unbacked" reading of
                       an honest disclosure. Extracted and shown, never
                       silently dropped, but excluded from the
                       ``unverifiable_dominated`` denominator — see
                       ``TruthfulnessReport.from_results``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, get_args

from pydantic import BaseModel, Field

# ADR-052 §5 — every report carries this stated limit, verbatim.
ORACLE_STATED_LIMIT = (
    "This report verifies consistency between the document and your profile "
    "(the vault). It cannot prove the vault itself: profile claims are "
    "self-attested; interview provenance mitigates but does not eliminate this."
)

# The current report schema version (bump on breaking shape changes; the
# marker data files carry their own version field). 1.1 = the additive
# ``misattributed`` verdict + fifth counts key (Oracle v2 role attribution).
# 1.2 = the additive ``not_applicable`` verdict + sixth counts key (#237
# round-3, employer-fact claims).
ORACLE_REPORT_VERSION = "1.2"

Verdict = Literal[
    "grounded", "inflated", "misattributed", "unbacked", "unverifiable",
    "not_applicable",
]

# Checker ids, carried on every verdict so a report line is attributable to
# the code (or the narrow entailment call) that produced it.
CheckerId = Literal[
    "numbers", "grounding", "stance", "attribution", "entailment", "extraction"
]

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
    # ``clause`` (#237): a narrative sentence decomposed into a smaller,
    # independently checkable fragment — the letter path's answer to the
    # F14 "one multi-fact sentence never clears the coverage floor" defect.
    kind: Literal["sentence", "bullet", "skill", "clause"] = "sentence"
    # The source WorkEntry.id of the position this claim is rendered under
    # (carried on TailoredWorkEntry.id since US187). None for role-agnostic
    # surfaces (summary, skills) and legacy/id-less tailored data — the
    # attribution matcher then stays silent (fails open to v1 behaviour).
    # #237: letter claims are NOT unconditionally role-agnostic anymore — a
    # sentence naming exactly one known employer/project is stamped with that
    # role's id too (see extract.extract_claims_from_letter), so the same
    # matcher becomes reachable for the letter path.
    source_experience_id: str | None = None
    # #248 — every experience/project id whose employer/project name is named
    # LOOSELY (word-boundary substring, legal-form-suffix tolerant,
    # ambiguity across same-company duplicate ids and multiple distinct
    # employers alike TOLERATED) in the SENTENCE this claim/clause was
    # extracted from. Deliberately a separate, more permissive signal than
    # ``source_experience_id`` (the strict "exactly one candidate, fail open
    # on any ambiguity" anchor, #237): it lets the non-figure ownership check
    # (``oracle.audit._unattributable_evidence_flag``) tell "this clause's
    # OWN sentence already names an owner of its evidence" apart from "the
    # letter names an owner somewhere else, but not here" — the distinction
    # that keeps an honest, single-employer sentence grounded even when the
    # strict anchor couldn't stamp it (e.g. the vault's legal-form company
    # name vs. the letter's shortened mention). Empty for non-letter callers.
    sentence_named_ids: frozenset[str] = Field(default_factory=frozenset)
    # #237 round-3: True when this clause/sentence is a statement about the
    # TARGET EMPLOYER (mentions the recipient company, or continues a run of
    # such sentences within the same paragraph, with NO first-person pronoun
    # anywhere) rather than the candidate — see
    # ``extract.extract_claims_from_letter``'s employer-fact classification.
    # ``verify_claim`` short-circuits these to ``not_applicable`` before any
    # vault-grounding attempt; always ``False`` for non-letter callers.
    is_employer_fact: bool = False
    # #282 (wave 7): True when this clause/sentence is a PURE denial or
    # third-party delegation ("I have not configured X myself"; "X was
    # handled by our system engineer") with NO positive claim of its own —
    # the vault holds no "evidence of absence", so these can never verdict
    # ``grounded`` and must not count toward ``unverifiable_dominated``
    # either. See ``extract.extract_claims_from_letter``'s denial
    # classification (``_is_pure_denial_clause``). Conservative by
    # construction: a clause that denies one thing but ALSO smuggles a real
    # positive claim ("I have not led AI teams, though I effectively ran the
    # ML org") stays ``False`` — the #207/#278 lesson, over-fire is as
    # damaging as under-fire. ``verify_claim`` short-circuits a ``True``
    # claim to ``not_applicable`` exactly like ``is_employer_fact``; always
    # ``False`` for non-letter callers.
    is_denial: bool = False


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
    # Verdict -> count, always carrying all five keys.
    counts: dict[str, int] = Field(default_factory=dict)
    # #237 — "an unverifiable-dominated report should itself fail louder":
    # True when STRICTLY MORE THAN HALF of all claims verdicted
    # ``unverifiable`` (a tie, e.g. 2/4, is not dominated). A backend-computed
    # fact rather than a UI-only heuristic re-derived from raw counts, so any
    # report consumer (frontend panel, future export, agent channel) sees the
    # same judgement. ``False`` for an empty report — no claims, nothing
    # dominates.
    unverifiable_dominated: bool = False
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
        # Derived from the Verdict vocabulary so adding a verdict can never
        # desync the counts keys (KeyError at audit time otherwise).
        counts = {v: 0 for v in get_args(Verdict)}
        for r in results:
            counts[r.verdict.verdict] += 1
        # #237 round-3: ``not_applicable`` (employer-fact) claims are shown
        # but never count toward the ratio — a letter that correctly engages
        # with the employer (#255) must not be penalised for it.
        checkable_total = len(results) - counts["not_applicable"]
        dominated = (
            checkable_total > 0
            and counts["unverifiable"] / checkable_total > 0.5
        )
        return cls(
            document_kind=document_kind,
            claims=results,
            counts=counts,
            unverifiable_dominated=dominated,
        )


class TruthfulnessReportResponse(BaseModel):
    """US246 API envelope — mirror of ``ATSReportResponse`` (ADR-039 pattern):
    ``report`` is null until generation + self-audit complete (or when the
    audit failed / the row predates Tiramisu)."""

    document_id: uuid.UUID
    status: str
    report: TruthfulnessReport | None = None
