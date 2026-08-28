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

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from applire.exceptions import LLMTruncatedError
from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import MasterProfileData, ProfileMetadata
from applire.schemas.testimony import NotApplied, TestimonyResult
from applire.services.profile.commit import (
    CommitProvenance,
    TurnGrounding,
    VaultWriteRevertedError,
    commit_ops,
)
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.witness import compute_not_applied

_SOURCE = "testimony"


def _derive_status(
    *,
    has_confirmations: bool,
    has_conflicts: bool,
    has_applied_changes: bool,
    has_denial: bool,
    has_not_applied: bool = False,
) -> str:
    """Precedence (#370 amendment — `partial` inserted): needs_confirmation >
    conflict > partial > applied > denial_recorded > no_change (the "error"
    status is assigned separately, before this is ever reached — a truncated
    reconcile never gets this far). Mirrors `agent_bridge._derive_status`
    (#231/#259) for a single testimony submission instead of a per-claim
    batch, now widened by one rung.

    `partial` — `has_applied_changes` AND `has_not_applied`: some of the
    submission visibly landed and some visibly did not. `applied` therefore
    now specifically means "changes landed AND nothing is known to be
    missing" — it no longer means "applied some of it" (#370's ask).
    `has_not_applied` alone (no applied changes) does NOT elevate `no_change`
    — there is no positive change to make partial; the witness spans still
    reach the caller via `TestimonyResult.not_applied` regardless (#371).
    """
    if has_confirmations:
        return "needs_confirmation"
    if has_conflicts:
        return "conflict"
    if has_applied_changes and has_not_applied:
        return "partial"
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

    # #370 — the deterministic witness, computed over exactly what the
    # ENGINE produced (post-parse/stance/attribution, pre-commit): does every
    # numeric figure of the submitted text literally show up in an op's own
    # payload, a denial, or the PRE-TURN vault content, and which raw ops did
    # the model emit that never even passed schema validation. No
    # sentence-level channel — ADR-063 amendment, see `witness.py`'s module
    # docstring. `current` is the same pre-turn profile `reconcile()` above
    # was just handed.
    #
    # `keep=frozenset()` — `metadata` MUST be excluded, not just filtered to
    # the prompt's usual allowlist (real-provider replay, 2026-08-28):
    # `metadata.denied_concepts[*].statement` stores the PRIOR turn's entire
    # raw testimony text verbatim (5 denials x ~10.5 KB in that replay), so a
    # figure the model correctly DROPPED last turn — never written to any
    # content field — still echoes inside that statement text. Folding it in
    # would make every such figure read as "already held" on a resubmission,
    # the opposite of what the vault fold exists to fix. `prompts/gap_
    # analysis` hit the identical shape first (a denial's own text
    # token-matches FOR the thing it denies, the F4 fix) and set the
    # precedent of passing `keep=frozenset()` for exactly this reason.
    # Bookkeeping is never content, however plainly it repeats one.
    from applire.services.prompt_view import prompt_profile_view

    vault_text = json.dumps(
        prompt_profile_view(current.model_dump(mode="json"), keep=frozenset()),
        ensure_ascii=False,
    )
    not_applied: list[NotApplied] = compute_not_applied(
        text,
        rc.ops,
        rejected_ops=rc.rejected_ops,
        denials=rc.denials,
        vault_text=vault_text,
    )

    # ADR-063 — the ONE write path. Everything this door used to do inline
    # (apply, park confirmations/conflicts, record denials, receipt, assign)
    # is the committer's invariant set now; the door keeps only its own wire
    # shape. The trail in particular is no longer conditional: the old
    # `if receipt_changes:` meant a testimony that changed nothing left no
    # trace that it was ever submitted.
    try:
        committed = await commit_ops(
            db,
            rc.ops,
            CommitProvenance(
                source=_SOURCE,
                intake="testimony",
                session_id=submission_id,
                actor="candidate",
            ),
            record=record,
            # §7.4 — a turn-based intake passes its turn text; stance/attribution
            # already ran over it inside `reconcile()` above.
            grounding=TurnGrounding(text=text, denials=list(rc.denials)),
            ambiguities=list(rc.ambiguities),
            snapshot=None,
            embedding_provider=None,
        )
    except VaultWriteRevertedError as exc:
        # ADR-063 amended 2026-08-28 (#597) — same data-loss-guard idiom as
        # the LLMTruncatedError branch above: nothing was persisted for this
        # submission, reported as an honest `status: "error"` result rather
        # than raised past this door. `db.commit()` below is skipped, so the
        # session's own rollback-on-close handles anything else the caller
        # may have flushed this request.
        return TestimonyResult(
            submission_id=submission_id,
            status="error",
            detail=(
                "Your testimony could not be saved — the vault write was "
                f"reverted ({exc.detail}). Please try submitting it again."
            ),
        )
    # Flush-not-commit (ADR-063 amended clause 6): this door owns its
    # transaction exactly as before — dropping this line is a silent no-write.
    await db.commit()

    return TestimonyResult(
        submission_id=submission_id,
        status=_derive_status(
            has_confirmations=bool(committed.pending_confirmations),
            has_conflicts=bool(committed.conflicts),
            has_applied_changes=bool(committed.changes),
            has_denial=bool(committed.denials),
            has_not_applied=bool(not_applied),
        ),
        changes=committed.enrichment_record.changes,
        confirmations=committed.pending_confirmations,
        conflicts=committed.conflicts,
        not_applied=not_applied,
    )
