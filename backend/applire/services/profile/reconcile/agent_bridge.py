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

logger = logging.getLogger(__name__)

_SOURCE = "agent_interview"


def _derive_status(result: ClaimResult) -> str:
    """Documented precedence: error > needs_confirmation > conflict > applied
    > no_change (the three lists are parallel — one claim can yield all)."""
    if result.detail is not None:
        return "error"
    if result.confirmations:
        return "needs_confirmation"
    if result.conflicts:
        return "conflict"
    if result.changes:
        return "applied"
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

        if applied.changes:
            current.metadata.enrichment_history.append(
                EnrichmentRecord(
                    timestamp=datetime.now(timezone.utc),
                    source=_SOURCE,
                    source_session_id=submission_id,
                    changes=applied.changes,
                )
            )
            # Ledger upgrade — gated exactly like the interview's addressed-gate
            # (#188): only a claim that actually changed the profile may flip
            # its (pre-validated, exact-member) concept.
            if claim.gap and gap_row is not None and gap_row.keyword_ledger:
                new_ledger, changed = upgrade_ledger_for_concepts(
                    gap_row.keyword_ledger, [claim.gap], claim.statement
                )
                if changed:
                    # Plain _JSON column — reassign the WHOLE attribute so
                    # SQLAlchemy flags it dirty (session.py:1278 parity).
                    gap_row.keyword_ledger = new_ledger
                    ledger_upgraded.append(claim.gap)

        result = ClaimResult(
            index=index,
            status="no_change",
            changes=applied.changes,
            confirmations=confirmations,
            conflicts=applied.conflicts,
        )
        result = result.model_copy(update={"status": _derive_status(result)})
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
