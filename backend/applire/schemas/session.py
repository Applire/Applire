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
    # When set with mode="targeted": scopes to a 1-question micro-session for Gap-Click mode
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
    # Legacy fields kept for backwards compatibility
    question: str  # same as first_question
    gaps_total: int
    gaps_remaining: int
    choices: list[str] | None = None
    resumed: bool = False  # True only when resuming an in-progress session the
    # user has already answered at least one question in (drives the "welcome
    # back" banner). A freshly created/idempotently-returned session is False.


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
    # Populated when ProfileUpdater detects a merge conflict (19.10)
    pending_conflicts: list[ConflictSummary] | None = None
    # Populated when the reconciler flags an ambiguity it will not guess (US185)
    pending_confirmations: list[ConfirmationPrompt] | None = None


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
