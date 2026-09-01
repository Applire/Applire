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

import uuid
from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    job_id: uuid.UUID
    # None = auto-detect based on profile completeness_score vs MODE_B_COMPLETENESS_THRESHOLD
    mode: Literal["targeted", "guided", "profile_enrich"] | None = None
    # #627 — authoritative whenever set, regardless of `mode`: scopes to a
    # 1-question micro-session for Gap-Click mode (create_session routes on
    # target_gap alone; the resulting session is always mode="targeted").
    target_gap: str | None = None


class SessionMessageRequest(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SessionCreateResponse(BaseModel):
    session_id: uuid.UUID
    mode: Literal["targeted", "guided"]
    first_question: str
    estimated_questions: int  # soft target mid-point for the resolved mode
    # issue #245 (NEW-4) — the actual configured hard_ceiling (InterviewSession.
    # hard_ceiling), read-only. `estimated_questions` is a soft midpoint
    # ((target_min + hard_ceiling) // 2, e.g. 7 for MODE A) that a real session
    # can legitimately overshoot by a wide margin (a founder-acceptance run hit
    # the real ceiling of 12 — 70% over the "~7" copy). The frontend now shows
    # an honest upper bound derived from this field instead of the midpoint.
    hard_ceiling: int = 0
    # Legacy fields kept for backwards compatibility
    question: str  # same as first_question
    gaps_total: int
    gaps_remaining: int
    choices: list[str] | None = None
    resumed: bool = False  # True only when resuming an in-progress session the
    # user has already answered at least one question in (drives the "welcome
    # back" banner). A freshly created/idempotently-returned session is False.
    # issue #241 (F9/F10/F16 item 1) — the interview split-screen cluster
    # tracker was inferring the "active" cluster from gaps_remaining via array
    # arithmetic, which desyncs from the real critical_gaps/gap_clusters order
    # (follow-up questions on the same gap, skipped gaps, gate/conflict entries
    # interleaved ahead of real clusters). These are the actual server-side
    # anchors: current_gap_id is the critical_gaps entry (== the gap-cluster id
    # for a real MODE A cluster) the session is currently asking about, if any;
    # addressed_gap_ids are the entries already resolved. Both are read-only —
    # they mirror InterviewState fields that already existed, nothing new is
    # computed. A value that doesn't match a real cluster id (a gate:/conflict:/
    # confirmation: pseudo-entry, or a MODE B section name) is honestly not a
    # cluster the frontend tracker knows about; it should not be force-mapped.
    current_gap_id: str | None = None
    addressed_gap_ids: list[str] = []
    # #259 run-4 finding 9 — the server-tracked question count (InterviewSession.
    # questions_asked). Defaults to 1 (a freshly created session's first
    # question), matching every create-path's actual starting value; a RESUMED
    # session reports the record's real count so the frontend counter doesn't
    # reset to "1 of up to N" on a page refresh despite real server-side
    # progress (SessionStateResponse already exposed this — this closes the
    # same gap on the create/resume response the frontend actually calls).
    questions_asked: int = 1


class ConflictSummary(BaseModel):
    """A detected merge conflict surfaced during the interview (19.10)."""
    conflict_id: str  # stable identifier: "{field}:{old_value}" hash
    field: str
    old_value: str
    new_value: str


class ConfirmationPrompt(BaseModel):
    """An ambiguity the reconciler could not resolve on its own (US185, ADR-046).

    The engine never guesses entity identity; when it is unsure (synonym role,
    project-vs-position, DE↔EN employer) it asks. ``options`` drive the answer
    buttons; ``context`` carries the two entities being compared so the UI can
    show what is being merged."""
    question: str
    options: list[str] = []
    context: dict = {}


class SessionMessageResponse(BaseModel):
    complete: bool
    question: str | None = None
    gaps_remaining: int | None = None
    choices: list[str] | None = None
    # Populated when complete=True
    reason: Literal["gaps_resolved", "user_ended", "max_questions_reached"] | None = None
    questions_asked: int | None = None
    gaps_resolved: int | None = None
    gaps_unresolved: list[str] | None = None
    completeness_score: float | None = None
    # Whether the reconciler actually wrote a change on the final turn (vs a
    # no-op decline). Set on the ceiling-hit completion (targeted micro-session)
    # so a single-turn caller (resolve_gap) can tell "applied" from "no change".
    changes_applied: bool | None = None
    # #231 — the final turn recorded a NEW/refreshed explicit denial (even
    # when `changes_applied` is False). Lets a single-turn caller
    # (resolve_gap) distinguish "candidate explicitly said no, recorded to
    # the vault" from a genuine no-op turn.
    denial_recorded: bool | None = None
    # Populated when ProfileUpdater detects a merge conflict (19.10)
    pending_conflicts: list[ConflictSummary] | None = None
    # Populated when the reconciler flags an ambiguity it will not guess (US185)
    pending_confirmations: list[ConfirmationPrompt] | None = None
    # issue #241 item 1 — see SessionCreateResponse for the honesty rationale.
    # current_gap_id: the critical_gaps entry this turn's question belongs to
    # (unchanged on a follow-up/re-ask, advanced on a resolved gap). None only
    # when there is no current gap (should not occur on a non-complete turn).
    current_gap_id: str | None = None
    addressed_gap_ids: list[str] | None = None


class SessionStateResponse(BaseModel):
    """Returned by GET /api/session/{id} — used for agent recovery and pause/resume."""

    session_id: uuid.UUID
    job_id: uuid.UUID | None  # None for a standalone profile-review session (US165)
    mode: Literal["targeted", "guided"]
    status: Literal["active", "complete", "expired"]
    questions_asked: int
    hard_ceiling: int
    current_question: str | None  # None if session is complete
    gaps_remaining: int
    completeness_score: float
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None


# ---------------------------------------------------------------------------
# Internal state stored in JSONB — not exposed via API
# ---------------------------------------------------------------------------


class InterviewState(TypedDict):
    mode: str  # "targeted" | "guided" | "profile_enrich"
    job_id: str | None
    gap_analysis_id: str | None  # None for MODE B until lazy analysis
    profile_id: str
    # MODE A: ordered gap strings (C-first, then B)
    # MODE B: ordered section names to build
    critical_gaps: list[str]
    gap_categories: dict  # {gap_str: "B" | "C"} — empty dict for MODE B
    gap_clusters_by_id: dict
    addressed_gaps: list[str]
    current_gap_index: int
    current_question: str
    current_choices: list | None
    messages: list[dict]  # {"role": "assistant"|"user", "content": "..."}
    questions_asked: int
    hard_ceiling: int
    # Sprint 15 additions (optional — missing keys default to {} / [] / [])
    questions_per_gap: dict   # gap_str → questions asked so far for this gap
    skipped_gaps: list[str]   # gaps resolved transitively via cross-gap answer
    full_gaps: list[str]      # full gap list from analysis; set for micro-sessions only
    na_gaps: list[str]        # gaps dismissed as N/A by the user (Mode C)
    # US163: descriptors for any deferred Tier-1 gate prepended to critical_gaps,
    # keyed by "gate:<upload_id>" — present only when a parked gate was injected.
    gate_clusters: dict
    # US165: descriptors for the open Tier-2 conflicts a standalone profile-review
    # session walks, keyed by "conflict:<conflict_id>". Present only for that entry.
    conflict_clusters: dict
    # #627 — the authoritative "is this a Gap-Click micro-session" marker
    # (services.session.is_micro_session). Every session _build_state creates
    # now stamps this explicitly (False for MODE A/B, True for a micro-
    # session); missing only on a row persisted before this field existed, in
    # which case is_micro_session() falls back to hard_ceiling == 1.
    micro_session: bool
