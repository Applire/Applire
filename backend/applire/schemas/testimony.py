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

"""The `testimony/1` free-text intake contract (#258, ADR-046/ADR-058/ADR-059).

Testimony is a candidate-authored free-text document ("anything else recruiters
should know") — a whole pasted/uploaded dossier, not an itemized elicited claim.
It runs through the SAME reconcile -> stance -> apply chain as `submit_claims`
and the interview, with a distinct `testimony` provenance marker, so the vault
effect is identical regardless of which door (UI paste box or MCP tool)
submitted it (ADR-058 door-parity invariant). Published as MCP resource
`schema://testimony`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from applire.schemas.profile import Conflict, FieldChange, PendingConfirmation

TESTIMONY_SCHEMA_VERSION = "testimony/1"

# Generous ceiling for a pasted dossier (several off-CV pages) while still
# bounding a single reconcile call's input size; not a claims-style itemized
# cap (see ClaimItem.statement's 2000 chars — a whole dossier does not fit
# there, which is exactly why this is its own door, not a submit_claims call).
TESTIMONY_MAX_CHARS = 20_000


class TestimonyRequest(BaseModel):
    """One free-text testimony submission, in the candidate's own words."""

    model_config = {"extra": "forbid"}

    text: str = Field(
        min_length=1,
        max_length=TESTIMONY_MAX_CHARS,
        description=(
            "Free-text testimony in the candidate's own words — pasted or "
            "uploaded prose, not itemized claims. This text is the ONLY "
            "grounding corpus for token claims: skills, languages, "
            "certifications and figures not stated here are dropped by the "
            "stance guard, and an explicit denial is recorded, not dropped."
        ),
    )


# ── Result envelope (returned by submit_testimony; not part of the input
#    contract published at schema://testimony) ────────────────────────────────


class TestimonyResult(BaseModel):
    """The outcome of reconciling one testimony submission into the vault.

    `status` precedence mirrors `ClaimResult` (error > needs_confirmation >
    conflict > applied > denial_recorded > no_change) — one submission can
    yield changes AND a confirmation AND a conflict AND a denial; `status`
    reports the single most-actionable outcome while `changes` /
    `confirmations` / `conflicts` carry the full parallel detail.
    """

    submission_id: str
    schema_version: str = TESTIMONY_SCHEMA_VERSION
    status: Literal[
        "error", "needs_confirmation", "conflict", "applied", "denial_recorded", "no_change"
    ]
    changes: list[FieldChange] = Field(default_factory=list)
    confirmations: list[PendingConfirmation] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    detail: str | None = None
