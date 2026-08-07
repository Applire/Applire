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
NordPharm-sentence-adjacent but exclusively Applire-owned — reaches
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

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from applire.constants import (
    ORACLE_ENTAILMENT_MAX_TOKENS,
    ORACLE_MAX_ENTAILMENT_CALLS,
    ORACLE_MAX_JUDGEMENT_CALLS,
)
from applire.prompts.oracle_judgement import (
    ORACLE_JUDGEMENT_BATCH_SIZE,
    ORACLE_JUDGEMENT_SYSTEM_PROMPT,
    build_judgement_user_prompt,
    judgement_call_max_tokens,
)
from applire.services.ats_audit import _norm, skill_tokens, surface_present
from applire.services.citation import citation_present
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
    extract_tenure_claims,
    find_foreign_owner,
    ground_skill_claim,
    ground_text_claim,
    ground_via_role_union,
    ground_via_skill_union,
    match_figures,
)
from applire.services.oracle.matchers.grounding import GROUNDED_MIN_COVERAGE
from applire.services.oracle.stance import classify_stance

logger = logging.getLogger(__name__)

_EXCERPT_CHARS = 160

# ADR-068 — human labels for the two DE/EN document languages the cross-
# language judgement seam quotes in its "grounded" detail text. Falls back to
# the bare language code for anything else (out of DACH-native scope today,
# but never a reason to raise).
_LANGUAGE_LABELS = {"de": "German", "en": "English"}


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


# ── ADR-068 — bounded equivalence judgement (cross-language + restatement) ──
#
# The deterministic layer above is a LITERAL matcher: it can prove a claim
# traces to the vault, but cannot tell a faithful restatement from a genuine
# miss. Two narrow seams defer that ONE question to a model, batched once per
# document (clause 6) rather than per claim — see
# ``applire.prompts.oracle_judgement`` for the full contract and rationale.
#
# ``verify_claim`` never calls the judgement provider itself. When a seam
# triggers it either (a) resolves IMMEDIATELY to the clause-3 fail-safe
# verdict, when no ``judgement_sink`` is wired (a bare ``verify_claim`` call,
# outside ``audit_document``'s batching), or (b) appends a ``_SeamCandidate``
# to the sink and returns it AS THE VERDICT SLOT — ``audit_document`` checks
# for this marker, holds the fail-safe verdict as a placeholder, and
# overwrites it once the batched judgement call resolves (or leaves it,
# fail-safe, if resolution never lands one).


@dataclass
class _SeamCandidate:
    """One claim deferred to the batched judgement pass."""

    seam: Literal["cross_language", "restatement"]
    claim_text: str
    units: list[EvidenceUnit]
    # cross_language only: corresponds=false/uncertain -> this verdict stands
    # (the pre-existing deterministic miss). None for restatement (its own
    # polarity is symmetric — see _resolve_seam_candidate).
    deny_verdict: ClaimVerdict | None
    # What the claim verdicts as if the judgement never resolves cleanly
    # (unavailable/malformed/citation-dropped) — always non-accusatory
    # (ADR-068 clause 3).
    unavailable_verdict: ClaimVerdict
    vault_language_label: str | None = None


def _find_citing_unit(quote: str | None, units: list[EvidenceUnit]) -> EvidenceUnit | None:
    for unit in units:
        if citation_present(quote, [unit.text]):
            return unit
    return None


def _cross_language_trigger(document_language: str | None, index: VaultIndex) -> bool:
    """ADR-068 clause 2a — a deterministic miss AND the document's language
    differs from the vault's own dominant language. ``document_language is
    None`` (the caller opted out, or a non-letter/CV audit) fails OPEN to
    today's behaviour — the seam never fires."""
    return document_language is not None and document_language != index.dominant_language


def _cross_language_candidate(
    claim_text: str,
    index: VaultIndex,
    document_language: str | None,
    units: list[EvidenceUnit],
    deny_verdict: ClaimVerdict,
    judgement_sink: "list[_SeamCandidate] | None",
) -> "ClaimVerdict | _SeamCandidate | None":
    """ADR-068 Seam A. ``None`` when the trigger doesn't hold or there is
    nothing to judge against — the caller keeps its pre-existing verdict.
    Otherwise a resolved fail-safe :class:`ClaimVerdict` (no sink wired) or a
    deferred :class:`_SeamCandidate` (sink wired — resolved later, batched)."""
    if not _cross_language_trigger(document_language, index) or not units:
        return None
    unavailable = ClaimVerdict(
        verdict="unverifiable",
        checker="cross_language_judgement",
        evidence=_evidence_refs(units),
        detail=(
            "This claim's language differs from the vault's own — an "
            "equivalence judgement could not confirm whether it restates the "
            "candidate's vault evidence, so this is left as an honest gap "
            "rather than an accusation (ADR-068)."
        ),
    )
    candidate = _SeamCandidate(
        seam="cross_language",
        claim_text=claim_text,
        units=units,
        deny_verdict=deny_verdict,
        unavailable_verdict=unavailable,
        vault_language_label=_LANGUAGE_LABELS.get(index.dominant_language, index.dominant_language),
    )
    if judgement_sink is None:
        logger.info(
            "ORACLE_JUDGEMENT_UNAVAILABLE reason=no_sink seam=cross_language"
        )
        return unavailable
    judgement_sink.append(candidate)
    return candidate


