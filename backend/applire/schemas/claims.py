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

"""The `claims/1` testimony contract for the agent door (E045, ADR-054).

Claims are free-text testimony from an agent-run interview — the agent is the
interviewer, Applire is the notary. The ADR-046 reconciler structures the
statement independently; callers never author profile ops directly. Published
as MCP resource ``schema://claims``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from applire.schemas.profile import Conflict, FieldChange, PendingConfirmation

CLAIMS_SCHEMA_VERSION = "claims/1"


class ClaimItem(BaseModel):
    """One elicited fact, in the candidate's own words."""

    model_config = {"extra": "forbid"}

    statement: str = Field(
        min_length=1,
        max_length=2000,
        description=(
            "The candidate's answer in their own words — the claim's testimony. "
            "This text is the ONLY grounding corpus for token claims: skills, "
            "languages, certifications and figures the candidate did not state "
            "here are dropped by the stance guard."
        ),
    )
    question: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "The interviewer's question that elicited the statement (context "
            "for the reconciler; never grounds token claims)."
        ),
    )
    gap: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "An exact `concept` string from the job's keyword ledger "
            "(`analyze_gaps` output). Requires job_id. When the claim produces "
            "profile changes, the matching ledger entry is upgraded with the "
            "statement as evidence. Values not exactly present in the ledger "
            "reject the whole call."
        ),
    )


class ClaimsSubmission(BaseModel):
    """A batch of claims from one agent-run interview session."""

    model_config = {"extra": "forbid"}

    claims: list[ClaimItem] = Field(min_length=1, max_length=20)


# ── Result envelope (returned by submit_claims; not part of the input
#    contract published at schema://claims) ───────────────────────────────────


class ClaimResult(BaseModel):
    """Per-claim outcome. The three lists are PARALLEL — one claim can yield
    changes AND a confirmation AND a conflict (ApplyResult semantics); `status`
    is derived with precedence error > needs_confirmation > conflict > applied
    > denial_recorded > no_change.

    `denial_recorded` (#231): the claim's testimony explicitly denied a skill
    ("I did not personally configure the embedding models…") and nothing else
    in the profile changed. The denial is NOT silently dropped as `no_change`
    — it is persisted to `metadata.denied_concepts` (see `changes`, which
    carries the receipt FieldChange) and the keyword ledger's denial override
    (services.keyword_ledger) then reads it as a hard floor no later adjacency
    inference can override."""

    index: int
    status: Literal[
        "error", "needs_confirmation", "conflict", "applied", "denial_recorded", "no_change"
    ]
    changes: list[FieldChange] = Field(default_factory=list)
    confirmations: list[PendingConfirmation] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    detail: str | None = None


class SubmissionResult(BaseModel):
    submission_id: str
    schema_version: str = CLAIMS_SCHEMA_VERSION
    results: list[ClaimResult]
    ledger_upgraded: list[str] = Field(default_factory=list)
    pending_review_count: int = 0
