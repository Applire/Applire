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
This helper does the model bridge once, commits the turn through ADR-063's
`commit_ops`, and maps engine Conflicts to the interview's ConflictSummary.

#480 PR 2 — it takes the session and the profile row because it IS the
interview's intake adapter, the same shape `submit_testimony` and the
agent-claims bridge took in PR 1: reconcile, then hand the ops and the turn's
grounding to the one write path. It **flushes and never commits** (ADR-063
amended clause 6) — `services/session.py` writes `session.state` in the same
transaction as the vault, and splitting that could desync "gap addressed" from
the profile. Both doors keep their own `db.commit()`."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.profile import MasterProfile
from applire.providers.llm.base import LLMProvider
from applire.schemas.profile import (
    Conflict,
    FieldChange,
    MasterProfileData,
    PendingConfirmation,
)
from applire.schemas.session import ConflictSummary
from applire.services.profile.commit import (
    CommitProvenance,
    TurnGrounding,
    commit_ops,
)
from applire.services.profile.reconcile.engine import reconcile
from applire.utils.display import format_display_value

_SOURCE = "interview"


@dataclass
class InterviewTurnResult:
    profile_dict: dict
    changes: list[FieldChange]
    addressed: bool  # True iff the answer produced at least one profile change
    conflict_summaries: list[ConflictSummary] = field(default_factory=list)
    # The engine's ambiguities plus the applier's own, in that order — since
    # #480 PR 2 these are the committer's `PendingConfirmation`s (question,
    # options and context are identical). They are deliberately NOT parked on
    # `metadata.pending_confirmations`: see `park_confirmations=False` at the
    # commit call below. Session-only, exactly as before.
    pending_confirmations: list[PendingConfirmation] = field(default_factory=list)
    # #231 — True iff this turn recorded a NEW/refreshed explicit denial (even
    # when `addressed` is False, i.e. nothing else in the profile changed).
    # Kept separate from `addressed`: a denial must never gate the ledger
    # upgrade / gap-advance logic that `addressed` drives (F8 — denying a
    # skill must not read as "resolved this gap").
    denial_recorded: bool = False
    # ADR-064 — the raw concept text(s) this turn's `record_denials` call
    # touched (mirrors `result.denials`, stripped). The interview's denial
    # transfer-probe trigger (session.py) reads this to find WHICH concept to
    # check for JD-criticality / probed-state — never re-parses the answer.
    denied_concepts: list[str] = field(default_factory=list)


def _to_summary(conflict: Conflict) -> ConflictSummary:
    return ConflictSummary(
        conflict_id=conflict.conflict_id,
        field=conflict.field,
        old_value=format_display_value(conflict.existing_value),
        new_value=format_display_value(conflict.incoming_value),
    )


async def reconcile_interview_turn(
    db: AsyncSession,
    *,
    profile_record: MasterProfile,
    gap: str,
    question: str,
    answer: str,
    provider: LLMProvider,
    session_id: str,
    lang: str = "en",
) -> InterviewTurnResult:
    """Reconcile one interview answer into the vault. ONE write path.

    Everything this bridge used to do inline after `apply_ops` — record the
    denials, build the receipt, append the trail, hand a dict back for the
    caller to assign — is the committer's invariant set since #480 PR 2. What
    is left is the interview's own wire shape: the engine call, and the mapping
    of the committed outcome onto the interview's DTOs.

    Two holes close by construction. The trail was gated on
    `if receipt_changes:`, so a turn that changed nothing left no trace it had
    happened; it is unconditional now. And the completeness recompute, which
    was import-only, now runs on every turn (ADR-063 amendment (4)).
    """
    before = MasterProfileData.model_validate(profile_record.profile_json)
    new_info = {"gap": gap, "question": question, "answer": answer}
    result = await reconcile(before, new_info, _SOURCE, provider, lang)

    committed = await commit_ops(
        db,
        result.ops,
        CommitProvenance(
            source=_SOURCE,
            intake="interview",
            session_id=session_id,
            actor="candidate",
        ),
        record=profile_record,
        # §7.4 — a turn-based intake passes its turn text; the committer records
        # the reconciler's own denial verdict about it (#231), so this bridge no
        # longer calls `record_denials` itself. Stance and attribution already
        # ran over the same text inside `reconcile()` above.
        grounding=TurnGrounding(
            text=answer, question=question, gap=gap, denials=list(result.denials)
        ),
        ambiguities=list(result.ambiguities),
        # Ambiguities deliberately NOT parked durably — a durable park without a
        # durable clear resurfaces answered confirmations; park+clear land
        # together in #480 PR 5 (`ResolveConfirmation`). The interview resolves
        # its own asks in SESSION STATE (#187), which never touches
        # `metadata.pending_confirmations`, so parking them here would let a
        # LATER session rebuild a confirmation cluster for an ask the candidate
        # already answered — worse than today, and this build's rule is that
        # every intermediate state leaves `main` a strict superset. They still
        # reach the caller on the result, exactly as before.
        park_confirmations=False,
        # ADR-063 amendment (5) — an interview turn snapshots NOTHING. Ten of
        # them would otherwise evict the import snapshot the undo exists for.
        snapshot=None,
    )

    return InterviewTurnResult(
        profile_dict=committed.record.profile_json,
        changes=committed.enrichment_record.changes,
        # `addressed` stays exactly "did positive content land" — a denial must
        # never read as "this gap was addressed" (F8: it drives the ledger
        # upgrade + gap-advance logic in session.py). `CommitResult` keeps that
        # separation for us: denials and demotions have their own lists and are
        # deliberately absent from `changes`.
        addressed=bool(committed.changes),
        conflict_summaries=[_to_summary(c) for c in committed.conflicts],
        pending_confirmations=committed.pending_confirmations,
        denial_recorded=bool(committed.denials),
        denied_concepts=[d.strip() for d in result.denials if d and d.strip()],
    )