def _restatement_candidate(
    claim_text: str,
    units: list[EvidenceUnit],
    judgement_sink: "list[_SeamCandidate] | None",
) -> "ClaimVerdict | _SeamCandidate":
    """ADR-068 Seam B (the unanchored-figure escalation, formerly a
    deterministic ``unbacked``) — always a candidate once the below-floor
    branch is reached; unlike Seam A there is no "trigger" to fail open on,
    only whether a batching sink is available."""
    unavailable = ClaimVerdict(
        verdict="unverifiable",
        checker="restatement_judgement",
        evidence=_evidence_refs(units),
        detail=(
            "This claim's only vault evidence is owned by a position, but "
            "this claim names no employer and the letter's own context "
            "doesn't unambiguously point to that one position — an "
            "equivalence judgement could not confirm the claim restates that "
            "evidence, so attribution is left unverifiable rather than "
            "accused (ADR-068)."
        ),
    )
    candidate = _SeamCandidate(
        seam="restatement",
        claim_text=claim_text,
        units=units,
        deny_verdict=None,
        unavailable_verdict=unavailable,
    )
    if judgement_sink is None:
        logger.info(
            "ORACLE_JUDGEMENT_UNAVAILABLE reason=no_sink seam=restatement"
        )
        return unavailable
    judgement_sink.append(candidate)
    return candidate


def _resolve_seam_candidate(
    candidate: _SeamCandidate, response_item: dict[str, Any] | None
) -> tuple[ClaimVerdict, bool, str | None]:
    """(verdict, unavailable, reason) for one resolved batch item.

    ``reason`` is ``None`` on a clean resolution (grant, deny, or the soft
    restatement verdict) and one of ``"malformed_item"``/``"citation_drop"``
    otherwise — the caller logs the distinct ``ORACLE_JUDGEMENT_UNAVAILABLE``/
    ``ORACLE_JUDGEMENT_CITATION_DROP`` lines from it.
    """
    if not isinstance(response_item, dict):
        return candidate.unavailable_verdict, True, "malformed_item"
    corresponds = response_item.get("corresponds")
    quote = response_item.get("vault_quote")
    unit_texts = [u.text for u in candidate.units]
    citation_ok = citation_present(quote, unit_texts)

    if candidate.seam == "cross_language":
        if corresponds is True:
            if not citation_ok:
                return candidate.unavailable_verdict, True, "citation_drop"
            matched_unit = _find_citing_unit(quote, candidate.units)
            verdict = ClaimVerdict(
                verdict="grounded",
                checker="cross_language_judgement",
                evidence=_evidence_refs([matched_unit] if matched_unit else candidate.units),
                detail=(
                    "Equivalent restatement across languages — vault evidence "
                    f'({candidate.vault_language_label}): "{quote}".'
                ),
            )
            return verdict, False, None
        if corresponds is False or corresponds == "uncertain":
            assert candidate.deny_verdict is not None
            return candidate.deny_verdict, False, None
        return candidate.unavailable_verdict, True, "malformed_item"

    # seam == "restatement" — both true/false/uncertain need a verified
    # citation (the model must always show which evidence it compared the
    # claim against, whichever way it answers).
    if not citation_ok:
        return candidate.unavailable_verdict, True, "citation_drop"
    if corresponds is False:
        verdict = ClaimVerdict(
            verdict="unbacked",
            checker="restatement_judgement",
            evidence=_evidence_refs(candidate.units),
            detail=(
                "The only vault evidence for this claim's figure belongs to "
                "a different position, and an equivalence judgement confirms "
                "this claim's own wording does not restate that evidence — "
                "the figure appears borrowed from an unrelated fact."
            ),
        )
        return verdict, False, None
    if corresponds is True or corresponds == "uncertain":
        verdict = ClaimVerdict(
            verdict="unverifiable",
            checker="restatement_judgement",
            evidence=_evidence_refs(candidate.units),
            detail=(
                "This claim's only vault evidence is owned by a position, but "
                "this claim names no employer and the letter's own context "
                "doesn't unambiguously point to that one position — "
                "attribution cannot be verified."
            ),
        )
        return verdict, False, None
    return candidate.unavailable_verdict, True, "malformed_item"


