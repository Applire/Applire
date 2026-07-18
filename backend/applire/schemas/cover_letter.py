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
from typing import Literal, Optional

from pydantic import BaseModel, Field

from applire.models.cover_letter import CoverLetterStatus

CLTemplate = Literal[
    "classic_german",
    "modern_swiss",
    "executive",
    "tech_developer",
    "creative_sidebar",
    "academic",
    "compact_pro",
]

CLTone = Literal["formal", "professional", "conversational"]

# US249 (E044, ADR-054): the public, versioned cover-letter content contract.
# ``letter_data`` was an untyped dict shaped by the writer prompt; the agent
# door (render_document) needs a typed contract that validates every shape the
# system produces today WITHOUT a data migration — hence all fields carry
# defaults (legacy rows contain {"name": ...}-only sections from
# _backfill_sender_name). ``extra="forbid"`` throughout: an agent typo must
# surface as a field-path validation error, never a silently dropped section.
# The letter SUBJECT is deliberately absent — it is computed at render time
# from job.role_title and is not content data. Bump the version on any
# breaking field change; the JSON Schema ships via the MCP resource
# ``schema://cover-letter`` (US251).
LETTER_SCHEMA_VERSION = "cover-letter/1"


class LetterHeader(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = ""
    address: str = ""
    phone: Optional[str] = None
    email: Optional[str] = None
    # Present in the writer shape but never rendered by any letter template.
    # The render_document entry point STRIPS it (storage-read safety, US250).
    photo_url: Optional[str] = None


class LetterRecipient(BaseModel):
    model_config = {"extra": "forbid"}

    name: Optional[str] = None
    title: Optional[str] = None
    company: Optional[str] = None
    address: Optional[str] = None
    # System chrome on the pipeline path (_inject_letter_date). Agent door:
    # caller-supplied value kept verbatim; injected only when None (US249).
    date: Optional[str] = None


class LetterBody(BaseModel):
    model_config = {"extra": "forbid"}

    paragraphs: list[str] = Field(min_length=1)


class LetterSignature(BaseModel):
    model_config = {"extra": "forbid"}

    # System chrome like recipient.date (_normalize_signature_closing).
    closing: Optional[str] = None
    name: str = ""


class LetterData(BaseModel):
    model_config = {"extra": "forbid"}

    header: LetterHeader = Field(default_factory=LetterHeader)
    recipient: LetterRecipient = Field(default_factory=LetterRecipient)
    body: LetterBody
    signature: LetterSignature = Field(default_factory=LetterSignature)


class CoverLetterGenerateRequest(BaseModel):
    job_id: uuid.UUID
    recipient_name: Optional[str] = None
    recipient_company: Optional[str] = None
    salary: Optional[str] = None
    availability: Optional[str] = None
    motivation: Optional[str] = None
    tone: CLTone = "formal"


class CoverLetterGenerateResponse(BaseModel):
    cover_letter_id: uuid.UUID
    status: CoverLetterStatus
    html_url: str
    pdf_url: str
    expires_at: datetime


class CoverLetterStatusResponse(BaseModel):
    cover_letter_id: uuid.UUID
    status: CoverLetterStatus
    html_url: Optional[str] = None
    pdf_url: Optional[str] = None
    error_message: Optional[str] = None
    expires_at: datetime
    letter_data: Optional[dict] = None  # populated only when status == ready
    # E044/US252 (ADR-054): 'pipeline' | 'agent' — drives the origin badge.
    origin: str = "pipeline"

    model_config = {"from_attributes": True}


class SectionOverridePatch(BaseModel):
    section: Literal["header", "recipient", "body", "signature"]
    content: str


class SectionOverridePatchResponse(BaseModel):
    cover_letter_id: uuid.UUID
    section: str
    status: str = "saved"


class CoverLetterSummaryResponse(BaseModel):
    cover_letter_id: uuid.UUID
    status: CoverLetterStatus
    template: str
    html_url: Optional[str] = None
    pdf_url: Optional[str] = None
    expires_at: datetime

    model_config = {"from_attributes": True}
