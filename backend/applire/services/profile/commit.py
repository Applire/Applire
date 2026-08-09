# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""ADR-063 — `commit_ops`, the single assigner of `master_profiles.profile_json`.

Every intake that wants to change the vault expresses the change as a list of
typed `CommitOp`s and hands them here. The committer owns the invariant set, so
each invariant is declared ONCE instead of being re-implemented (or forgotten)
by each of the eleven writers the 2026-07 inventory found.

**The invariants this module owns** (design §2 on #480, as amended
2026-08-09):

1. the ops are applied through `apply_ops` — the only path from intent to state;
2. the **persisted-denial re-floor** runs after the ops land: a write that
   re-introduces a skill the candidate retracted is taken back at the seam,
   whatever door it came through (`_refloor_persisted_denials`);
3. the enrichment trail is **unconditional** (this is what closes the
   `if receipt_changes:` holes the testimony and agent bridges shipped: a turn
   that changed nothing left no trace that it happened);
4. the completeness recompute is **universal** (it was import-only, so the
   stored score drifted after every testimony/interview/agent write);
5. `metadata.last_updated` and `record.updated_at` both move;
6. deterministic skill enrichment runs unconditionally — a skill's duration and
   provenance must never depend on whether the caller happened to pass an LLM
   provider (ADR-058 clause 2: the same edit may not behave differently by door);
