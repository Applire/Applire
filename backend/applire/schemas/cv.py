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

from pydantic import BaseModel, Field, field_validator

from applire.constants import MAX_TARGET_PAGES
from applire.models.cv import CVGenerationStatus
from applire.schemas.profile import FieldChange

# E044 (ADR-054): version tag of the public tailored-CV content contract served
# via the MCP resource schema://cv. Bump on any breaking field change.
CV_SCHEMA_VERSION = "cv/1"

CVTemplate = Literal[
    "classic_german",
    "modern_swiss",
    "executive",
    "tech_developer",
    "creative_sidebar",
    "academic",
    "compact_pro",
]


class CVGenerateRequest(BaseModel):
    job_id: uuid.UUID
    template: CVTemplate = "classic_german"
    # E042/US236 (ADR-051 §1): optional per-generation page-count override.
    # None = fall back to the user's UserSettings.target_cv_pages, then the
    # region standard (resolve_target_pages()). #379: upper-bounded — an
    # unbounded override fed straight into the per-role bullet-budget math
    # and produced inert "max 1002 bullet(s)" ceilings.
    target_pages: int | None = Field(default=None, ge=1, le=MAX_TARGET_PAGES)


class CVProfileDiffResponse(BaseModel):
    """US147 / ADR-040 — deterministic divergences of a generated CV from the Master
    Profile, for the pre-download review surface. `grounded` is True when nothing diverges."""
    items: list[FieldChange] = []
    grounded: bool = True


class CVGenerateResponse(BaseModel):
    """Returned immediately by POST /api/cv/generate (async path)."""
    cv_id: uuid.UUID
    status: CVGenerationStatus
    html_url: str  # stable URL — usable once status='ready'
    pdf_url: str
    expires_at: datetime


class CVStatusResponse(BaseModel):
    """Returned by GET /api/cv/{cv_id}/status."""
    cv_id: uuid.UUID
    status: CVGenerationStatus
    html_url: Optional[str] = None
    pdf_url: Optional[str] = None
    # Stable machine code for a failed generation (ADR-047 §4 / PQ F6). The frontend maps
    # it to a localized human message + retry; the raw LLM exception text is never sent.
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    expires_at: datetime
    template: Optional[str] = None      # E041/US232: version identity in the dossier documents zone
    created_at: Optional[datetime] = None
    # E042/US236 (ADR-051 §1, whole-branch review Finding 2): the target page
    # count this CV was rendered at (None = region standard / no override).
    # Lets the header re-tailor button forward the newest READY CV's target
    # even on non-stale applications, where stale_cv is absent.
    target_pages: Optional[int] = None
    # E044/US252 (ADR-054): 'pipeline' | 'agent' — agent-rendered documents are
    # never presented as Applire-authored (origin badge in the UI).
    origin: str = "pipeline"

    model_config = {"from_attributes": True}


def _coerce_none_str(cls, v):
    """Coerce None to empty string for CV tailoring string fields."""
    return v if v is not None else ""


class TailoredProjectEntry(BaseModel):
    """A tailored project (US187). Rendered under its parent position in the CV
    when nested on a TailoredWorkEntry, or in a standalone section when it has no
    associated work/volunteer parent. Name + bullets at minimum — the heading and
    description carry the project; bullets carry tailored responsibilities/achievements."""
    name: str = ""
    bullets: list[str] = []

    _coerce_fields = field_validator("name", mode="before")(_coerce_none_str)


class TailoredWorkEntry(BaseModel):
    # Stable key carried from the source WorkEntry.id so the deterministic
    # project-nesting step (services/cv._nest_projects, US187) can match a tailored
    # entry back to its source. Empty for legacy records / mock fixtures — nesting
    # then matches on company+role instead.
    id: str = ""
    company: str = ""
    role: str = ""
    start_date: str = ""
    end_date: str | None = None
    bullets: list[str] = []
    # Projects associated with this position (US187 / ADR-044). Populated by the
    # deterministic post-tailoring nesting step, never by the LLM directly.
    projects: list[TailoredProjectEntry] = []
    # #328 (ADR-062 clause 1 — a fact, not prose): quantified role facts carried
    # verbatim from the vault WorkEntry by services.cv._apply_role_facts, AFTER
    # the LLM tailoring step(s) — the writer's schema never mentions these
    # fields, so it can never mint or invent them. Rendered as deterministic
    # document furniture (a per-role sub-header line), never composed into a
    # sentence. None means "not stated" — 0 is a valid team_size.
    team_size: int | None = None
    budget_managed: str | None = None
    industry_context: str | None = None

    _coerce_fields = field_validator("company", "role", "start_date", mode="before")(_coerce_none_str)
    _coerce_id = field_validator("id", mode="before")(_coerce_none_str)


class TailoredEducationEntry(BaseModel):
    institution: str = ""
    degree: str = ""
    field: str = ""
    start_date: str = ""
    end_date: str | None = None

    _coerce_fields = field_validator("institution", "degree", "field", "start_date", mode="before")(_coerce_none_str)


class TailoredLanguage(BaseModel):
    language: str = ""
    level: str = ""

    _coerce_fields = field_validator("language", "level", mode="before")(_coerce_none_str)


class TailoredCertification(BaseModel):
    """A certification carried verbatim from the Master Profile (PQ F7 / ADR-040).

    Certifications are FACTUAL data, like contact info — copied deterministically
    into ``tailored_data.certifications`` by ``services.cv._apply_certifications``
    AFTER the LLM step(s), never routed through an LLM JSON schema. Dates are kept
    as strings (rendered, not computed on) so the profile's ISO date is passed
    through unmodified rather than re-parsed.
    """
    name: str = ""
    issuing_organization: str = ""
    date_obtained: str = ""
    expiry_date: str = ""

    _coerce_fields = field_validator(
        "name", "issuing_organization", "date_obtained", "expiry_date", mode="before"
    )(_coerce_none_str)


class TailoredContact(BaseModel):
    name: str = ""
    email: str | None = None
    phone: str | None = None
    location: str = ""
    linkedin: str | None = None
    photo_url: str | None = None  # ADR-021; file path resolved to base64 URI at render time

    _coerce_fields = field_validator("name", "location", mode="before")(_coerce_none_str)


class TailoredCVData(BaseModel):
    contact: TailoredContact
    summary: str = ""
    work_history: list[TailoredWorkEntry] = []
    skills: list[str] = []
    education: list[TailoredEducationEntry] = []
    languages: list[TailoredLanguage] = []
    # Standalone projects — those with no associated work/volunteer parent (US187).
    # Projects parented to a position are nested on the relevant TailoredWorkEntry.
    projects: list[TailoredProjectEntry] = []
    # Copied verbatim from the Master Profile by services.cv._apply_certifications,
    # never LLM-generated (PQ F7 / ADR-040 truthfulness).
    certifications: list[TailoredCertification] = []
    show_photo: bool = True  # country-aware photo rendering hook (ADR-021); True for all DACH jobs

    # The LLM occasionally returns an explicit null summary; degrade to an
    # empty section instead of failing the whole CV generation.
    _coerce_fields = field_validator("summary", mode="before")(_coerce_none_str)


class GeneratedCVResponse(BaseModel):
    id: uuid.UUID
    job_analysis_id: uuid.UUID
    profile_id: uuid.UUID
    created_at: datetime
    expires_at: datetime

    model_config = {"from_attributes": True}
