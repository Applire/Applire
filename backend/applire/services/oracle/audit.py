# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US243/US244/US245 — the Oracle aggregator (ADR-052).

Deterministic-first: matchers run before any LLM is consulted, and their red
flags (``unbacked`` figures, ``inflated`` stance) return IMMEDIATELY — the
narrow entailment call is structurally unreachable once a deterministic red
flag exists, which is how "entailment may never overrule a deterministic red
flag" (ADR-052 §2) is enforced. Entailment fires only where deterministic
checks cannot decide, and its budget is hard-capped per document.
"""
from __future__ import annotations

from typing import Any

from applire.constants import (
    ORACLE_ENTAILMENT_MAX_TOKENS,
    ORACLE_MAX_ENTAILMENT_CALLS,
)
from applire.schemas.oracle import (
    Claim,
    ClaimResult,
    ClaimVerdict,
    DocumentKind,
    EvidenceRef,
    TruthfulnessReport,
)
from applire.services.oracle.extract import (
    extract_claims_from_letter,
    extract_claims_from_tailored,
    extract_claims_from_text,
)
from applire.services.oracle.matchers import (
    EvidenceUnit,
    VaultIndex,
    build_vault_index,
    extract_figures,
    ground_skill_claim,
    ground_text_claim,
    match_figures,
)
from applire.services.oracle.matchers.grounding import GROUNDED_MIN_COVERAGE
from applire.services.oracle.stance import classify_stance

_EXCERPT_CHARS = 160


def _evidence_refs(units: list[EvidenceUnit]) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    seen_receipts: set[str] = set()
    for unit in units:
        refs.append(
            EvidenceRef(kind="profile_path", ref=unit.path, excerpt=unit.text[:_EXCERPT_CHARS])
        )
        for rid in unit.receipt_ids:
            if rid not in seen_receipts:
                seen_receipts.add(rid)
                refs.append(EvidenceRef(kind="enrichment_record", ref=rid))
    return refs


class _EntailmentBudget:
    """Per-document cap on entailment calls (ADR-052: narrow and capped)."""

    def __init__(self, limit: int = ORACLE_MAX_ENTAILMENT_CALLS):
        self.remaining = limit

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


_ENTAILMENT_PROMPT = (
    "You are a strict verification function for job-application claims.\n"
    "Compare the DOCUMENT CLAIM against the PROFILE EVIDENCE and return "
    'STRICT JSON: {{"verdict": "grounded" | "inflated" | "unbacked" | "unverifiable"}}.\n'
    "- grounded: the evidence supports the claim as stated\n"
    "- inflated: the evidence is aspirational or weaker than the claim's rendering "
    "(e.g. a target presented as an achieved result)\n"
    "- unbacked: the evidence does not contain or contradicts the claim\n"
    "- unverifiable: subjective, or the evidence cannot decide it\n\n"
    "PROFILE EVIDENCE:\n{evidence}\n\nDOCUMENT CLAIM:\n{claim}"
)

_VALID_ENTAILMENT_VERDICTS = {"grounded", "inflated", "unbacked", "unverifiable"}


async def _entailment(
    claim_text: str,
    evidence_units: list[EvidenceUnit],
    provider: Any,
    budget: _EntailmentBudget,
    fallback: ClaimVerdict,
) -> ClaimVerdict:
    """Narrow, bounded entailment call — used ONLY on deterministically
    undecided claims (never after a red flag; see module docstring)."""
    if provider is None or not budget.take():
        return fallback
    evidence = "\n".join(f"- {u.text}" for u in evidence_units) or "- (no close evidence)"
    try:
        result = await provider.aparse_json(
            _ENTAILMENT_PROMPT.format(evidence=evidence, claim=claim_text),
            temperature=0.0,
            max_tokens=ORACLE_ENTAILMENT_MAX_TOKENS,
        )
    except Exception:
        return fallback
    verdict = result.get("verdict") if isinstance(result, dict) else None
    if verdict not in _VALID_ENTAILMENT_VERDICTS:
        return fallback
    return ClaimVerdict(
        verdict=verdict,  # type: ignore[arg-type]
        checker="entailment",
        evidence=_evidence_refs(evidence_units),
        detail="Narrow entailment verdict (deterministic checks could not decide).",
    )


async def verify_claim(
    claim: Claim | str,
    profile: Any,
    provider: Any | None = None,
    *,
    index: VaultIndex | None = None,
    budget: _EntailmentBudget | None = None,
) -> ClaimVerdict:
    """Verdict for a single claim against the vault (ADR-052 §1).

    ``profile`` accepts a ``MasterProfileData`` or its JSONB dict; pass a
    prebuilt ``index`` when auditing many claims. Deterministic red flags
    return before ``provider`` is ever consulted.
    """
    if isinstance(claim, str):
        claim = Claim(text=claim, location="claim[0]")
    idx = index or build_vault_index(profile)
    budget = budget or _EntailmentBudget()

    # ── skills: shared-predicate grounding, deterministic either way ────────
    if claim.kind == "skill":
        unit = ground_skill_claim(claim.text, idx)
        if unit is not None:
            return ClaimVerdict(
                verdict="grounded", checker="grounding", evidence=_evidence_refs([unit])
            )
        return ClaimVerdict(
            verdict="unbacked",
            checker="grounding",
            detail=f'Skill "{claim.text}" has no vault evidence.',
        )

    # ── 1. number/date provenance (deterministic red flag) ──────────────────
    figures = extract_figures(claim.text)
    fig_match = match_figures(figures, idx)
    if fig_match.unmatched:
        missing = ", ".join(f.raw for f in fig_match.unmatched)
        return ClaimVerdict(
            verdict="unbacked",
            checker="numbers",
            detail=f"No vault evidence for figure(s): {missing}.",
        )

    # ── 2. target-vs-achieved stance (deterministic red flag) ───────────────
    if fig_match.matched:
        evidence_units: list[EvidenceUnit] = []
        for _, units in fig_match.matched:
            for u in units:
                if u not in evidence_units:
                    evidence_units.append(u)
        claim_stance = classify_stance(claim.text)
        unit_stances = [classify_stance(u.text) for u in evidence_units]
        if claim_stance == "achieved":
            aspirational = [
                u for u, s in zip(evidence_units, unit_stances) if s == "aspirational"
            ]
            if aspirational and not any(s == "achieved" for s in unit_stances):
                return ClaimVerdict(
                    verdict="inflated",
                    checker="stance",
                    evidence=_evidence_refs(aspirational),
                    detail=(
                        "Rendered as an achieved outcome, but the vault evidence "
                        "for these figures is aspirational (a target, not a result)."
                    ),
                )
        # US245: entailment ONLY when both sides lack stance markers.
        if claim_stance is None and not any(unit_stances):
            fallback = ClaimVerdict(
                verdict="grounded",
                checker="numbers",
                evidence=_evidence_refs(evidence_units),
                detail="All figures trace to vault evidence.",
            )
            # Adversarial review 2026-07-18 MINOR-1: the figure-carrying units
            # alone (often bare date spans) starve the entailment of the role/
            # org evidence that actually decides the claim — merge in the
            # grounding top units so a true claim isn't over-flagged.
            context_units = list(evidence_units)
            for u in ground_text_claim(claim.text, idx).top_units:
                if u not in context_units:
                    context_units.append(u)
            return await _entailment(claim.text, context_units, provider, budget, fallback)
        return ClaimVerdict(
            verdict="grounded",
            checker="numbers",
            evidence=_evidence_refs(evidence_units),
            detail="All figures trace to vault evidence.",
        )

    # ── 3. figure-free claims: shared-predicate grounding ───────────────────
    grounding = ground_text_claim(claim.text, idx)
    if grounding.content_tokens == 0:
        return ClaimVerdict(
            verdict="unverifiable",
            checker="grounding",
            detail="No checkable content (soft or formulaic statement).",
        )
    if grounding.best_coverage >= GROUNDED_MIN_COVERAGE and grounding.best_unit is not None:
        # Adversarial review 2026-07-18 MAJOR-1: the stance red flag applies
        # HERE too — a claim rendered as achieved whose grounding evidence is
        # purely aspirational is inflated even when the writer dropped the
        # numeral (US245 has no figure restriction). Without this, the report
        # actively endorsed the inflation with the aspirational unit as backing.
        if classify_stance(claim.text) == "achieved":
            top_stances = [classify_stance(u.text) for u in grounding.top_units]
            aspirational = [
                u for u, s in zip(grounding.top_units, top_stances) if s == "aspirational"
            ]
            if aspirational and not any(s == "achieved" for s in top_stances):
                return ClaimVerdict(
                    verdict="inflated",
                    checker="stance",
                    evidence=_evidence_refs(aspirational),
                    detail=(
                        "Rendered as an achieved outcome, but the vault evidence "
                        "grounding this claim is aspirational (a target, not a result)."
                    ),
                )
        return ClaimVerdict(
            verdict="grounded",
            checker="grounding",
            evidence=_evidence_refs([grounding.best_unit]),
        )
    fallback = ClaimVerdict(
        verdict="unverifiable",
        checker="grounding",
        detail="No sufficiently close vault evidence for a deterministic verdict.",
    )
    return await _entailment(claim.text, grounding.top_units, provider, budget, fallback)


async def audit_document(
    document_kind: DocumentKind,
    profile: Any,
    *,
    tailored_data: dict[str, Any] | None = None,
    letter_data: dict[str, Any] | None = None,
    text: str | None = None,
    provider: Any | None = None,
) -> TruthfulnessReport:
    """Full-document audit → :class:`TruthfulnessReport` (ADR-052 §1).

    Exactly one of ``tailored_data`` / ``letter_data`` / ``text`` must be
    provided. The report NEVER blocks delivery in v1 (ADR-040 attestation
    remains the gate) and always carries the ADR-052 §5 stated limit.
    """
    sources = [s for s in (tailored_data, letter_data, text) if s is not None]
    if len(sources) != 1:
        raise ValueError("audit_document needs exactly one of tailored_data/letter_data/text")

    if tailored_data is not None:
        claims = extract_claims_from_tailored(tailored_data)
    elif letter_data is not None:
        claims = extract_claims_from_letter(letter_data)
    else:
        claims = await extract_claims_from_text(text or "", provider=provider)

    index = build_vault_index(profile)
    budget = _EntailmentBudget()
    results: list[ClaimResult] = []
    for claim in claims:
        verdict = await verify_claim(
            claim, profile, provider, index=index, budget=budget
        )
        results.append(ClaimResult(claim=claim, verdict=verdict))

    return TruthfulnessReport.from_results(document_kind, results)