7. **receipt separation** — demotions, denials and re-floorings are
   receipted into the `EnrichmentRecord` but NEVER enter `bool(changes)`. A
   retraction must not read as "gap addressed" or request a ledger upgrade
   (#231/#352);
8. the `_ensure_loadable` round-trip is the last gate before assignment.

**Flush, not commit** (ADR-063 amended 2026-08-09 clause 6). `commit_ops` ends
with `await db.flush()` and never calls `commit()`/`rollback()`/`refresh()`:
`services/session.py` writes `session.state` in the same transaction as the
profile write, and a committer that split that could desync "gap addressed"
from the vault. Every caller keeps its own `db.commit()` — a forgotten one is a
silent no-write, which is why each migrated writer lands with a door-level
integration test asserting the write survives the request.

**What this module deliberately does NOT do yet:**

* RELEASE a denial — the ADR-059 un-denial correction act is unbuilt and the
  seam is reserved with a raise (:data:`UN_DENIAL_INTAKE`, #506); until it
  lands, a denial floored here is permanent;
* snapshot coverage beyond the two import writers — `snapshot=MERGE` is real
  since PR 2, but widening it to any other intake stays BLOCKED (ADR-063
  amendment (5) / #339);
* first-profile CREATION — the three keyword-argument constructor sites are
  routed in PR 8; until then `commit_ops` requires an existing record;
* persisting `ops` on the `EnrichmentRecord` — §7.8 ruling: the replayable op
  log is deferred to the Finetuner release (#508), explicit as a scheduled goal
  rather than an implied property of the system;
* owning the embedding — §7.2 ruling: a parameter now, an ownership decision
  once there is run data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.profile import MasterProfile, authorized_profile_write
from applire.schemas.profile import (
    Conflict,
    EnrichmentRecord,
    FieldChange,
    MasterProfileData,
    PendingConfirmation,
    ProfileMetadata,
)
from applire.services.profile.reconcile.apply import _ensure_loadable, apply_ops
from applire.services.profile.reconcile.import_bridge import _to_pending_confirmation
from applire.services.profile.reconcile.ops import CommitOp, RequestConfirmation
from applire.services.profile.reconcile.stance import record_denials
from applire.services.skill_enrichment import enrich_skills, enrich_skills_deterministic

if TYPE_CHECKING:  # pragma: no cover — import cost only
    from applire.providers.embedding.base import EmbeddingProvider
    from applire.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)


class SnapshotClass(str, Enum):
    """ADR-042 snapshot classes.

    Only `MERGE` exists today — the class the import writers capture, and since
    #480 PR 2 the one the committer captures on their behalf. Universal
    snapshot coverage is BLOCKED by decision (ADR-063 amendment (5)): with
    `SNAPSHOT_MAX_PER_PROFILE = 10` pruned by recency, snapshotting every write
    lets one 10-turn interview evict the import snapshot, degrading the
    guarantee for the case it exists to protect. It is additionally blocked
    behind #339 (nothing calls `undo-last-merge` on either channel yet).

    So `snapshot` is a per-intake PARAMETER and not an invariant: the two import
    writers pass `MERGE`, every other intake passes `None`, and `None` is an
    honest no-op rather than a silent omission.
    """

    MERGE = "merge"


class EnrichPolicy(str, Enum):
    """Which half of the skill enrichment the commit runs (ADR-058 clause 2)."""

    #: Phase 1 always; phase 2 (the LLM duration estimate) only when the caller
    #: also supplies `llm_provider`. The default, and the deterministic half is
    #: unconditional: provenance and computed durations are not optional, and
    #: must never depend on how the caller happened to be wired (#337).
    DETERMINISTIC = "deterministic"
    #: For callers that have already enriched the profile they hand over (the
    #: import path runs `enrich_skills` with a provider before reaching here).
    SKIP = "skip"


@dataclass(frozen=True)
class TurnGrounding:
    """The turn text a grounded intake reconciled — §7.4's clause-5 parameter.

    The PO's framing, recorded as the rationale: there are two control families.
    **Type-1** stops LLM output becoming an unguarded write — stance and
    attribution over turn text, which run inside `reconcile()` before the ops
    land. **Type-2** checks final document status. Grounding is type-1's input,
    so it applies to turn-grounded intakes only; the committer never
    re-adjudicates direct user input.

    `grounding=None` therefore means *a direct act* → `confirmed` (ADR-061
    clause 2's pathway map as amended 2026-08-08), matching
    `stance._resolve_token`'s existing `corpus is None → "confirmed"` shape. A
    `FieldEdit` has no turn text, and requiring one would put an LLM
    adjudication in front of every manual edit.

    `denials` travels with the text because it IS a verdict about that text —
    the reconciler's own atomic denial declarations for this turn. Recording
    them is the committer's job (invariant 7's receipt separation applies to
    them exactly as it does to demotions).
    """

    text: str
    question: str | None = None
    gap: str | None = None
    denials: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommitProvenance:
    """Who is writing, through which door, on whose behalf.

    `source` is the durable `EnrichmentRecord.source` literal (it reaches the
    candidate's "what changed & why" surface); `intake` names the adapter for
    logs and future receipts.
    """

    source: str
    intake: str
    session_id: str | None = None
    actor: str | None = None


@dataclass
class CommitResult:
    """Everything the caller needs to answer its door honestly.

    The three receipt-only lists (`denials`, `demotions`, `refloored`) are kept
    OFF `changes` on purpose — see invariant 7. `enrichment_record.changes` is
    their union, which is what the candidate sees.
    """

    record: MasterProfile
    profile: MasterProfileData
    #: Positive, gap-addressing content. The ONLY list an `addressed`/upgrade
    #: gate may read.
    changes: list[FieldChange]
    #: #231 — receipts for denials this turn recorded.
    denials: list[FieldChange]
    #: #485 — receipts for `demote_skill` ops.
    demotions: list[FieldChange]
    #: Receipts for the persisted-denial re-floor (invariant 2).
    refloored: list[FieldChange]
    conflicts: list[Conflict]
    pending_confirmations: list[PendingConfirmation]
    enrichment_record: EnrichmentRecord
    completeness: float


_embedding_staleness_logged = False


def reset_embedding_staleness_log() -> None:
    """Test hook — the "logged once" flag is process-global by design."""
    global _embedding_staleness_logged
    _embedding_staleness_logged = False


def _log_embedding_staleness_once() -> None:
    global _embedding_staleness_logged
    if _embedding_staleness_logged:
        return
    _embedding_staleness_logged = True
    logger.info(
        "commit_ops: no embedding_provider supplied — master_profiles.embedding "
        "is left STALE for this and every subsequent untethered write, so "
        "similarity scoring drifts from the vault (ADR-063 amended 2026-08-09 "
        "clause 3 / #480 §7.2). Logged once per process, not per write."
    )


#: The intake name reserved for the ADR-059 explicit un-denial correction act
#: (2026-07-26 amendment, scheduled for the Finetuner release as #506). Naming
#: it here is the whole point: the seam a future act will need is visible and
#: RESERVED, so nobody quietly routes a release through an ordinary commit.
UN_DENIAL_INTAKE = "un_denial"


def _refloor_persisted_denials(profile: MasterProfileData) -> list[FieldChange]:
    """Invariant 2 — take back any vault skill a PERSISTED denial retracts.

    ADR-059 amended 2026-08-08 step 2; home ADR-063 clause 8(d). Until this, the
    floor only ever saw the denials the CURRENT turn declared
    (`enforce_stance`), so any later write through any door could re-introduce a
    skill the candidate had retracted, and nothing caught it before the next
    ledger rebuild — if one ever ran.

    **One instrument, not a second one** (design §3.2). `demote_ops_for_denials`
    is parameter-shaped and stays the single emission rule; the only change is
    that this caller feeds it the PERSISTED list off `metadata.denied_concepts`
    instead of the same-turn declarations its other caller passes. That input
    swap IS step 2, and it is what makes the ADR-059 #486 amendment clause (b)
    lockstep constraint hold by construction rather than by review.

    Its scope is INHERITED, never re-decided here: `confirmed` **skills** only
    (certifications and languages have no demote emission path — the
    reconciler's `denials` array carries no entity kind; recorded on #504).

    **The never-upgrade half stays read-side** (§3.3). The emitter matches
    declared-exact, longest-first — never the compound-containment branch — so a
    denial of "Tailwind CSS" never writes `denied` on the vault's bare "CSS".
    Containment still refuses the CLAIM at the ledger; it may not fabricate
    testimony in the vault. A fourth "floored-but-not-asserted" vault status was
    considered and rejected: a state with no testimony behind it is #486's own
    error one level down.

    **Release: none** (§3.4). Nothing here deletes a `DeniedConcept`, un-demotes
    or consults an affirmation predicate — see :data:`UN_DENIAL_INTAKE`.

    Mutates `profile` in place (through the shared applier) and returns the
    receipts, which the caller keeps OFF `changes` per invariant 7.
    """
    from applire.services.profile.reconcile.apply import _apply_demote_skill
    from applire.services.profile.reconcile.stance import demote_ops_for_denials

    metadata = profile.metadata
    persisted = [d.concept for d in (metadata.denied_concepts if metadata else []) if d.concept]
    if not persisted:
        return []

    refloored: list[FieldChange] = []
    for op in demote_ops_for_denials(profile, persisted):
        # THE shared applier, so a re-flooring and a `demote_skill` op write
        # byte-identically — including the idempotent skip on an entry that is
        # already `denied`, which is what keeps a repeated save from littering
        # the enrichment history.
        _apply_demote_skill(op, profile, refloored)
    if refloored:
        logger.info(
            "commit_ops: re-floored %d vault skill(s) against %d persisted "
            "denial(s) (ADR-059 step 2 / #480 invariant 2)",
            len(refloored),
            len(persisted),
        )
    return refloored


async def commit_ops(
    db: AsyncSession,
    ops: Sequence[CommitOp],
    provenance: CommitProvenance,
    *,
    record: MasterProfile | None = None,
    grounding: TurnGrounding | None = None,
    snapshot: SnapshotClass | None = None,
    ambiguities: Sequence[RequestConfirmation] = (),
    enrichment: EnrichPolicy = EnrichPolicy.DETERMINISTIC,
    llm_provider: "LLMProvider | None" = None,
    embedding_provider: "EmbeddingProvider | None" = None,
) -> CommitResult:
    """Apply `ops` to the Master Profile and persist the result. One write path.

    Args:
        db: the session. **Flushed, never committed** — the caller owns the
            transaction.
        ops: the typed intent. Model-emittable ops (`ReconcileOp`) and
            adapter-only ops (`DecisionOp`) both belong here; the union split
            lives in `reconcile/ops.py`.
        provenance: source / intake / session / actor for the receipt.
        record: the profile row to write. `None` resolves the latest.
            Creation is NOT routed yet (PR 8) — a missing profile raises
            `LookupError`, exactly as the bridges do today.
        grounding: the turn text and its denial verdict, for turn-based
            intakes. `None` = a direct act (§7.4).
        snapshot: ADR-042 snapshot class. `MERGE` captures the pre-op
            `profile_json` keyed to this write's enrichment record — import
            intakes only. `None` (every other intake) is a no-op; widening is
            blocked (ADR-063 amendment (5) / #339).
        ambiguities: engine-level `RequestConfirmation`s parked alongside the
            applier's own. Parking is UNCONDITIONAL since #480 PR 5 — see the
            note at the park site.
        enrichment: which half of the skill enrichment to run.
        llm_provider: when supplied (and `enrichment` is `DETERMINISTIC`), the
            phase-2 LLM duration estimate is layered ON TOP of the deterministic
            pass for skills no dated role could date. `None` runs the
            deterministic half alone — never nothing. This is the #337 split
            expressed as one parameter: the deterministic half is an invariant,
            the LLM half is an enhancement, and no door may lose the former by
            omitting a provider.
        embedding_provider: `None` leaves `master_profiles.embedding` stale and
            says so once (§7.2).

    Raises:
        LookupError: no profile exists yet.
        NotImplementedError: an unhandled `SnapshotClass`, or an
            `UN_DENIAL_INTAKE` intake — the reserved release seam (§3.4).
    """
    # Lazy: applire.services.profile imports this package's siblings.
    from applire.services.profile import _compute_embedding, _get_latest
    from applire.services.profile.snapshots import capture_pre_merge_snapshot

    if provenance.intake == UN_DENIAL_INTAKE:
        # ADR-059 §3.4, the RESERVED seam — refused before anything is read or
        # written, so no half of an un-denial can land. `commit_ops` never
        # deletes a `DeniedConcept`, never un-demotes, and never consults an
        # affirmation predicate to release one: the 2026-07-26 explicit
        # un-denial correction act is the only release path, and it is unbuilt.
        # Until it lands, a denial floored at this seam is PERMANENT — the
        # honest description, stated rather than discovered.
        raise NotImplementedError(
            "commit_ops has no un-denial path: releasing a persisted denial "
            "requires the explicit, confirmed, receipted correction act of "
            "ADR-059 (amended 2026-07-26), which is scheduled for the "
            "Finetuner release as #506. The seam is reserved here so that "
            "nobody routes a release through an ordinary write in the "
            "meantime. Failing loudly rather than silently not releasing."
        )

    if snapshot is not None and snapshot is not SnapshotClass.MERGE:  # pragma: no cover
        raise NotImplementedError(
            f"commit_ops captures no {snapshot!r} snapshot — `MERGE` is the only "
            "class, and widening coverage beyond the import writers stays "
            "blocked behind #339 (ADR-063 amendment (5)). Failing loudly rather "
            "than silently not capturing."
        )

    if record is None:
        record = await _get_latest(db)
    if record is None:
        raise LookupError("No profile found — import a CV or create a profile first")

    # The exact bytes an ADR-042 undo restores: the profile as it stands BEFORE
    # any op is applied. Bound here, at the top, so nothing downstream can
    # quietly redefine what "pre-merge" means; the DB row is only written at the
    # very end, so this reference stays the pre-op state throughout.
    pre_op_json = record.profile_json

    current = MasterProfileData.model_validate(record.profile_json)
    if current.metadata is None:
        current.metadata = ProfileMetadata(
            completeness_score=current.calculate_completeness()
        )

    # ── Invariant 1 — apply_ops is the only path from intent to state ────────
    applied = apply_ops(current, list(ops), provenance.source)
    profile = applied.profile
    if profile.metadata is None:  # apply never strips metadata; belt & braces
        profile.metadata = ProfileMetadata()

    now = datetime.now(timezone.utc)

    # Parked asks and disputes land on the vault's own channels. Order matches
    # the bridges': engine ambiguities first, then the applier's own.
    confirmations = [
        _to_pending_confirmation(a, source=provenance.source)
        for a in list(ambiguities) + list(applied.pending_confirmations)
    ]
    # UNCONDITIONAL since #480 PR 5. It was briefly a `park_confirmations`
    # parameter, because a durable park is only safe where a durable CLEAR
    # exists: the interview resolves its own asks in session state (#187) and
    # touched no metadata, so parking an interview turn's asks would have let a
    # LATER session re-ask something the candidate had already answered.
    # `ResolveConfirmation` is that clear, and the interview's in-session path
    # now goes through it — so park and clear are one lifecycle again, and an
    # ask outliving the session that raised it is the POINT rather than a bug.
    profile.metadata.pending_confirmations.extend(confirmations)
    # Disputes park the same way; `ResolveField` closes them.
    profile.metadata.pending_conflicts.extend(applied.conflicts)

    # ADR-059 — the reconciler's own denial verdict is persisted whether or not
    # the turn also applied real ops. A denial-only turn must not go unrecorded.
    denial_changes: list[FieldChange] = []
    if grounding is not None and grounding.denials:
        denial_changes = record_denials(
            profile.metadata,
            list(grounding.denials),
            statement=grounding.text,
            source=provenance.source,
            when=now,
        )

    # ── Invariant 2 — the persisted-denial re-floor ──────────────────────────
    # It runs HERE, after the ops land and before the assignment, so it sees the
    # post-op profile including the denial `record_denials` just wrote above.
    refloored = _refloor_persisted_denials(profile)

    # ── Invariant 6 — deterministic skill enrichment, unconditional ──────────
    # `enrich_skills` IS the deterministic pass plus the LLM estimate for the
    # skills it could not date, so a provider layers phase 2 on top rather than
    # replacing phase 1 — the property #337 fixed and ADR-058 clause 2 requires.
    if enrichment is EnrichPolicy.DETERMINISTIC:
        metadata = profile.metadata
        if llm_provider is not None:
            profile = await enrich_skills(profile, llm_provider)
        else:
            profile = enrich_skills_deterministic(profile)
        profile.metadata = metadata

    # ── Invariant 3 + 7 — the trail is unconditional; the receipt is separate ─
    receipt_changes = (
        list(applied.changes) + denial_changes + list(applied.demotions) + refloored
    )
    enrichment_record = EnrichmentRecord(
        timestamp=now,
        source=provenance.source,
        source_session_id=provenance.session_id,
        changes=receipt_changes,
        # US161 (ADR-041 amended) — merge statistics ride out of the applier
        # because only an import intake can compute them (`ApplyImportMerge`);
        # `None` for every other batch, which is what the field already means.
        reconciliation=applied.reconciliation,
    )
    profile.metadata.enrichment_history.append(enrichment_record)

    # ── Invariants 4 + 5 — completeness and the clocks ───────────────────────
    completeness = profile.calculate_completeness()
    profile.metadata.completeness_score = completeness
    profile.metadata.last_updated = now

    # ── Invariant 8 — the last gate before assignment ────────────────────────
    # `apply_ops` already round-trips its own output; everything above mutates
    # metadata afterwards, so the guarantee is re-established here.
    final = _ensure_loadable(profile, fallback=applied.profile)
    if final is applied.profile:
        # `_ensure_loadable` hands back a freshly re-validated object on success,
        # so getting the fallback object back IS the failure signal — and
        # `applied.profile` shares the very metadata object that just failed to
        # load, so it is not a safe fallback either. Reload the untouched
        # persisted state: persist NOTHING rather than half a turn. Reaching
        # here is a bug, hence ERROR.
        logger.error(
            "commit_ops: the committed profile failed its load round-trip; the "
            "vault is left UNCHANGED for this turn (source=%s intake=%s)",
            provenance.source,
            provenance.intake,
        )
        final = MasterProfileData.model_validate(record.profile_json)

    # ── The ADR-042 pre-merge snapshot (import intakes only) ─────────────────
    # Captured BEFORE the row is overwritten and keyed to THIS write's
    # enrichment record, so `undo_last_merge` can both restore the pre-import
    # state and tell whether that merge is still the profile's head. Same
    # coverage and same class as the import writers captured inline before PR 2
    # — the omission everywhere else is now a parameter that says so.
    if snapshot is SnapshotClass.MERGE:
        await capture_pre_merge_snapshot(
            db,
            profile_id=record.id,
            profile_json=pre_op_json,
            enrichment_record_id=enrichment_record.id,
        )

    payload = final.model_dump(mode="json")
    with authorized_profile_write():
        record.profile_json = payload
        record.updated_at = now
        if embedding_provider is not None:
            record.embedding = await _compute_embedding(payload, embedding_provider)
        else:
            _log_embedding_staleness_once()
        # Flush, NOT commit — the caller owns the transaction.
        await db.flush()

    logger.debug(
        "commit_ops: %d op(s) via %s/%s (grounded=%s) → %d change(s), %d denial(s), "
        "%d demotion(s), %d re-floored",
        len(ops),
        provenance.source,
        provenance.intake,
        grounding is not None,
        len(applied.changes),
        len(denial_changes),
        len(applied.demotions),
        len(refloored),
    )

    return CommitResult(
        record=record,
        profile=final,
        changes=list(applied.changes),
        denials=denial_changes,
        demotions=list(applied.demotions),
        refloored=refloored,
        conflicts=list(applied.conflicts),
        pending_confirmations=confirmations,
        enrichment_record=enrichment_record,
        completeness=completeness,
    )
