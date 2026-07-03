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

# backend/applire/schemas/cv_sections.py
"""Pydantic schemas for the CV Section Editor API (Sprint 9, ADR-019)."""
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field


class SnapshotPosition(BaseModel):
    """One work history entry as stored in content_snapshot."""
    id: str  # stable UUID string assigned at snapshot time
    index: int  # index in tailored_data.work_history — used for override application
    title: str
    company: str
    period: str
    bullets: list[str]


class ContentSnapshot(BaseModel):
    """Structured rendering context captured at CV generation time."""
    introduction: str
    positions: list[SnapshotPosition]
    skills: list[str]


class GapHintItem(BaseModel):
    id: str
    label: str
    # ADR-019 amended 2026-07-03 (#117): the ledger entry's evidence status decides
    # the CTA — "claimable" (profile-backed, offer editor/Kaile) vs "honest" (not in
    # the profile, route to the enrichment interview; never invite a written claim).
    kind: Literal["claimable", "honest"] = "claimable"


class SectionItem(BaseModel):
    section_id: str  # e.g. "introduction", "position::uuid", "skills"
    label: str       # Human-readable, e.g. "Introduction", "Senior Engineer — SAP"
    content: str     # Snapshot content merged with override (override wins)
    has_override: bool
    gaps: list[GapHintItem]


class CVSectionsResponse(BaseModel):
    sections: list[SectionItem]
    general_gaps: list[GapHintItem]


class SectionPatchRequest(BaseModel):
    content: str = Field(..., max_length=10_000)
    save_to_profile: bool = False


class SectionPatchResponse(BaseModel):
    html: str
    overrides_applied: list[str]
    resolved_gaps: list[str] = []


class AssistStartRequest(BaseModel):
    gap_id: str


class AssistStartResponse(BaseModel):
    session_id: str
    question: str


class AssistAnswerRequest(BaseModel):
    session_id: str
    answer: str


class AssistAnswerResponse(BaseModel):
    suggestion: str


class RewriteRequest(BaseModel):
    directions: str = Field("", max_length=2000)
    gap_ids: list[str] = Field(default_factory=list)


class RewriteResponse(BaseModel):
    suggestion: str
