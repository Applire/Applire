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

"""Application schemas — Iteration 17"""

import uuid
from datetime import datetime

from pydantic import BaseModel, model_validator

from applire.models.application import UserStatus, WorkflowStatus


class CreateApplicationRequest(BaseModel):
    job_analysis_id: uuid.UUID
    start_workflow: bool = False
    # User overrides for denormalized fields; falls back to LLM-extracted values
    company_name: str | None = None
    role_title: str | None = None
    notes: str | None = None
    deadline: datetime | None = None
    source_url: str | None = None


class PatchApplicationRequest(BaseModel):
    """Only user-managed fields. workflow_status is rejected at the service layer."""
    user_status: UserStatus | None = None
    company_name: str | None = None
    role_title: str | None = None
    notes: str | None = None
    applied_at: datetime | None = None
    deadline: datetime | None = None
    source_url: str | None = None
    # Submitted pins (E039/US219): value = pin (validated against the artifact),
    # explicit null = unpin. Same present-in-body clear semantics as the dossier fields.
    submitted_cv_id: uuid.UUID | None = None
    submitted_cover_letter_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def at_least_one_field(self) -> "PatchApplicationRequest":
        # Explicit nulls are legitimate clear requests (e.g. {"deadline": null}),
        # so "provided" means present in the body — not non-None (E039/US217).
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided.")
        return self


class ApplicationResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    job_analysis_id: uuid.UUID
    workflow_status: WorkflowStatus
    user_status: UserStatus
    company_name: str | None
    role_title: str | None
    notes: str | None
    applied_at: datetime | None
    deadline: datetime | None
    source_url: str | None
    submitted_cv_id: uuid.UUID | None = None
    submitted_cover_letter_id: uuid.UUID | None = None
    flow_session_id: uuid.UUID | None
    flow_current_step: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    model_config = {"from_attributes": True}


class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]
    total: int
