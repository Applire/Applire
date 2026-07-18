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

from pydantic import BaseModel

from applire.models.cv import CVGenerationStatus


class DocumentItem(BaseModel):
    cv_id: uuid.UUID
    flow_id: uuid.UUID | None
    role_title: str | None
    company_name: str | None
    template: str
    status: CVGenerationStatus
    created_at: datetime
    expires_at: datetime
    # E044/US252 (ADR-054): 'pipeline' | 'agent' — drives the origin badge.
    origin: str = "pipeline"

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    items: list[DocumentItem]
    total: int
