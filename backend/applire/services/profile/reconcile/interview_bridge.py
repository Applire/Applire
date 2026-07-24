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

"""Interview-path bridge over the ADR-046 reconciler (US182a).

The interview holds profile JSONB dicts; the engine works on MasterProfileData.
This helper does the dict<->model bridge once, writes the enrichment trail, and
maps engine Conflicts to the interview's ConflictSummary."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import (
    Conflict,
    EnrichmentRecord,
    FieldChange,
    MasterProfileData,
    ProfileMetadata,
)
from applire.schemas.session import ConflictSummary
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import RequestConfirmation
from applire.services.profile.reconcile.stance import record_denials
from applire.utils.display import format_display_value


@dataclass
class InterviewTurnResult:
    profile_dict: dict
    changes: list[FieldChange]
    addressed: bool  # True iff the answer produced at least one profile change
    conflict_summaries: list[ConflictSummary] = field(default_factory=list)
    pending_confirmations: list[RequestConfirmation] = field(default_factory=list)
    # #231 — True iff this turn recorded a NEW/refreshed explicit denial (even
    # when `addressed` is False, i.e. nothing else in the profile changed).
    # Kept separate from `addressed`: a denial must never gate the ledger
    # upgrade / gap-advance logic that `addressed` drives (F8 — denying a
    # skill must not read as "resolved this gap").
    denial_recorded: bool = False


def _to_summary(conflict: Conflict) -> ConflictSummary:
    return ConflictSummary(
        conflict_id=conflict.conflict_id,
        field=conflict.field,
        old_value=format_display_value(conflict.existing_value),
        new_value=format_display_value(conflict.incoming_value),
    )


async def reconcile_interview_turn(
    *, profile_dict: dict, gap: str, question: str, answer: str,
    provider: LLMProvider, session_id: str, lang: str = "en",
) -> InterviewTurnResult:
    before = MasterProfileData.model_validate(profile_dict)
    new_info = {"gap": gap, "question": question, "answer": answer}
    result = await reconcile(before, new_info, "interview", provider, lang)
    applied = apply_ops(before, result.ops, "interview")

    # #231 — persist the reconciler's own denial verdict (rc.denials) into the
    # vault. Mutating applied.profile.metadata HERE (still a model, not a
    # dict) so the model_dump below carries it through in one pass; ProfileMetadata
    # is created if a prior turn somehow left it unset (apply_ops never strips it).
    if applied.profile.metadata is None:
        applied.profile.metadata = ProfileMetadata()
    denial_changes = record_denials(
        applied.profile.metadata,
        result.denials,
        statement=answer,
        source="interview",
        when=datetime.now(timezone.utc),
    )

    updated = applied.profile.model_dump(mode="json")
    # The interview's profile is JSONB-dict at this point, so the enrichment
    # trail is appended dict-side here (callers no longer write it themselves).
    receipt_changes = applied.changes + denial_changes
    if receipt_changes:
        meta = dict(updated.get("metadata") or {})
        history = list(meta.get("enrichment_history") or [])
        history.append(
            EnrichmentRecord(
                timestamp=datetime.now(timezone.utc),
                source="interview",
                source_session_id=session_id,
                changes=receipt_changes,
            ).model_dump(mode="json")
        )
        meta["enrichment_history"] = history
        updated["metadata"] = meta

    pending = list(result.ambiguities) + list(applied.pending_confirmations)
    return InterviewTurnResult(
        profile_dict=updated,
        changes=receipt_changes,
        # `addressed` stays exactly bool(applied.changes) — a denial must
        # never read as "this gap was addressed" (F8: it drives the ledger
        # upgrade + gap-advance logic in session.py).
        addressed=bool(applied.changes),
        conflict_summaries=[_to_summary(c) for c in applied.conflicts],
        pending_confirmations=pending,
        denial_recorded=bool(denial_changes),
    )
