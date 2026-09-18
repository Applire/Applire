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

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Child resource summaries — lightweight DTOs for the FlowStateResponse
# ---------------------------------------------------------------------------

FlowStep = Literal[
    "jd_analysis",
    "cv_import",
    "gap_analysis",
    "interview",
    "cv_generation",
    "complete",
]


class JobAnalysisSummary(BaseModel):
    job_id: uuid.UUID
    role_title: str


class GapAnalysisSummary(BaseModel):
    gap_analysis_id: uuid.UUID
    match_score: float
    critical_gaps_count: int
    category_c_count: int


class InterviewSummary(BaseModel):
    session_id: uuid.UUID
    mode: Literal["targeted", "guided"]
    status: Literal["active", "complete"]
    questions_asked: int
    hard_ceiling: int


class CVSummary(BaseModel):
    cv_id: uuid.UUID
    pdf_url: str
    expires_at: datetime


class CoverLetterSummary(BaseModel):
    cover_letter_id: uuid.UUID
    status: str
    template: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateFlowRequest(BaseModel):
    job_id: uuid.UUID | None = None


class CreateFlowResponse(BaseModel):
    flow_id: uuid.UUID
    user_type: Literal["new", "returning"]
    current_step: FlowStep
    available_actions: dict[str, str]
    job_summary: JobAnalysisSummary | None = None


class AdvanceFlowRequest(BaseModel):
    step: str
    # Recorded when advancing into a step that produces an artifact:
    #   gap_analysis    → gap_analysis_id   (required)
    #   interview       → interview_session_id (required)
    #   cv_generation   → generated_cv_id   (recorded when supplied — #676 line 35:
    #                     the step is entered in order to produce the CV, so the id
    #                     usually arrives on a second, idempotent advance)
    #   complete        → generated_cv_id   (required)
    # Any other step answers with a `notices` entry instead of dropping the id.
    artifact_id: uuid.UUID | None = None


class FlowStateResponse(BaseModel):
    flow_id: uuid.UUID
    job_id: uuid.UUID | None = None
    application_id: uuid.UUID | None = None  # linked Application, if any
    user_type: Literal["new", "returning"]
    current_step: FlowStep
    available_actions: dict[str, str]
    # Populated as the flow progresses
    job_summary: JobAnalysisSummary | None = None
    profile_completeness: float | None = None
    gap_summary: GapAnalysisSummary | None = None
    interview_summary: InterviewSummary | None = None
    cv_summary: CVSummary | None = None
    cover_letter_summary: CoverLetterSummary | None = None
    created_at: datetime
    updated_at: datetime
    #: #676 line 35 — advance-only, additive, default-empty: what the call could
    #: not do but did not fail over. Today its one producer is an `artifact_id`
    #: passed at a step that records none (`unrecordable_artifact_notice`), which
    #: used to be dropped in silence. A READ (`get_flow_state`) never populates
    #: it, and no existing client breaks on a new empty list.
    notices: list[str] = Field(default_factory=list)
