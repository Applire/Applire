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
from typing import Literal

from pydantic import BaseModel, model_validator

from applire.models.application import UserStatus, WorkflowStatus


class DuplicateOfHint(BaseModel):
    """Read model for the duplicate-JD hint (E039/US220, journey Branch F).

    Rides on the analyze response when the freshly analyzed JD matches one of
    the USER'S existing applications. analyzed_at is the application's
    created_at — the shared job_analyses row's timestamp may belong to another
    user's first analysis, which must never leak into this hint.
    """

    application_id: uuid.UUID
    job_analysis_id: uuid.UUID
    company_name: str | None = None
    role_title: str | None = None
    analyzed_at: datetime
    matched_on: Literal["job", "source_url", "text"]


class StaleCVGained(BaseModel):
    """One line of the explained delta: how many enrichment changes a profile
    section gained since the CV was generated (E039/US221)."""

    section: str
    count: int


class StaleCVInfo(BaseModel):
    """Read model for the stale-CV indicator (E039/US221, journey Branch H).

    Present when the application's newest READY generated CV predates the
    newest Master-Profile enrichment record and the user hasn't dismissed the
    hint since. `gained` is the explained delta — per-section change counts
    aggregated from enrichment records newer than the CV — so the re-tailor
    nudge can say WHAT the profile gained (Branch H: "the re-tailor must
    explain what changed, or the new version erodes trust").
    latest_cv_template lets one-click re-tailor keep the version's template.
    """

    latest_cv_id: uuid.UUID
    latest_cv_created_at: datetime
    latest_cv_template: str
    profile_enriched_at: datetime
    gained: list[StaleCVGained]
    # E042/US236 (ADR-051 amendment §4): the pinned/newest ready CV's
    # persisted target page count, so a one-click re-tailor can forward the
    # same target. None on legacy/pre-E042 rows.
    target_pages: int | None = None


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
    # Stale-CV nudge dismissal (E039/US221): True stamps stale_cv_dismissed_at.
    # There is no un-dismiss — the hint re-arms by itself on the next enrichment.
    dismiss_stale_cv: bool | None = None
    # E054 / ADR-038 amendment 2026-08-23 clause 5: the user's document-language
    # choice. CLEARABLE — explicit null returns the application to automatic
    # detection. DE/EN only (clause 8).
    language_override: Literal["de", "en"] | None = None

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
    # E054: the user's document-language choice; None = automatic detection.
    language_override: str | None = None
    # Read model: the pinned CV's creation timestamp — the stable "version"
    # identity for the sent badge (an ordinal would renumber when retention
    # purges older unpinned CVs). Enriched by the service layer, not a column.
    submitted_cv_created_at: datetime | None = None
    # Read model: stale-CV indicator (E039/US221) — set by the service layer
    # when the newest ready CV predates the newest profile enrichment and the
    # user hasn't dismissed the nudge since. None = nothing to re-tailor.
    stale_cv: StaleCVInfo | None = None
    stale_cv_dismissed_at: datetime | None = None
    flow_session_id: uuid.UUID | None
    flow_current_step: str | None = None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    model_config = {"from_attributes": True}


class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]
    total: int
