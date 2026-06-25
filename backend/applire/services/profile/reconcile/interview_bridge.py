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
from applire.schemas.profile import Conflict, EnrichmentRecord, FieldChange, MasterProfileData
from applire.schemas.session import ConflictSummary
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import reconcile
from applire.services.profile.reconcile.ops import RequestConfirmation


@dataclass
class InterviewTurnResult:
    profile_dict: dict
    changes: list[FieldChange]
    addressed: bool  # True iff the answer produced at least one profile change
    conflict_summaries: list[ConflictSummary] = field(default_factory=list)
    pending_confirmations: list[RequestConfirmation] = field(default_factory=list)


def _to_summary(conflict: Conflict) -> ConflictSummary:
    return ConflictSummary(
        conflict_id=conflict.conflict_id,
        field=conflict.field,
        old_value="" if conflict.existing_value is None else str(conflict.existing_value),
        new_value="" if conflict.incoming_value is None else str(conflict.incoming_value),
    )


async def reconcile_interview_turn(
    *, profile_dict: dict, gap: str, question: str, answer: str,
    provider: LLMProvider, session_id: str, lang: str = "en",
) -> InterviewTurnResult:
    before = MasterProfileData.model_validate(profile_dict)
    new_info = {"gap": gap, "question": question, "answer": answer}
    result = await reconcile(before, new_info, "interview", provider, lang)
    applied = apply_ops(before, result.ops, "interview")

    updated = applied.profile.model_dump(mode="json")
    # The profile is already dumped to JSONB-dict form at this point, so we
    # append the enrichment trail directly to the serialised dict here. This
    # mirrors the transitional pattern in services/session.py that Task 3 will
    # replace.
    if applied.changes:
        meta = dict(updated.get("metadata") or {})
        history = list(meta.get("enrichment_history") or [])
        history.append(
            EnrichmentRecord(
                timestamp=datetime.now(timezone.utc),
                source="interview",
                source_session_id=session_id,
                changes=applied.changes,
            ).model_dump(mode="json")
        )
        meta["enrichment_history"] = history
        updated["metadata"] = meta

    pending = list(result.ambiguities) + list(applied.pending_confirmations)
    return InterviewTurnResult(
        profile_dict=updated,
        changes=applied.changes,
        addressed=bool(applied.changes),
        conflict_summaries=[_to_summary(c) for c in applied.conflicts],
        pending_confirmations=pending,
    )
