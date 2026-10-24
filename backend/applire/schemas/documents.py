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

"""My Documents — response schemas for GET /api/documents."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from applire.models.cv import CVGenerationStatus


class LetterRef(BaseModel):
    """#789 (Hazelnut build 3): the cover letter that belongs with a CV row.

    My Documents listed only CVs, so a ready letter had no home on the page
    named "Meine Dokumente". Read model, never stored: the newest live letter
    for the same posting and owner, attached to that posting's newest CV row.
    """

    cover_letter_id: uuid.UUID
    status: str
    created_at: datetime
    expires_at: datetime


class DocumentItem(BaseModel):
    #: 789:187 (Hazelnut build 4, additive): ``"cover_letter"`` for a letter
    #: whose posting has no live CV — then ``cv_id`` is ``None`` and
    #: ``cover_letter`` names the letter itself.
    kind: Literal["cv", "cover_letter"] = "cv"
    cv_id: uuid.UUID | None
    flow_id: uuid.UUID | None
    role_title: str | None
    company_name: str | None
    template: str
    status: CVGenerationStatus
    created_at: datetime
    expires_at: datetime
    # E044/US252 (ADR-054): 'pipeline' | 'agent' — drives the origin badge.
    origin: str = "pipeline"
    # #789: the letter for this posting (on its newest CV row only). Additive.
    cover_letter: LetterRef | None = None

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    items: list[DocumentItem]
    #: Rows across all pages (pagination).
    total: int
    #: 789:187 (additive): documents across all pages — a CV row that carries
    #: its letter counts two. ``None`` only from callers that predate it.
    documents_total: int | None = None