async def _run_judgement_batches(
    candidates: list[_SeamCandidate], provider: Any
) -> list[tuple[ClaimVerdict, bool]]:
    """Resolve every candidate via batched ORACLE_JUDGEMENT calls (clause 6),
    ``ORACLE_JUDGEMENT_BATCH_SIZE`` items per call, ``ORACLE_MAX_JUDGEMENT_
    CALLS`` calls per document. Returns ``(verdict, unavailable)`` pairs in
    the SAME order as *candidates*.

    Fail-safe end-to-end (ADR-068): a batch's own exception, a malformed
    top-level response, or a missing provider degrades ONLY the candidates in
    that batch (or all of them, for a missing provider) to their own
    ``unavailable_verdict`` — this function never raises.
    """
    if not candidates:
        return []
    results: list[tuple[ClaimVerdict, bool]] = [
        (c.unavailable_verdict, True) for c in candidates
    ]
    if provider is None:
        for c in candidates:
            logger.info(
                "ORACLE_JUDGEMENT_UNAVAILABLE reason=no_provider seam=%s", c.seam
            )
        return results

    calls_made = 0
    for start in range(0, len(candidates), ORACLE_JUDGEMENT_BATCH_SIZE):
        batch = candidates[start : start + ORACLE_JUDGEMENT_BATCH_SIZE]
        if calls_made >= ORACLE_MAX_JUDGEMENT_CALLS:
            for c in batch:
                logger.info(
                    "ORACLE_JUDGEMENT_UNAVAILABLE reason=budget_exhausted seam=%s",
                    c.seam,
                )
            continue
        calls_made += 1
        items = [(c.claim_text, [u.text for u in c.units], c.seam) for c in batch]
        prompt = build_judgement_user_prompt(items)
        try:
            response = await provider.aparse_json(
                prompt,
                system=ORACLE_JUDGEMENT_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=judgement_call_max_tokens(len(batch)),
            )
        except Exception:
            logger.info(
                "ORACLE_JUDGEMENT_UNAVAILABLE reason=provider_error seam=batch "
                "size=%d",
                len(batch),
            )
            continue  # this sub-batch's placeholders (unavailable) stand

        raw_items = response.get("items") if isinstance(response, dict) else None
        if not isinstance(raw_items, list):
            logger.info(
                "ORACLE_JUDGEMENT_UNAVAILABLE reason=malformed_response seam=batch "
                "size=%d",
                len(batch),
            )
            continue

        by_index: dict[int, Any] = {}
        for entry in raw_items:
            if isinstance(entry, dict) and isinstance(entry.get("index"), int):
                by_index[entry["index"]] = entry

        for j, c in enumerate(batch):
            entry = by_index.get(j)
            if entry is None and j < len(raw_items):
                entry = raw_items[j]  # positional fallback (bad/missing index)
            verdict, unavailable, reason = _resolve_seam_candidate(c, entry)
            results[start + j] = (verdict, unavailable)
            if reason == "citation_drop":
                logger.info(
                    "ORACLE_JUDGEMENT_CITATION_DROP seam=%s span=%r",
                    c.seam,
                    (entry or {}).get("vault_quote") if isinstance(entry, dict) else None,
                )
            elif reason == "malformed_item":
                logger.info(
                    "ORACLE_JUDGEMENT_UNAVAILABLE reason=malformed_item seam=%s",
                    c.seam,
                )
    return results


# ── #469 — the tenure ceiling ────────────────────────────────────────────────
#
# The rounding slack a stated duration is allowed above the derived span.
# Two sources, both real and both one-sided toward NOT accusing:
#
#   1. Idiomatic rounding. Someone at 13.6 years who writes "14 Jahre" is
#      speaking normally, not inflating. #469 names this case explicitly.
#   2. Date granularity. Vault dates are routinely year-only ("2011") or
#      month-only ("2011-08"); ``_coerce_partial_date`` expands both to the
#      FIRST of the period, so a span's true END can sit up to twelve months
#      later than the stored value — the derived ceiling is systematically
#      too LOW by up to a year, and a tolerance below 1.0 would turn that
#      storage artefact into an accusation.
#
# One year is therefore the smallest value that covers (2); (1) fits inside
# it. Larger would start excusing real inflation: the class this check exists
# for ("25 Jahre" on a 11-year career) clears any plausible tolerance by a
# wide margin, so nothing is bought by widening further.
_TENURE_TOLERANCE_YEARS = 1.0


