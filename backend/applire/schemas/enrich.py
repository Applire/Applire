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
from typing import Literal

from pydantic import BaseModel

from applire.schemas.session import ConfirmationPrompt


class EnrichStartRequest(BaseModel):
    # None = full profile scan
    # "work_experience:<company>:<role>" = scoped to one entry
    scope: str | None = None


class GapItem(BaseModel):
    id: str   # gap descriptor string, e.g. "achievements: Product Lead @ Beta GmbH"
    label: str
    status: Literal["pending", "active", "done", "na", "skipped"]


class EnrichStartResponse(BaseModel):
    session_id: uuid.UUID
    first_question: str
    gaps: list[GapItem]
    estimated_questions: int


class EnrichRespondRequest(BaseModel):
    answer: str


class EnrichRespondResponse(BaseModel):
    next_question: str | None
    gaps: list[GapItem]
    done: bool
    profile_updated: bool
    # The reconciler flagged an ambiguity it will not guess (US185). When set,
    # next_question carries the confirmation question and these carry the options.
    pending_confirmations: list[ConfirmationPrompt] | None = None


class EnrichActionResponse(BaseModel):
    """Returned by /skip and /na endpoints."""
    next_question: str | None
    gaps: list[GapItem]
    done: bool
