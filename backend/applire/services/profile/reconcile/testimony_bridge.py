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

"""Testimony-path bridge over the ADR-046 reconciler (#258).

Free-text testimony — a pasted/uploaded narrative "anything else recruiters
should know" — runs through the SAME reconcile -> stance -> apply chain as the
interview (`interview_bridge`) and agent-claims (`agent_bridge`) doors, with a
distinct `testimony` provenance marker. Unlike `submit_claims` (itemized
claims, <=2000 chars each, elicited turn-by-turn by an interviewer), testimony
is the candidate's OWN whole free-form document — reconciled as ONE `new_info`
payload, the same shape `interview_bridge` already uses for a single answer.

This is the UI door's and the agent door's shared entry point (ADR-058
door-parity invariant, #258): both `routers/profile.py`'s POST
/api/profile/testimony and the MCP `submit_testimony` tool call this exact
function, so the vault effect never depends on which door submitted the text.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from applire.exceptions import LLMTruncatedError
from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import EnrichmentRecord, MasterProfileData, ProfileMetadata
from applire.schemas.testimony import TestimonyResult
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.import_bridge import _to_pending_confirmation
from applire.services.profile.reconcile.stance import record_denials

_SOURCE = "testimony"


def _derive_status(
    *, has_confirmations: bool, has_conflicts: bool, has_applied_changes: bool, has_denial: bool
) -> str:
    """Precedence: needs_confirmation > conflict > applied > denial_recorded >
    no_change (the "error" status is assigned separately, before this is ever
    reached — a truncated reconcile never gets this far). Mirrors
    `agent_bridge._derive_status` (#231/#259) for a single testimony submission
    instead of a per-claim batch."""
    if has_confirmations:
        return "needs_confirmation"
    if has_conflicts:
        return "conflict"
    if has_applied_changes:
        return "applied"
    if has_denial:
        return "denial_recorded"
    return "no_change"


async def submit_testimony(
    text: str,
    db: AsyncSession,
    provider: LLMProvider,
    lang: str = "en",
) -> TestimonyResult:
    """Reconcile one free-text testimony submission into the vault.

    Raises `LookupError` when no profile exists yet (mirrors `agent_bridge`).
    A truncated reconcile call is reported as an honest `status: "error"`
    result rather than raised or silently swallowed — nothing is persisted for
    that submission (no partial-dossier merge; ADR-047 data-loss guard).
    """
    # Lazy imports: applire.services.profile imports this package's siblings.
    from applire.services.profile import _get_latest
    from applire.services.session import get_ui_language

    record = await _get_latest(db)
    if record is None:
        raise LookupError("No profile found — import a CV or create a profile first")

    lang = await get_ui_language(db)
    submission_id = str(uuid.uuid4())
    current = MasterProfileData.model_validate(record.profile_json)
    if current.metadata is None:
        current.metadata = ProfileMetadata(
            completeness_score=current.calculate_completeness()
        )

    try:
        rc = await reconcile(current, {"answer": text}, _SOURCE, provider, lang)
    except LLMTruncatedError as exc:
        return TestimonyResult(
            submission_id=submission_id,
            status="error",
            detail=str(exc)
            or (
                "Testimony was too long to reconcile in one pass — split it "
                "into shorter sections and submit again."
            ),
        )

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

    # ADR-059 — persist the reconciler's own denial verdict into the vault,
    # whether or not the submission also applied real ops.
    denial_changes = record_denials(
        current.metadata,
        rc.denials,
        statement=text,
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

    # Persist once — same shape as agent_bridge (metadata.last_updated /
    # completeness_score recompute stays import-only).
    record.profile_json = current.model_dump(mode="json")
    record.updated_at = datetime.now(timezone.utc)
    await db.commit()

    return TestimonyResult(
        submission_id=submission_id,
        status=_derive_status(
            has_confirmations=bool(confirmations),
            has_conflicts=bool(applied.conflicts),
            has_applied_changes=bool(applied.changes),
            has_denial=bool(denial_changes),
        ),
        changes=receipt_changes,
        confirmations=confirmations,
        conflicts=applied.conflicts,
    )
