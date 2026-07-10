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
from typing import Optional
from urllib.parse import urlparse

from pydantic import BaseModel, field_validator, model_validator

from applire.schemas.application import DuplicateOfHint


def _coerce_to_list(v: object) -> list[str]:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [item.strip() for item in v.split(",") if item.strip()]
    return []


class JobAnalyzeRequest(BaseModel):
    text: Optional[str] = None
    url: Optional[str] = None

    @model_validator(mode="after")
    def check_exactly_one(self) -> "JobAnalyzeRequest":
        has_text = bool(self.text and self.text.strip())
        has_url = bool(self.url and self.url.strip())
        if has_text == has_url:
            raise ValueError("Provide exactly one of 'text' or 'url'.")
        if has_url:
            parsed = urlparse(self.url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("'url' must be a valid http or https URL.")
        return self


class JobAnalysisResponse(BaseModel):
    id: uuid.UUID
    role_title: str
    required_skills: list[str]
    nice_to_have_skills: list[str]
    keywords: list[str]
    seniority_level: str
    company_culture_signals: list[str]
    language_requirement: str
    company_name: Optional[str] = None
    # KldB 2020 (BA-Klassifikation der Berufe 2020) — nullable for pre-migration rows
    berufsbild_code: Optional[str] = None
    berufsbild_label: Optional[str] = None
    raw_text_hash: str
    source_url: Optional[str] = None
    # E039/US220 (journey Branch F): set when this JD matches one of the user's
    # existing applications — a repost recognition hint, never a block. Enriched
    # by the caller (router / MCP tool), not by analyze_jd itself: the service
    # stays user-agnostic like the job_analyses table it manages.
    duplicate_of: Optional[DuplicateOfHint] = None

    model_config = {"from_attributes": True}

    @field_validator("required_skills", "nice_to_have_skills", "keywords", "company_culture_signals", mode="before")
    @classmethod
    def coerce_list_fields(cls, v: object) -> list[str]:
        return _coerce_to_list(v)
