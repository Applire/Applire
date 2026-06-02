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

from pydantic import BaseModel, Field

from applire.schemas.gap_cluster import GapClusterSchema


class RequirementBreakdownItem(BaseModel):
    requirement: str
    source: str  # "required" | "nice_to_have"
    status: str  # "direct" | "partial" | "gap"
    slot: float
    earned: float
    reason: str = ""


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
    gap_clusters: list[GapClusterSchema] = Field(default_factory=list)
    requirement_breakdown: list[RequirementBreakdownItem] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}
