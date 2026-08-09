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
2026-08-09 — row 2, the persisted-denial re-floor, belongs to PR 4 and is an
empty `refloored` placeholder here):

1. the ops are applied through `apply_ops` — the only path from intent to state;
3. the enrichment trail is **unconditional** (this is what closes the
   `if receipt_changes:` holes the testimony and agent bridges shipped: a turn
   that changed nothing left no trace that it happened);
4. the completeness recompute is **universal** (it was import-only, so the
   stored score drifted after every testimony/interview/agent write);
5. `metadata.last_updated` and `record.updated_at` both move;
6. deterministic skill enrichment runs unconditionally — a skill's duration and
   provenance must never depend on whether the caller happened to pass an LLM
   provider (ADR-058 clause 2: the same edit may not behave differently by door);
7. **receipt separation** — demotions, denials and (from PR 4) re-floorings are
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

* the persisted-denial re-floor and the `UN_DENIAL` release seam — PR 4;
* snapshot capture — `snapshot` is a declared parameter that fails loudly until
  PR 2 wires the import writers (widening beyond imports stays blocked behind
  #339, ADR-063 amendment (5));
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
from applire.services.skill_enrichment import enrich_skills_deterministic

if TYPE_CHECKING:  # pragma: no cover — import cost only
    from applire.providers.embedding.base import EmbeddingProvider

logger = logging.getLogger(__name__)


class SnapshotClass(str, Enum):
    """ADR-042 snapshot classes.

    Only `MERGE` exists today — the class the import writers capture. Universal
    snapshot coverage is BLOCKED by decision (ADR-063 amendment (5)): with
    `SNAPSHOT_MAX_PER_PROFILE = 10` pruned by recency, snapshotting every write
    lets one 10-turn interview evict the import snapshot, degrading the
    guarantee for the case it exists to protect.
    """

    MERGE = "merge"


class EnrichPolicy(str, Enum):
    """Which half of the skill enrichment the commit runs (ADR-058 clause 2)."""

    #: Phase 1 only — no provider, no LLM call, no network. The default, and
    #: unconditional: provenance and computed durations are not optional.
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
    #: PR 4 — receipts for the persisted-denial re-floor. Always empty here.
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
        snapshot: ADR-042 snapshot class. Declared for PR 2's import writers;
            passing one today fails loudly rather than silently not capturing.
        ambiguities: engine-level `RequestConfirmation`s parked alongside the
            applier's own.
        enrichment: which half of the skill enrichment to run.
        embedding_provider: `None` leaves `master_profiles.embedding` stale and
            says so once (§7.2).

    Raises:
        LookupError: no profile exists yet.
        NotImplementedError: `snapshot` is not `None` (PR 2).
    """
    # Lazy: applire.services.profile imports this package's siblings.
    from applire.services.profile import _compute_embedding, _get_latest

    if snapshot is not None:
        raise NotImplementedError(
            "commit_ops does not capture snapshots yet — the import writers wire "
            "`snapshot=SnapshotClass.MERGE` in #480 PR 2, and widening beyond "
            "them stays blocked behind #339 (ADR-063 amendment (5)). Failing "
            "loudly rather than silently not capturing."
        )

    if record is None:
        record = await _get_latest(db)
    if record is None:
        raise LookupError("No profile found — import a CV or create a profile first")

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
    profile.metadata.pending_confirmations.extend(confirmations)
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

    # ── Invariant 2 — the persisted-denial re-floor. PR 4 owns it. ───────────
    # Deliberately an empty placeholder: it must run HERE, after the ops land
    # and before the assignment, so it sees the post-op profile including any
    # denial this same turn recorded. Building it early would ship a half floor.
    refloored: list[FieldChange] = []

    # ── Invariant 6 — deterministic skill enrichment, unconditional ──────────
    if enrichment is EnrichPolicy.DETERMINISTIC:
        metadata = profile.metadata
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
        "%d demotion(s)",
        len(ops),
        provenance.source,
        provenance.intake,
        grounding is not None,
        len(applied.changes),
        len(denial_changes),
        len(applied.demotions),
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
