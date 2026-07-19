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

from pydantic import BaseModel, Field

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