def _tenure_ceiling_flag(text: str, index: VaultIndex) -> ClaimVerdict | None:
    """A stated tenure above the vault's derivable span (#469, #403).

    The predicate: **claimed years ≤ derivable years + tolerance**, where
    "derivable" is the envelope of the vault's own dated spans
    (:func:`matchers.vault.derive_tenure_ceiling_years`). ``None`` when the
    claim states no duration, when the vault has no dated span at all, or
    when the claim sits at or below the ceiling.

    **Direction.** Only the OVERCLAIM direction is decidable. A duration
    BELOW the derivable span is not a false statement about the span, and a
    rule that fired on it would be inventing a limit on the candidate — the
    ADR-061 clause-5 error in its deflation direction. Understatements and
    document-vs-document mismatches ("14 Jahren" in the CV, "11-jährige" in
    the letter — charter run 17) are a CROSS-DOCUMENT INCONSISTENCY, not a
    vault contradiction, and belong to the critic's ``numeric_inconsistency``
    advisory (#403/#417). This function is deliberately silent on them.

    **Scope boundary.** A scope claim ("X Jahre Erfahrung in <domain>")
    bounds the domain as well as the total, but *which roles count toward
    domain X* is a JUDGEMENT under ADR-062 clause 1 — it cannot be settled
    without reading prose for meaning. The ceiling therefore uses the TOTAL
    derivable span, which is a valid upper bound for every domain subset: a
    domain claim ABOVE the total career length is decidably false, while
    domain-scoped inflation BELOW it is not deterministically decidable and
    is left to the critic and the other judgement seams. Widening this to
    per-domain spans would require exactly the semantic matching ADR-062
    clause 3 says to delete rather than tune.

    **Why this is not the #214 mechanism returning.** #214 was right: a
    duration is derived from date spans, never stored as a literal, so
    matching it against vault FIGURES attributed "14 Jahren Expertise" to a
    shift headcount that happened to share the digits. Durations stay out of
    ``extract_figures``/``figure_map``. This compares a duration against the
    only corpus that can actually contain one — the vault's dates.

    ADR-062 classification: **FACT** on both sides — a closed number-word
    table plus the unit token on the claim side (``extract_tenure_claims``),
    date arithmetic on the vault side. ADR-061 clause 5 (as amended
    2026-08-02): the derived ceiling is a classification input; it names
    itself in the Oracle's own audit note, which is report/UI-facing only,
    and is never handed to a writer or reviewer as the candidate's claim.
    """
    ceiling = index.derivable_tenure_years
    if ceiling is None:
        return None
    allowed = ceiling + _TENURE_TOLERANCE_YEARS
    for tenure in extract_tenure_claims(text):
        if tenure.years <= allowed:
            continue
        return ClaimVerdict(
            verdict="unbacked",
            checker="numbers",
            detail=(
                f'Claimed duration "{tenure.raw.strip()}" exceeds the '
                f"{ceiling:.1f} years derivable from the vault's own dated "
                "spans (earliest recorded start to the latest end; an "
                "open-ended span counts to today)."
            ),
        )
    return None


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
    judgement_sink: "list[_SeamCandidate] | None" = None,
) -> "ClaimVerdict | _SeamCandidate | None":
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
        NordPharm..." sentence must clear here even in a letter that also
        names Applire elsewhere — the letter-wide escape alone would wrongly
        flag it once NordPharm is correctly counted as "named elsewhere",
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
    (NordPharm named in one sentence, Applire in the next, then a bare
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
    it, role-agnostic prose excluded — decides how to proceed: coverage AT
    OR ABOVE ``_UNATTRIBUTABLE_CONTENT_FLOOR`` (a plausible restatement whose
    ownership just can't be pinned down in the letter's prose) stays
    ``unverifiable`` directly — a cheap, deterministic short-circuit, no
    model call, honest uncertainty rather than an accusation.

    BELOW the floor (ADR-068 clause 4/Seam B, amended 2026-08-01 — this
    branch previously escalated straight to ``unbacked`` on the coverage
    number alone): the claim becomes a RESTATEMENT-JUDGEMENT CANDIDATE
    (:func:`_restatement_candidate`) instead. A bounded equivalence
    judgement, not a bare token-overlap number, decides whether the claim's
    own wording genuinely restates the owning position's evidence
    (``corresponds=false``, citation-verified -> ``unbacked``,
    checker ``restatement_judgement``) or not (``true``/``uncertain``, or the
    judgement is unavailable -> the same soft ``unverifiable`` this function
    already returns for the at-or-above-floor case). The coverage floor may
    only ever soften toward ``unverifiable`` on its own now — it can no
    longer, by itself, accuse.
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
        return _restatement_candidate(claim_text, units, judgement_sink)
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


# #404 retrofit (2026-08-01): this call had NO ``system=`` argument at all —
# the entire prompt, including its own opening identity line, went through
# as the user prompt. That is invisible to ``MockLLMProvider.aparse_json``'s
# fingerprint strategy (it inspects ``system``, never ``prompt`` — see
# ``providers/llm/mock.py``'s own module docstring), so under the mock
# provider this call ALWAYS fell to the generic ``{"mock": ...}`` fallback,
# which fails ``_VALID_ENTAILMENT_VERDICTS`` and degrades to ``fallback`` —
# safe, but silently means the mock stack has never once exercised a real
# entailment verdict. Split into a system identity line (fingerprinted below,
# and pinned by ``test_mock_reviewer_chain_recognition.py``) + a user prompt
# carrying the actual comparison.
_ENTAILMENT_SYSTEM_PROMPT = (
    "You are a strict verification function for job-application claims.\n"
    "Compare the DOCUMENT CLAIM against the PROFILE EVIDENCE and return "
    'STRICT JSON: {"verdict": "grounded" | "inflated" | "unbacked" | "unverifiable"}.\n'
    "- grounded: the evidence supports the claim as stated\n"
    "- inflated: the evidence is aspirational or weaker than the claim's rendering "
    "(e.g. a target presented as an achieved result)\n"
    "- unbacked: the evidence does not contain or contradicts the claim\n"
    "- unverifiable: subjective, or the evidence cannot decide it"
)

_ENTAILMENT_USER_PROMPT = "PROFILE EVIDENCE:\n{evidence}\n\nDOCUMENT CLAIM:\n{claim}"

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
            _ENTAILMENT_USER_PROMPT.format(evidence=evidence, claim=claim_text),
            system=_ENTAILMENT_SYSTEM_PROMPT,
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


# ── #422 — guarded figure absorption from the denial rail ────────────────────
# The claim must substantially RESTATE the specific denial statement it
# borrows a figure from. Content tokens only (digits + tokens of ≥ 4 chars
# over the shared ``_norm`` form): German function words inflate raw overlap
# in both directions. Threshold validated against the run-14/15 ground truth:
# the two real honest restatements score 0.78–0.87, while a fabricated
# achieved claim reusing the denied figure ("Ich führe derzeit 120
# Mitarbeitende in drei Schichten") scores 0.5 against the very statement
# that denies it.
#
# Overlap ALONE is not sufficient, and the adversarial refutation pass
# (2026-08-02) proved it: word reuse cannot see DIRECTION, so a claim that
# inverts the denial's own framing scores HIGHEST of all. "Die dauerhafte
# Spanne von 120 habe ich bei Weberit geführt" — the exact negation of the
# statement it borrows from — scored 0.83 and absorbed. Three vectors
# confirmed (plain number, percent range via #412, and a mixed true-win +
# laundered-scope sentence). Hence the stance-consistency rule below.
#
# It is deliberately NOT ``stance.classify_stance``: that classifier is
# negation-blind (a statement containing "das habe ich so nie erreicht"
# classifies as "achieved"), so routing this through it would have closed
# one vector and left the other open. This is a local, literal
# hedge/negation test over the SUB-CLAUSE that actually carries the figure —
# a fact-level check in ADR-062's sense, never a judgement about meaning.
_DENIAL_OVERLAP_THRESHOLD = 0.6
_DENIAL_MIN_CONTENT_TOKENS = 3

# Sub-clause boundaries: sentence enders, plus the separators a German
# denial statement actually uses to attach its hedge ("…, jeweils 2 bis 4
# Wochen am Stück. Ehrlich gesagt: die dauerhafte Spanne von 120 wäre …").
# The dash alternatives require surrounding whitespace so a compound noun
# ("Hygiene- und Dokumentationsdisziplin", "ISO-9001-Audit-Praxis") is never
# split — the figure would lose the very context this check reads.
#
# The two sides split DIFFERENTLY, and deliberately so — the asymmetry is
# the control (SF-ORACLE.1's anchors: false accusation S=2, false assurance
# S=3, so every uncertain case must resolve toward refusing absorption):
#
#   statement side — the WIDER window (no comma). A narrower one could carve
#       a hedged statement into a factual-LOOKING fragment ("Die Spanne von
#       120, die wäre neu für mich") and grant blanket absorption.
#   claim side — the NARROWER window (comma included). Without it, an
#       assertion buys absorption by appending an unrelated hedged tail
#       ("…habe ich geführt, mehr wäre der nächste Schritt" — found by
#       probing the first hardening, 2026-08-02). The cost is a resumptive
#       honest phrasing ("Eine Spanne von 120, das wäre der nächste
#       Schritt") staying accused: the cheaper error, by the anchors above.
_DENIAL_CLAUSE_SPLIT_RE = re.compile(r"[.!?;:]|\s[-‒–—―−]\s")
_DENIAL_CLAIM_SPLIT_RE = re.compile(r"[.!?;:,]|\s[-‒–—―−]\s")

# Literal hedge/negation markers (DE + EN). Matched as whole tokens over the
# shared ``_norm`` form, so "kein" never fires inside "keineswegs"-style
# neighbours and "no" never fires inside a word.
_DENIAL_HEDGE_TOKENS = frozenset({
    "wäre", "wären", "würde", "würden", "hätte", "hätten", "sollte",
    "nicht", "nie", "niemals", "kein", "keine", "keinen", "keinem",
    "keiner", "keines", "nein", "ziel", "künftig", "zukünftig",
    "would", "could", "not", "never", "no", "aspire", "goal", "future",
})
# Multi-word hedges checked as substrings of the normalized sub-clause.
_DENIAL_HEDGE_PHRASES = (
    "nächste schritt", "nächster schritt", "nächsten schritt",
    "noch nicht", "bisher nicht", "next step", "have not", "has not",
    "not yet",
)


def _denial_content_tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if len(t) >= 4 or t.isdigit()}


