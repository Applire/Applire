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
from applire.services.ats_audit import skill_tokens, surface_present
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
    ground_via_role_union,
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
    source_id: str | None,
    units: list[EvidenceUnit],
    index: VaultIndex | None = None,
) -> ClaimVerdict | None:
    """The shared misattribution red flag (#196, ADR-052 §6) — deterministic.

    Wraps :func:`find_foreign_owner` (the matcher holds the exclusively-foreign
    rule) into a verdict; ``None`` when attribution is fine. ``index`` (#237
    round-3) supplies ``same_employer_ids`` so a claim's SAME-COMPANY sibling
    roles are never treated as foreign — omit only when no index is
    available (fails open to the pre-round-3 behaviour, never over-flags).
    """
    same_employer_ids = (
        index.same_employer_ids.get(source_id, frozenset())
        if index is not None and source_id
        else frozenset()
    )
    unit = find_foreign_owner(source_id, units, same_employer_ids)
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


# #237 (run-4 residual): the bar for "this claim is at least SOMEWHAT a
# genuine restatement of vault content" — deliberately well BELOW
# ``GROUNDED_MIN_COVERAGE`` (0.6, the bar for an independently grounded
# claim). This check only fires once every ownership escape has already
# failed, i.e. the claim could never verdict ``grounded`` here regardless;
# its sole job is discriminating "plausible restatement, ownership just
# unprovable" (stay unverifiable) from "a number wearing unrelated content"
# (escalate to unbacked) — see ``_unattributable_evidence_flag``.
_UNATTRIBUTABLE_CONTENT_FLOOR = 0.35


def _owner_scoped_coverage(
    claim_text: str, index: VaultIndex, owners: set[str]
) -> float:
    """Best single-unit coverage of ``claim_text`` against evidence OWNED BY
    ``owners`` only (#237 run-4 residual).

    Deliberately excludes role-agnostic evidence (professional summary,
    skills) — that prose is broad and generic BY DESIGN (it describes the
    whole career), so it coincidentally overlaps almost any leadership-
    flavoured sentence at 30-50% coverage regardless of whether the claim is
    genuine, which would make the vault-wide check too permissive to
    discriminate a fabrication here. Scoping to the SAME position(s) whose
    evidence the claim's figure/content actually matched keeps the question
    honest: "does this claim's content, not just its number, belong to the
    position that number came from?"
    """
    tokens = skill_tokens(claim_text)
    if not tokens:
        return 0.0
    owned_units = [u for u in index.units if u.owner_ids & owners]
    best = 0
    for u in owned_units:
        hits = sum(1 for t in tokens if surface_present(t, u.text_norm))
        best = max(best, hits)
    return best / len(tokens)


def _all_same_employer(ids: frozenset[str], index: VaultIndex) -> bool:
    """True when every id in ``ids`` is a same-employer sibling of every
    other (#237 round-3, ``VaultIndex.same_employer_ids``) — the
    letter-wide escape's generalization from "names exactly one experience
    id" to "names roles at exactly one COMPANY". A single-id set is
    trivially true (the pre-round-3 behaviour, unchanged); an id the vault
    has no company grouping for (e.g. a project) falls back to its own
    singleton group, so an actually-mixed set still correctly fails.
    """
    if not ids:
        return False
    first = next(iter(ids))
    group = index.same_employer_ids.get(first, frozenset({first}))
    return all(i in group for i in ids)


