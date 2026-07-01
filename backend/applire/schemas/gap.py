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
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from applire.schemas.gap_cluster import GapClusterSchema


class RequirementBreakdownItem(BaseModel):
    requirement: str
    source: str  # "required" | "nice_to_have"
    status: str  # "direct" | "partial" | "gap"
    slot: float
    earned: float
    reason: str = ""


class KeywordLedgerEntry(BaseModel):
    """One JD expectation in the Keyword Ledger (ADR-048, E037)."""

    concept: str
    surface_forms: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)  # required | nice_to_have | keyword
    fit_weight: float  # 1.0 required / 0.5 nice_to_have / 0.0 keyword-only
    status: str  # "direct" | "partial" | "gap"
    evidence: str = ""
    claimable: bool


class GapAnalysisResponse(BaseModel):
    id: uuid.UUID
    job_analysis_id: uuid.UUID
    profile_id: uuid.UUID
    match_score: float | None = Field(default=None, ge=0.0, le=1.0)
    critical_gaps: list[str]
    minor_gaps: list[str]
    strengths: list[str]
    keyword_gaps: list[str]
    category_a: list[str] = Field(default_factory=list)
    category_b: list[str] = Field(default_factory=list)
    category_c: list[str] = Field(default_factory=list)
    keyword_ledger: list[KeywordLedgerEntry] = Field(default_factory=list)
    gap_clusters: list[GapClusterSchema] = Field(default_factory=list)
    requirement_breakdown: list[RequirementBreakdownItem] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("keyword_ledger", mode="before")
    @classmethod
    def _ledger_none_to_empty(cls, v):
        # Legacy gap_analyses rows (pre-E037) have NULL keyword_ledger.
        return v or []


_GAP_JOB_STATUS = Literal["pending", "processing", "ready", "failed", "expired"]


class GapJobResponse(BaseModel):
    """Response for POST /api/job/{job_id}/gap-jobs — the async gap-analysis handle.

    The kick-off returns immediately; the heavy LLM analysis runs in a background task
    (so the gaps screen can't block ~2 min or 504 fragilely). Poll
    GET /api/job/{job_id}/gap-jobs/{gap_job_id} until status is ``ready`` or ``failed``.
    """

    gap_job_id: uuid.UUID
    status: _GAP_JOB_STATUS


class GapJobStatusResponse(BaseModel):
    """Response for GET /api/job/{job_id}/gap-jobs/{gap_job_id} (async gap-analysis poll).

    ``result`` carries the same GapAnalysisResponse the synchronous path returned,
    populated when status == ``ready``. On ``failed``, ``error_code`` is a stable machine
    code the frontend localizes — the raw provider text is never surfaced.
    """

    gap_job_id: uuid.UUID
    status: _GAP_JOB_STATUS
    error_code: str | None = None
    result: GapAnalysisResponse | None = None