def _denial_sub_clauses(text: str, *, claim_side: bool = False) -> list[str]:
    pattern = _DENIAL_CLAIM_SPLIT_RE if claim_side else _DENIAL_CLAUSE_SPLIT_RE
    return [part for part in pattern.split(text) if part.strip()]


def _denial_is_hedged(text: str) -> bool:
    normalized = _norm(text)
    if any(phrase in normalized for phrase in _DENIAL_HEDGE_PHRASES):
        return True
    return bool(set(normalized.split()) & _DENIAL_HEDGE_TOKENS)


def _denial_carrying_clauses(
    text: str, fig: Any, *, claim_side: bool = False
) -> list[str]:
    """Sub-clauses of ``text`` that actually state ``fig``.

    Uses the SAME extractor as everything else, so #412's percent-range
    distribution composes here instead of being re-derived.
    """
    return [
        clause
        for clause in _denial_sub_clauses(text, claim_side=claim_side)
        if any(
            f.kind == fig.kind and f.value == fig.value
            for f in extract_figures(clause)
        )
    ]


def _denial_stance_consistent(claim_text: str, statement: str, fig: Any) -> bool:
    """False when the claim ASSERTS a figure its statement only hedges.

    If every sub-clause of the statement that carries the figure is hedged or
    negated, the figure is stated as a limit — and only a claim that carries
    a hedge in its OWN figure-bearing sub-clause may restate it. A figure
    also stated factually somewhere in the statement (e.g. "seit 2021" inside
    a transfer clause) is ordinary testimony and needs no hedge at all.

    The claim side is checked per sub-clause, not whole-claim: otherwise an
    achieved overclaim could buy absorption by appending an unrelated "…das
    wäre der nächste Schritt" tail.
    """
    statement_clauses = _denial_carrying_clauses(statement, fig)
    if not statement_clauses:
        return False  # fail-safe: cannot locate the figure's own context
    if not all(_denial_is_hedged(c) for c in statement_clauses):
        return True  # stated factually at least once — genuine testimony
    claim_clauses = _denial_carrying_clauses(claim_text, fig, claim_side=True)
    if not claim_clauses:
        return False  # fail-safe: cannot locate the claim's own context
    return all(_denial_is_hedged(c) for c in claim_clauses)