def _unattributable_evidence_flag(
    claim_text: str,
    index: VaultIndex,
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
      * the letter names roles at EXACTLY ONE EMPLOYER overall (#237
        round-3: same-company siblings count as the same employer here too,
        via ``VaultIndex.same_employer_ids`` — a tenure held across several
        internal roles at ONE company, all loosely matched by the
        whole-letter scan, is still an unambiguous single-employer letter)
        and one of those ids is AMONG the backing units' owners — just not
        repeated in THIS clause (legitimate unanchored summary-style
        sentence). Membership, not subset: a project unit's ``owner_ids``
        also carries the project's OWN id alongside its resolved parent
        work id (US187 nesting, vault.py), but a project name is a
        candidate anchor mapped straight to that PARENT id
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

    #237 (run-4 residual): a report dominated by soft ``unverifiable``
    verdicts camouflages a real fabrication as checker conservatism ("no
    sufficiently close evidence" reads the same whether the vault is silent
    OR was silently mismatched). Once every escape above has failed to
    clear the claim, :func:`_owner_scoped_coverage` — the claim's best
    coverage against ONLY the evidence owned by the position(s) that back
    it, role-agnostic prose excluded — decides which negative verdict fits:
    if the claim's own wording clears ``_UNATTRIBUTABLE_CONTENT_FLOOR``
    against that position's OWN evidence (a plausible restatement whose
    ownership just can't be pinned down in the letter's prose), stay
    ``unverifiable`` — honest uncertainty, not an accusation. If it does not
    (the claim's action/content has nothing to do with what that position's
    evidence actually says — a number lifted from an unrelated fact and
    dressed in different wording), escalate to ``unbacked``: the evidence
    backs the FIGURE, not the CLAIM.
    """
    if source_id is not None or letter_named_ids is None or not units:
        return None
    if any(not u.owner_ids for u in units):
        return None
    owners = {oid for u in units for oid in u.owner_ids}
    if sentence_named_ids and sentence_named_ids & owners:
        return None
    if _all_same_employer(letter_named_ids, index) and letter_named_ids & owners:
        return None
    if _owner_scoped_coverage(claim_text, index, owners) < _UNATTRIBUTABLE_CONTENT_FLOOR:
        return ClaimVerdict(
            verdict="unbacked",
            checker="attribution",
            evidence=_evidence_refs(units),
            detail=(
                "The only vault evidence for this claim's figure belongs to "
                "a different position, and this claim's own wording does not "
                "correspond to that evidence's content — the figure appears "
                "borrowed from an unrelated fact."
            ),
        )
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
    index: VaultIndex | None = None,
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
        flag = _attribution_red_flag(source_id, evidence_units, index)
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

    # ── employer facts (#237 round-3): out of the vault's domain, full stop ─
    # A statement about the TARGET COMPANY, not the candidate — never even
    # attempt vault-grounding (there IS none to attempt; this is not the same
    # as "the vault has no evidence", which would misleadingly read as an
    # unbacked/unverifiable CANDIDATE claim). See extract.py's module
    # docstring point 6 and top-of-file section for the classification.
    if claim.is_employer_fact:
        return ClaimVerdict(
            verdict="not_applicable",
            checker="extraction",
            detail=(
                "Statement about the target employer, not the candidate — "
                "outside the vault's scope (checked against the job "
                "description elsewhere, not here)."
            ),
        )

    # ── honest gap disclaimers (#282, wave 7): no positive claim to ground ──
    # A pure denial or third-party delegation ("I have not configured X
    # myself"; "X was handled by our system engineer") asserts nothing about
    # the candidate that the vault could ever confirm — there is no
    # "evidence of absence" to trace to. Grading it ``unverifiable`` would
    # score the more honest letter WORSE. See extract.py's
    # ``_is_pure_denial_clause`` for the classification (conservative:
    # never fires when the clause smuggles a real positive claim alongside
    # the denial).
    if claim.is_denial:
        return ClaimVerdict(
            verdict="not_applicable",
            checker="extraction",
            detail=(
                "Honest denial or third-party delegation — no positive "
                "claim about the candidate to verify against the vault."
            ),
        )

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
            flag = _attribution_red_flag(source_id, fig_units, idx)
            if flag is not None:
                return flag
        # ── 2c. unattributable figure (unanchored letter claim, #243-adjacent)
        for fig, fig_units in fig_match.matched:
            if fig.kind == "year":
                continue
            flag = _unattributable_evidence_flag(
                claim.text, idx, source_id, letter_named_ids, claim.sentence_named_ids, fig_units
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
                claim.text, context_units, provider, budget, fallback, source_id, idx
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
        flag = _attribution_red_flag(source_id, grounding.qualifying_units, idx)
        if flag is not None:
            return flag
        # ── unattributable figure-free claim (unanchored letter claim, #248) ─
        # The non-figure counterpart of §2c: an unanchored letter claim whose
        # entire grounding backing is owned is the SAME genuine-unattributable
        # shape a figure carries, just without a figure to route through the
        # per-figure loop above (df78cac was scoped to figures only).
        flag = _unattributable_evidence_flag(
            claim.text,
            idx,
            source_id,
            letter_named_ids,
            claim.sentence_named_ids,
            grounding.qualifying_units,
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
        pre_union_flag = _attribution_red_flag(source_id, grounding.top_units, idx)
        if pre_union_flag is not None:
            return pre_union_flag
        # ── 3a2. role-union fallback (#237 run-4 residual) ───────────────────
        # An ANCHORED claim narrating several facts about ONE role — no single
        # bullet clears the floor, but the role's OWN evidence collectively
        # does. Scoped to source_id's own units only (see
        # grounding.ground_via_role_union's docstring) — the pre-union
        # attribution guard above already ruled out a foreign-owned claim, so
        # this can never rescue a misattributed blend.
        role_grounding = ground_via_role_union(
            claim.text, idx, source_id, idx.same_employer_ids.get(source_id, frozenset())
        )
        if role_grounding is not None:
            return ClaimVerdict(
                verdict="grounded",
                checker="grounding",
                evidence=_evidence_refs(role_grounding.qualifying_units),
                detail=(
                    "Grounded via the union of this role's own vault evidence "
                    "(multi-fact narrative clause)."
                ),
            )
    elif len(claim.sentence_named_ids) == 1:
        # ── 3a3. soft-anchor role-union (#237 run-4 residual) ────────────────
        # The STRICT anchor (``source_id``) intentionally stays ``None`` on a
        # legal-form-suffix mismatch or any other ambiguity it refuses to
        # guess through (#237/#248's own "fail open, never guess" rule,
        # unchanged). ``sentence_named_ids`` (#248) is deliberately more
        # permissive — legal-form-suffix tolerant, same-company duplicate-id
        # ambiguity tolerated — and already trusted elsewhere
        # (``_unattributable_evidence_flag``'s escape) as "this clause's own
        # sentence names its true owner". When it names EXACTLY one id, that
        # is enough to try — never to CLAIM — the SAME role-union grounding
        # an explicit anchor gets, still behind the identical pre-union
        # attribution guard (a claim whose best-effort evidence is
        # EXCLUSIVELY a different, foreign position must flag misattributed,
        # never be silently rescued here).
        (soft_id,) = claim.sentence_named_ids
        pre_union_flag = _attribution_red_flag(soft_id, grounding.top_units, idx)
        if pre_union_flag is not None:
            return pre_union_flag
        role_grounding = ground_via_role_union(
            claim.text, idx, soft_id, idx.same_employer_ids.get(soft_id, frozenset())
        )
        if role_grounding is not None:
            return ClaimVerdict(
                verdict="grounded",
                checker="grounding",
                evidence=_evidence_refs(role_grounding.qualifying_units),
                detail=(
                    "Grounded via the union of this role's own vault evidence "
                    "(multi-fact narrative clause, sentence-named owner)."
                ),
            )
    elif letter_named_ids and _all_same_employer(letter_named_ids, idx):
        # ── 3a4. whole-letter single-employer role-union (#237 round-3) ──────
        # A genuinely UNANCHORED claim with no employer context of its own —
        # not even a same-company-ambiguous mention (that's 3a3 above) — in a
        # letter that, taken as a WHOLE, only ever names roles at ONE
        # company: there is no OTHER employer this content could plausibly be
        # confused with, so it may draw on that company's full evidence via
        # the same role-union mechanism. Never used for misattribution
        # flagging (no specific rendered position is being claimed wrong,
        # so no pre-union guard here) — only widens what counts as "this
        # claim's own evidence" when nothing more precise names it. A letter
        # naming TWO OR MORE distinct employers never reaches this branch
        # (``_all_same_employer`` is false), so a genuine cross-employer
        # letter gets no such benefit of the doubt.
        whole_letter_id = next(iter(letter_named_ids))
        role_grounding = ground_via_role_union(
            claim.text,
            idx,
            whole_letter_id,
            idx.same_employer_ids.get(whole_letter_id, frozenset()),
        )
        if role_grounding is not None:
            return ClaimVerdict(
                verdict="grounded",
                checker="grounding",
                evidence=_evidence_refs(role_grounding.qualifying_units),
                detail=(
                    "Grounded via the union of the letter's single named "
                    "employer's vault evidence (multi-fact narrative "
                    "clause, no closer anchor available)."
                ),
            )
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
        claim.text, grounding.top_units, provider, budget, fallback, source_id, idx
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
