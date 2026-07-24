# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US243/US244/US245 — the Oracle aggregator (ADR-052).

Deterministic-first: matchers run before any LLM is consulted, and their red
flags (``unbacked`` figures, ``inflated`` stance) return IMMEDIATELY — the
narrow entailment call is structurally unreachable once a deterministic red
flag exists, which is how "entailment may never overrule a deterministic red
flag" (ADR-052 §2) is enforced. Entailment fires only where deterministic
checks cannot decide, and its budget is hard-capped per document.

#243-adjacent (letter figure ownership, live-reproduced 2026-07-24): a letter
clause naming no employer (or two — :func:`extract._find_employer_anchor`
fails open on ambiguity) carries no ``source_experience_id``, so the existing
per-figure attribution red flag (:func:`_attribution_red_flag`, #196) never
fires for it — by design, it only judges claims that ARE anchored. That left
a genuine ownership gap: an unanchored figure whose only vault backing is
owned by a position the letter simply never (or ambiguously) names verdicted
``grounded`` from evidence that, read narratively, belongs to someone else.
:func:`_unattributable_evidence_flag` closes it: for letters only (CV bullets
always carry their real rendered-position id directly, no ambiguity to
resolve), an unanchored claim backed exclusively by owned units downgrades to
``unverifiable`` UNLESS (a) the claim's OWN enclosing sentence already names
one of the owning positions (``Claim.sentence_named_ids``, #248 — see below),
or (b) the letter names exactly one employer/project overall and that one
owns the evidence — the same "ambiguity beats false certainty" rule the
anchor matcher itself already applies.

#248 (non-figure letter ownership, live-reproduced 2026-07-24,
generated_cover_letters 37ee8f77-...): df78cac's fix above was scoped to
FIGURES only (the per-figure loop in ``verify_claim`` §2). A figure-FREE
clause — "a deterministic verification layer ensuring trustworthiness.",
BioNTech-sentence-adjacent but exclusively Applire-owned — reaches
``verify_claim``'s SEPARATE figure-free grounding branch (§3), which had no
equivalent ownership check at all: an unanchored figure-free claim backed
exclusively by owned evidence simply verdicted ``grounded``, full stop.
:func:`_unattributable_evidence_flag` is now generalized (renamed from
``_unattributable_figure_flag``) to accept ANY evidence-unit list, and §3
calls it exactly like §2c does for figures. The extra ``sentence_named_ids``
escape (a) is what keeps this from over-dropping an honest, single-employer
sentence whose evidence genuinely belongs to it but whose STRICT anchor
failed for an unrelated reason (e.g. the vault's legal-form company name vs.
the letter's shortened mention, #248) — without it, the letter-wide escape
(b) alone cannot tell "this clause's own sentence names its true owner" apart
from "the letter names an owner somewhere else, but not here", and would
wrongly flag the former too (see ``test_oracle_letter_nonfigure_ownership.py``).
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
    letter_named_experience_ids,
)
from applire.services.oracle.matchers import (
    EvidenceUnit,
    VaultIndex,
    build_vault_index,
    extract_figures,
    find_foreign_owner,
    ground_skill_claim,
    ground_text_claim,
    ground_via_skill_union,
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


def _attribution_red_flag(
    source_id: str | None, units: list[EvidenceUnit]
) -> ClaimVerdict | None:
    """The shared misattribution red flag (#196, ADR-052 §6) — deterministic.

    Wraps :func:`find_foreign_owner` (the matcher holds the exclusively-foreign
    rule) into a verdict; ``None`` when attribution is fine.
    """
    unit = find_foreign_owner(source_id, units)
    if unit is None:
        return None
    return ClaimVerdict(
        verdict="misattributed",
        checker="attribution",
        evidence=_evidence_refs([unit]),
        detail=(
            f"Backed only by evidence from a different position ({unit.path}) — "
            "the claim is rendered under a role it does not belong to."
        ),
    )


def _unattributable_evidence_flag(
    source_id: str | None,
    letter_named_ids: frozenset[str] | None,
    sentence_named_ids: frozenset[str],
    units: list[EvidenceUnit],
) -> ClaimVerdict | None:
    """#243-adjacent / #248 — the letter ownership check (module docstring).

    Generalized (#248) from figure-only evidence to ANY evidence-unit list —
    both the per-figure loop (§2c) and the figure-free grounding path (§3) of
    :func:`verify_claim` call this with their own qualifying units.

    Complements :func:`_attribution_red_flag`, which only fires when a claim
    IS anchored to a rendered position (``source_id`` set) and can therefore
    be provably wrong. An UNANCHORED letter claim (no employer named, or two)
    whose ENTIRE vault backing is owned — by construction, every ``units``
    entry has non-empty ``owner_ids`` — and none of those owners are named
    ANYWHERE else in the letter either, is genuinely unattributable: neither
    "this employer" nor "not this employer" is decidable, so it must not
    verdict ``grounded`` (that would silently launder a blend exactly like
    #237/F14, just without the anchor to catch it). Downgrades to
    ``unverifiable`` with an honest note instead of fabricating a
    misattribution verdict the claim's own text never claims.

    Fails open (returns ``None``, no flag) whenever:
      * the claim IS anchored (``source_id`` is not ``None`` — the existing
        attribution check already covers that case), or
      * ``letter_named_ids`` is ``None`` (a CV claim, or any caller that
        didn't opt in — the CV path never needs this: bullets always carry
        their real rendered-position id directly, no anchoring ambiguity), or
      * any backing unit is role-agnostic (``not u.owner_ids``) — role-
        agnostic evidence (summary, skills, stories with no experience_refs)
        clears any position, anchored or not, or
      * ``sentence_named_ids`` (#248) — every employer/project LOOSELY named
        anywhere in THIS CLAIM'S OWN enclosing sentence, legal-form-suffix
        and same-company-duplicate-id ambiguity tolerated (unlike the strict
        anchor) — intersects the backing owners. This is the narrower,
        claim-local escape: a sentence naming its own true owner (just not
        crisply enough for the strict anchor, e.g. the vault's legal entity
        name vs. the letter's shortened mention) is NOT the genuinely
        unattributable case, even when the letter overall names several
        employers. Checked BEFORE the letter-wide escape below because it is
        strictly more precise (ground truth: an honest "In my recent role at
        BioNTech..." sentence must clear here even in a letter that also
        names Applire elsewhere — the letter-wide escape alone would wrongly
        flag it once BioNTech is correctly counted as "named elsewhere",
        #248), or
      * the letter names EXACTLY ONE employer/project overall and its id is
        AMONG the backing units' owners — an unambiguous single-employer
        letter, just not repeated in THIS clause (legitimate unanchored
        summary-style sentence). Membership, not subset: a project unit's
        ``owner_ids`` also carries the project's OWN id alongside its
        resolved parent work id (US187 nesting, vault.py), but a project
        name is a candidate anchor mapped straight to that PARENT id
        (``_employer_anchor_candidates``) — the bare project id can never
        itself appear in ``letter_named_ids``, so requiring the full owner
        set to be named would never clear a project-owned claim at all.

    A letter naming TWO OR MORE employers/projects anywhere, with NEITHER
    escape clearing it — this is the live #243-adjacent / #248 shape
    (BioNTech named in one sentence, Applire in the next, then a bare
    unanchored continuation naming neither) where a human reader would infer
    the WRONG employer from local context even though the right one is
    technically "named somewhere" in the document. Mirrors
    :func:`_find_employer_anchor`'s own fail-open-on-ambiguity rule — two or
    more candidates is never enough to clear an unanchored claim, only
    exactly one is (or the claim's own sentence naming the true owner
    directly).
    """
    if source_id is not None or letter_named_ids is None or not units:
        return None
    if any(not u.owner_ids for u in units):
        return None
    owners = {oid for u in units for oid in u.owner_ids}
    if sentence_named_ids and sentence_named_ids & owners:
        return None
    if len(letter_named_ids) == 1 and letter_named_ids & owners:
        return None
    return ClaimVerdict(
        verdict="unverifiable",
        checker="attribution",
        evidence=_evidence_refs(units),
        detail=(
            "This claim's only vault evidence is owned by a position, but "
            "this claim names no employer and the letter's own context "
            "doesn't unambiguously point to that one position — attribution "
            "cannot be verified."
        ),
    )


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
    source_id: str | None = None,
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
    if verdict == "grounded":
        # #196 adversarial review: "grounded" on exclusively-foreign evidence
        # is still misattribution — the paraphrase evasion (light rewording
        # drops coverage below the deterministic floor, entailment then
        # endorses the wrong position's evidence). Determinism outranks the
        # LLM here exactly as it does for red flags.
        flag = _attribution_red_flag(source_id, evidence_units)
        if flag is not None:
            return flag
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
    letter_named_ids: frozenset[str] | None = None,
) -> ClaimVerdict:
    """Verdict for a single claim against the vault (ADR-052 §1).

    ``profile`` accepts a ``MasterProfileData`` or its JSONB dict; pass a
    prebuilt ``index`` when auditing many claims. Deterministic red flags
    return before ``provider`` is ever consulted. ``letter_named_ids``
    (#243-adjacent) is the set of experience ids named anywhere in a letter
    being audited — ``None`` for CV/text callers, who never need it (see
    :func:`_unattributable_evidence_flag`).
    """
    if isinstance(claim, str):
        claim = Claim(text=claim, location="claim[0]")
    idx = index or build_vault_index(profile)
    budget = budget or _EntailmentBudget()
    # #196: a stamped id the vault does not know (backfill heuristics, stale
    # data) disables the attribution matcher — fail open, never flag on it.
    source_id = claim.source_experience_id
    if source_id is not None and source_id not in idx.experience_ids:
        source_id = None

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
        # ── 2b. role attribution (deterministic red flag, #196) ─────────────
        # Per FIGURE, before entailment: a figure whose vault occurrences all
        # belong to a different position is misattribution — checked per
        # figure so an ambient same-role year can never launder a foreign
        # achievement figure. Years are exempt entirely (tenure-ambient: date
        # spans and "since 20XX" phrasing overlap across positions).
        for fig, fig_units in fig_match.matched:
            if fig.kind == "year":
                continue
            flag = _attribution_red_flag(source_id, fig_units)
            if flag is not None:
                return flag
        # ── 2c. unattributable figure (unanchored letter claim, #243-adjacent)
        for fig, fig_units in fig_match.matched:
            if fig.kind == "year":
                continue
            flag = _unattributable_evidence_flag(
                source_id, letter_named_ids, claim.sentence_named_ids, fig_units
            )
            if flag is not None:
                return flag
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
            return await _entailment(
                claim.text, context_units, provider, budget, fallback, source_id
            )
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
        # ── role attribution on the grounding path (#196) ───────────────────
        # The backing set is EVERY unit clearing the coverage floor
        # (grounding.qualifying_units — deliberately not the top-3 entailment
        # window); only when all of them belong to a foreign position is the
        # claim misattributed (same-role or role-agnostic backing clears it).
        flag = _attribution_red_flag(source_id, grounding.qualifying_units)
        if flag is not None:
            return flag
        # ── unattributable figure-free claim (unanchored letter claim, #248) ─
        # The non-figure counterpart of §2c: an unanchored letter claim whose
        # entire grounding backing is owned is the SAME genuine-unattributable
        # shape a figure carries, just without a figure to route through the
        # per-figure loop above (df78cac was scoped to figures only).
        flag = _unattributable_evidence_flag(
            source_id, letter_named_ids, claim.sentence_named_ids, grounding.qualifying_units
        )
        if flag is not None:
            return flag
        return ClaimVerdict(
            verdict="grounded",
            checker="grounding",
            evidence=_evidence_refs([grounding.best_unit]),
        )

    # ── 3b. skill-union fallback for enumeration clauses (adversarial-pass
    # residual, 2026-07-23) ─────────────────────────────────────────────────
    # The attribution check runs FIRST, against the single-unit grounding's
    # OWN best-effort evidence (``top_units`` — the best-scoring units found,
    # regardless of whether they cleared the coverage floor). A claim whose
    # only real evidence belongs to a foreign position must flag misattributed
    # here and stop — it must never be allowed to reach the skill-union
    # fallback and get rescued by role-agnostic skill evidence.
    if source_id is not None:
        pre_union_flag = _attribution_red_flag(source_id, grounding.top_units)
        if pre_union_flag is not None:
            return pre_union_flag
    union_grounding = ground_via_skill_union(claim.text, idx)
    if union_grounding is not None:
        return ClaimVerdict(
            verdict="grounded",
            checker="grounding",
            evidence=_evidence_refs(union_grounding.qualifying_units),
            detail=(
                "Grounded via the union of vault skill evidence "
                "(multi-skill enumeration clause)."
            ),
        )

    fallback = ClaimVerdict(
        verdict="unverifiable",
        checker="grounding",
        detail="No sufficiently close vault evidence for a deterministic verdict.",
    )
    return await _entailment(
        claim.text, grounding.top_units, provider, budget, fallback, source_id
    )


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

    # #243-adjacent: only a letter audit knows/needs "named anywhere in this
    # document" — CV bullets carry their real rendered-position id directly,
    # no anchoring ambiguity, so ``letter_named_ids`` stays None for them.
    letter_named_ids: frozenset[str] | None = None
    if tailored_data is not None:
        claims = extract_claims_from_tailored(tailored_data)
    elif letter_data is not None:
        claims = extract_claims_from_letter(letter_data, profile)
        letter_named_ids = letter_named_experience_ids(letter_data, profile)
    else:
        claims = await extract_claims_from_text(text or "", provider=provider)

    index = build_vault_index(profile)
    budget = _EntailmentBudget()
    results: list[ClaimResult] = []
    for claim in claims:
        verdict = await verify_claim(
            claim,
            profile,
            provider,
            index=index,
            budget=budget,
            letter_named_ids=letter_named_ids,
        )
        results.append(ClaimResult(claim=claim, verdict=verdict))

    return TruthfulnessReport.from_results(document_kind, results)
