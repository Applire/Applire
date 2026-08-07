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

"""Agent-door bridge over the ADR-046 reconciler (E045, US254 — ADR-054).

`submit_claims` testimony lands here: agent = interviewer, Applire = notary.
Each claim's free-text statement runs through the existing reconcile → stance →
apply chain with `agent_interview` provenance; receipts, confirmation parking
and the keyword-ledger upgrade reuse the interview/import seams — a new entry
point, not new machinery (vision §8 #6)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.exceptions import LLMTruncatedError
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.providers.llm.base import LLMProvider
from applire.schemas.claims import (
    ClaimResult,
    ClaimsSubmission,
    SubmissionResult,
)
from applire.schemas.profile import EnrichmentRecord, MasterProfileData, ProfileMetadata
from applire.services.keyword_ledger import _norm, upgrade_ledger_for_concepts
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.import_bridge import _to_pending_confirmation
from applire.services.profile.reconcile.stance import record_denials

logger = logging.getLogger(__name__)

_SOURCE = "agent_interview"


def _derive_status(
    result: ClaimResult, *, has_applied_changes: bool, has_denial: bool
) -> str:
    """Documented precedence: error > needs_confirmation > conflict > applied
    > denial_recorded > no_change (the three lists are parallel — one claim
    can yield all). ``has_applied_changes``/``has_denial`` are passed
    separately from ``result.changes`` (#231): that list may carry BOTH a real
    op change and a denial receipt, and only the former counts as "applied" —
    a denial-only turn must read as its own honest status, never masquerade
    as an applied profile change."""
    if result.detail is not None:
        return "error"
    if result.confirmations:
        return "needs_confirmation"
    if result.conflicts:
        return "conflict"
    if has_applied_changes:
        return "applied"
    if has_denial:
        return "denial_recorded"
    return "no_change"


async def _latest_gap_analysis(
    job_id: uuid.UUID, db: AsyncSession
) -> GapAnalysis | None:
    result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(desc(GapAnalysis.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _validate_gap_claims(
    submission: ClaimsSubmission, job_id: uuid.UUID | None, db: AsyncSession
) -> GapAnalysis | None:
    """Up-front, before ANY LLM spend: every `gap` must be an exact (normalized
    EQUALITY, never substring — the "Go"/"R"/"AI" over-flip trap) member of the
    job's latest keyword ledger. A non-member rejects the whole call — nothing
    partially applied."""
    gap_values = [c.gap for c in submission.claims if c.gap]
    if not gap_values:
        return None
    if job_id is None:
        raise ValueError(
            "claims with `gap` set require job_id (the gap must be an exact "
            "concept from that job's keyword ledger — see analyze_gaps)"
        )
    gap_row = await _latest_gap_analysis(job_id, db)
    members = {
        _norm(e.get("concept", ""))
        for e in (gap_row.keyword_ledger if gap_row else None) or []
    } - {""}
    if not members:
        raise ValueError(
            "no keyword ledger exists for this job — run analyze_gaps first, "
            "then copy exact `concept` strings from its output"
        )
    for value in gap_values:
        if _norm(value) not in members:
            raise ValueError(
                f"gap '{value}' is not a concept in the job's keyword ledger — "
                "copy the exact `concept` string from analyze_gaps output"
            )
    return gap_row


async def submit_agent_claims(
    submission: ClaimsSubmission,
    job_id: uuid.UUID | None,
    db: AsyncSession,
    provider: LLMProvider,
) -> SubmissionResult:
    """Reconcile a batch of agent-elicited claims into the vault.

    Sequential per claim (later claims see earlier claims' profile state).
    Raises LookupError for missing profile/job, ValueError for gap-contract
    violations (mapped to -32001/-32602 at the MCP layer). The profile is
    persisted ONCE at the end; a truncated reconcile fails only its own claim.
    """
    # Lazy imports: applire.services.profile imports this package's siblings.
    from applire.services.profile import _get_latest
    from applire.services.session import get_ui_language

    record = await _get_latest(db)
    if record is None:
        raise LookupError("No profile found — import a CV or create a profile first")
    if job_id is not None:
        job = (
            await db.execute(select(JobAnalysis).where(JobAnalysis.id == job_id))
        ).scalar_one_or_none()
        if job is None:
            raise LookupError(f"Job {job_id} not found")

    gap_row = await _validate_gap_claims(submission, job_id, db)

    lang = await get_ui_language(db)
    submission_id = str(uuid.uuid4())
    current = MasterProfileData.model_validate(record.profile_json)
    if current.metadata is None:
        current.metadata = ProfileMetadata(
            completeness_score=current.calculate_completeness()
        )

    results: list[ClaimResult] = []
    ledger_upgraded: list[str] = []
    for index, claim in enumerate(submission.claims):
        new_info: dict[str, str] = {"answer": claim.statement}
        if claim.question:
            new_info["question"] = claim.question
        if claim.gap:
            new_info["gap"] = claim.gap

        try:
            rc = await reconcile(current, new_info, _SOURCE, provider, lang)
        except LLMTruncatedError as exc:
            # Data-loss guard (engine docstring): a truncated reconcile must not
            # half-merge — fail THIS claim, keep the batch going.
            results.append(
                ClaimResult(
                    index=index,
                    status="error",
                    detail=str(exc) or "LLM output truncated — restate the claim",
                )
            )
            continue

        applied = apply_ops(current, rc.ops, _SOURCE)
        current = applied.profile
        if current.metadata is None:  # apply never strips metadata; belt & braces
            current.metadata = ProfileMetadata()

        confirmations = [
            _to_pending_confirmation(a, source=_SOURCE)
            for a in list(rc.ambiguities) + list(applied.pending_confirmations)
        ]
        current.metadata.pending_confirmations.extend(confirmations)
        current.metadata.pending_conflicts.extend(applied.conflicts)

        # #231 — persist the reconciler's own denial verdict into the vault
        # (previously discarded: enforce_stance strips a denied token from
        # THIS turn's ops, but rc.denials itself never survived past the
        # call). Runs whether or not the turn also applied real ops.
        denial_changes = record_denials(
            current.metadata,
            rc.denials,
            statement=claim.statement,
            source=_SOURCE,
            when=datetime.now(timezone.utc),
        )
        receipt_changes = applied.changes + denial_changes

        if receipt_changes:
            current.metadata.enrichment_history.append(
                EnrichmentRecord(
                    timestamp=datetime.now(timezone.utc),
                    source=_SOURCE,
                    source_session_id=submission_id,
                    changes=receipt_changes,
                )
            )
            # Ledger polarity seam. The #188 addressed-gate — only a claim that
            # actually changed the profile may flip its (pre-validated,
            # exact-member) concept — is preserved EXACTLY, but it has moved
            # from the door's `if` into `upgrade=`: it now gates the upgrade,
            # not the whole block.
            #
            # #352: gating the block on `applied.changes` also gated the ADR-059
            # denial floor, and a pure denial ("actually scratch that, I have
            # never touched Kubernetes") produces no ops. So the one claim that
            # is a retraction was the one claim the floor never saw, and an
            # entry an earlier claim in the SAME batch had already flipped to
            # `direct`/`claimable` survived the candidate taking it back — the
            # stale row being the one both writers read. ADR-059 clause 3:
            # polarity is consulted at EVERY ledger write seam.
            if (
                claim.gap
                and gap_row is not None
                and gap_row.keyword_ledger
                and (applied.changes or denial_changes)
            ):
                # #341 — door parity (ADR-058 clause 2). The interview door
                # (session.py) passes the candidate's live denials into this
                # call; this door never did, so ``upgrade_ledger_for_concepts``'
                # second floor — the live, same-session denial check — was
                # structurally unreachable on the agent channel and only the
                # persisted-status floor applied. The sharp case is INSIDE one
                # claim: ``record_denials`` above has already written this
                # turn's denials onto ``current.metadata``, so a mixed
                # statement ("Docker ja, Kubernetes nie angefasst") would
                # otherwise flip its own denied concept to claimable with the
                # denial sentence as the backing evidence — the exact ADR-059
                # run-#7 blocker, one door over.
                denied_concepts = [
                    d.concept for d in current.metadata.denied_concepts if d.concept
                ]
                new_ledger, changed = upgrade_ledger_for_concepts(
                    gap_row.keyword_ledger,
                    [claim.gap],
                    claim.statement,
                    denied_concepts=denied_concepts,
                    upgrade=bool(applied.changes),
                )
                if changed:
                    # Plain _JSON column — reassign the WHOLE attribute so
                    # SQLAlchemy flags it dirty (session.py:1278 parity).
                    gap_row.keyword_ledger = new_ledger
                    # Echo the CANONICAL ledger concept, not the caller's
                    # casing (membership is normalized equality).
                    entry = next(
                        (
                            e
                            for e in new_ledger
                            if _norm(e.get("concept", "")) == _norm(claim.gap)
                        ),
                        None,
                    )
                    # ``changed`` is also True when the floor RECORDED the
                    # concept as denied. That is a real ledger write worth
                    # persisting, but it is the opposite of an upgrade —
                    # reporting it in ``ledger_upgraded`` would tell the agent
                    # its claim was accepted as a strength. The denial reaches
                    # the caller honestly, as a receipt FieldChange in
                    # ``changes``.
                    canonical = entry.get("concept", "") if entry else claim.gap
                    if entry is not None and entry.get("status") == "denied":
                        # #352 — the reversal case: an EARLIER claim in this
                        # same batch put this concept into ``ledger_upgraded``.
                        # The wire report is part of the state the upgrade
                        # wrote, so the retraction has to reach it too — a
                        # batch that ends with a denied entry must not still
                        # report the concept as upgraded.
                        ledger_upgraded[:] = [
                            c for c in ledger_upgraded if _norm(c) != _norm(canonical)
                        ]
                    else:
                        ledger_upgraded.append(canonical)

        result = ClaimResult(
            index=index,
            status="no_change",
            changes=receipt_changes,
            confirmations=confirmations,
            conflicts=applied.conflicts,
        )
        result = result.model_copy(
            update={
                "status": _derive_status(
                    result,
                    has_applied_changes=bool(applied.changes),
                    has_denial=bool(denial_changes),
                )
            }
        )
        results.append(result)

    # Persist once. metadata.last_updated / completeness_score recompute stays
    # import-only (decided: interview parity — session.py touches updated_at only).
    record.profile_json = current.model_dump(mode="json")
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return SubmissionResult(
        submission_id=submission_id,
        results=results,
        ledger_upgraded=ledger_upgraded,
        pending_review_count=sum(
            len(r.confirmations) + len(r.conflicts) for r in results
        ),
    )