def _denial_statement_backing(
    claim_text: str,
    unmatched: list[Any],
    idx: VaultIndex,
) -> list[EvidenceUnit] | None:
    """Denial-statement units absorbing EVERY unmatched figure, else None.

    Each unmatched figure must appear (same kind, same canonical value) in a
    denial statement the claim substantially restates AND restates with a
    consistent stance; one unresolved figure means no absorption at all and
    the caller's accusation stands unchanged. Fail-safe by construction — a
    miss keeps today's verdict (a false ACCUSATION at worst), never creates
    a false pass.
    """
    if not idx.denial_units:
        return None
    claim_tokens = _denial_content_tokens(claim_text)
    if len(claim_tokens) < _DENIAL_MIN_CONTENT_TOKENS:
        return None
    backing: list[EvidenceUnit] = []
    for fig in unmatched:
        found: EvidenceUnit | None = None
        for unit in idx.denial_units:
            if not any(
                f.kind == fig.kind and f.value == fig.value for f in unit.figures
            ):
                continue
            statement_tokens = _denial_content_tokens(unit.text)
            overlap = len(claim_tokens & statement_tokens) / len(claim_tokens)
            if overlap < _DENIAL_OVERLAP_THRESHOLD:
                continue
            if not _denial_stance_consistent(claim_text, unit.text, fig):
                logger.info(
                    "oracle #422: denial absorption REFUSED for figure %r — "
                    "the statement hedges it, the claim asserts it (%r)",
                    fig.raw, unit.path,
                )
                continue
            found = unit
            break
        if found is None:
            return None
        if found not in backing:
            backing.append(found)
    return backing


async def verify_claim(
    claim: Claim | str,
    profile: Any,
    provider: Any | None = None,
    *,
    index: VaultIndex | None = None,
    budget: _EntailmentBudget | None = None,
    letter_named_ids: frozenset[str] | None = None,
    document_language: str | None = None,
    judgement_sink: "list[_SeamCandidate] | None" = None,
) -> "ClaimVerdict | _SeamCandidate":
    """Verdict for a single claim against the vault (ADR-052 §1).

    ``profile`` accepts a ``MasterProfileData`` or its JSONB dict; pass a
    prebuilt ``index`` when auditing many claims. Deterministic red flags
    return before ``provider`` is ever consulted. ``letter_named_ids``
    (#243-adjacent) is the set of experience ids named anywhere in a letter
    being audited — ``None`` for CV/text callers, who never need it (see
    :func:`_unattributable_evidence_flag`).

    ``document_language`` (ADR-068 clause 2a) is the language THIS document
    is written in; ``None`` (the default) keeps the cross-language judgement
    seam off, fail-open to pre-ADR-068 behaviour. ``judgement_sink``, when
    supplied, is where a triggered seam DEFERS its candidate instead of
    resolving it immediately — the return value is then a
    :class:`_SeamCandidate`, not a :class:`ClaimVerdict`; a bare call (the
    common case for direct callers outside :func:`audit_document`) always
    gets back a real, immediately fail-safe :class:`ClaimVerdict`.
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
        deny = ClaimVerdict(
            verdict="unbacked",
            checker="grounding",
            detail=f'Skill "{claim.text}" has no vault evidence.',
        )
        # ADR-068 Seam A (#394 site): a literal miss on a DIFFERENT-language
        # document gets one bounded equivalence judgement before the
        # accusation stands — the deterministic matcher only ever compares
        # surface forms, so a skill genuinely held but named in the vault's
        # OTHER supported language always misses here. Same-language misses
        # (the trigger fails open, ``document_language is None`` included)
        # are completely unaffected — ``deny`` is returned exactly as before.
        # The vault's OWN skill units are ALWAYS in the candidate pool for a
        # skill label — a zero-token-overlap translation ("Capital
        # consolidation" ↔ "Kapitalkonsolidierung", the German compound-noun
        # class) leaves lexical top_units either empty or, worse,
        # coincidentally non-empty with unrelated narrative units, starving
        # the judgement of the one unit that answers it (real-provider probe,
        # 2026-08-01: 3/4 #394 pairs grounded, the fourth failed exactly
        # here). Skill units lead; lexical top_units append as narrative
        # context.
        skill_units = [u for u in idx.units if u.path.startswith("skills[")]
        top_units = ground_text_claim(claim.text, idx).top_units
        skill_candidates = skill_units + [u for u in top_units if u not in skill_units]
        seam = _cross_language_candidate(
            claim.text, idx, document_language, skill_candidates, deny, judgement_sink
        )
        return seam if seam is not None else deny

    # ── 1a. tenure ceiling (deterministic red flag, #469) ───────────────────
    # Runs FIRST of the numeric passes, deliberately: every branch below can
    # return early (an unmatched figure, a denial-absorbed figure, a grounded
    # figure set), and #469 is precisely the report of a red that became
    # unreachable because the sentence's OTHER figures grounded. See
    # :func:`_tenure_ceiling_flag` for the predicate, its direction, and the
    # judgement boundary it deliberately does not cross.
    tenure_flag = _tenure_ceiling_flag(claim.text, idx)
    if tenure_flag is not None:
        return tenure_flag

    # ── 1. number/date provenance (deterministic red flag) ──────────────────
    figures = extract_figures(claim.text)
    fig_match = match_figures(figures, idx)
    denial_backing: list[EvidenceUnit] = []
    if fig_match.unmatched:
        # #422: before accusing, check the denial rail — the letter carries
        # ADR-064 STATED LIMITS statements (near-)verbatim, so their figures
        # legitimately re-appear in honest restatements. Guarded absorption
        # only (see ``_denial_statement_backing``); a miss falls through to
        # the accusation exactly as before.
        absorbed = _denial_statement_backing(claim.text, fig_match.unmatched, idx)
        if absorbed is None:
            missing = ", ".join(f.raw for f in fig_match.unmatched)
            return ClaimVerdict(
                verdict="unbacked",
                checker="numbers",
                detail=f"No vault evidence for figure(s): {missing}.",
            )
        denial_backing = absorbed
        if not fig_match.matched:
            return ClaimVerdict(
                verdict="grounded",
                checker="numbers",
                evidence=_evidence_refs(denial_backing),
                detail=(
                    "Figure(s) trace to the candidate's recorded denial "
                    "statement (ADR-064 stated limit) — an honest "
                    "restatement, not a new claim."
                ),
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
                claim.text,
                idx,
                source_id,
                letter_named_ids,
                claim.sentence_named_ids,
                fig_units,
                judgement_sink,
            )
            if flag is not None:
                return flag
        # US245: entailment ONLY when both sides lack stance markers.
        # #422 (mixed case): denial-absorbed figures join the EVIDENCE and the
        # entailment context, never the stance analysis above — a denial
        # statement's own stance must not be able to suppress an inflation
        # flag raised by real vault evidence.
        if denial_backing:
            evidence_units = evidence_units + [
                u for u in denial_backing if u not in evidence_units
            ]
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
            judgement_sink,
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
    document_language: str | None = None,
    entailment: bool = True,
) -> TruthfulnessReport:
    """Full-document audit → :class:`TruthfulnessReport` (ADR-052 §1).

    Exactly one of ``tailored_data`` / ``letter_data`` / ``text`` must be
    provided. The report NEVER blocks delivery in v1 (ADR-040 attestation
    remains the gate) and always carries the ADR-052 §5 stated limit.

    ``document_language`` (ADR-068 clause 2a) is this document's OWN
    language (e.g. the generation output language) — ``None`` keeps the
    cross-language judgement seam off, fail-open to pre-ADR-068 behaviour.
    Both bounded-equivalence seams (cross-language + restatement) are
    BATCHED once per document (clause 6): every claim's per-claim
    verification either finalizes immediately (the deterministic layer,
    unchanged) or is deferred as a judgement candidate; after the full pass
    over every claim, one (or a few, ``ORACLE_MAX_JUDGEMENT_CALLS``-bounded)
    batched judgement call resolves them all, and citation-verified answers
    are patched into the report. A judgement failure of any kind — no
    provider, budget exhaustion, a provider exception, a malformed response,
    or a citation that doesn't verify — degrades ONLY the affected claim(s)
    to the clause-3 fail-safe verdict and never fails the audit.

    ``entailment`` (ADR-068 clause 7 scoping, added during implementation —
    NOT part of the original ADR-068 clause text, flagged as a deviation in
    the implementing commit): the pre-existing narrow ``_entailment`` call
    (ADR-052, undecided figure-free claims) shares this same ``provider``
    parameter. ``build_self_audit_report`` (the generation-time self-audit)
    passes ``entailment=False`` so threading a real provider for the TWO NEW
    bounded judgement seams does not also silently reactivate the older,
    broader, previously-dormant entailment mechanism during every CV/letter
    generation — that would reopen the "free of added latency/cost" half of
    the ADR-052 §4 guarantee ADR-068 never asked to amend, and did break
    several pre-existing tests whose mocked providers assert an exact call
    count/sequence. The agent-door ``audit_document`` tool (the OTHER
    caller) keeps ``entailment=True`` (the default) — entailment there was
    always live and intentional.
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
    budget = _EntailmentBudget(limit=ORACLE_MAX_ENTAILMENT_CALLS if entailment else 0)
    # ADR-068 clause 6 — every claim that defers to the judgement layer lands
    # here; ``pending`` remembers, in the SAME order, which ``results`` slot
    # each one fills once the batch resolves.
    judgement_sink: list[_SeamCandidate] = []
    pending_result_indices: list[int] = []
    results: list[ClaimResult] = []
    for claim in claims:
        verdict = await verify_claim(
            claim,
            profile,
            provider,
            index=index,
            budget=budget,
            letter_named_ids=letter_named_ids,
            document_language=document_language,
            judgement_sink=judgement_sink,
        )
        if isinstance(verdict, _SeamCandidate):
            # The candidate's own fail-safe verdict is the placeholder — if
            # the batch below never resolves it, this is exactly what stays.
            results.append(ClaimResult(claim=claim, verdict=verdict.unavailable_verdict))
            pending_result_indices.append(len(results) - 1)
        else:
            results.append(ClaimResult(claim=claim, verdict=verdict))

    judgement_unavailable = 0
    try:
        resolved = await _run_judgement_batches(judgement_sink, provider)
    except Exception:
        # Fail-safe end-to-end (ADR-068): an unexpected error anywhere in the
        # batching layer must never fail the audit — every pending claim
        # simply keeps its already-placed fail-safe placeholder.
        logger.exception("Oracle judgement batching failed — every pending claim stays fail-safe")
        resolved = [(c.unavailable_verdict, True) for c in judgement_sink]
    for sink_idx, (verdict, unavailable) in enumerate(resolved):
        result_idx = pending_result_indices[sink_idx]
        claim = results[result_idx].claim
        results[result_idx] = ClaimResult(claim=claim, verdict=verdict)
        if unavailable:
            judgement_unavailable += 1

    return TruthfulnessReport.from_results(
        document_kind, results, judgement_unavailable=judgement_unavailable
    )
