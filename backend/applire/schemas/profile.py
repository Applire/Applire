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

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ─── Section sub-models ───────────────────────────────────────────────────────

class ProfessionalSummary(BaseModel):
    """Multilingual professional summary — user's elevator pitch."""
    de: str | None = None
    en: str | None = None


class PersonalInfo(BaseModel):
    name: str = ""
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    address: str | None = None
    nationality: str | None = None
    date_of_birth: date | None = None
    photo_url: str | None = None
    linkedin_url: str | None = None
    xing_url: str | None = None
    website_url: str | None = None

    @field_validator("date_of_birth", mode="before")
    @classmethod
    def _normalise_date(cls, v: object) -> object:
        """Accept DD.MM.YYYY (German) and DD/MM/YYYY in addition to ISO 8601."""
        if not isinstance(v, str):
            return v
        import re
        m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$", v)
        if m:
            day, month, year = m.groups()
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
        return v


# Backwards-compat alias — existing JSONB records and LLM output use 'contact'.
Contact = PersonalInfo


class WorkEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    company: str = ""
    role: str = ""

    @field_validator("company", "role", mode="before")
    @classmethod
    def coerce_company(cls, v: object) -> str:
        return v if isinstance(v, str) else ""

    @field_validator("role_aliases", "responsibilities", "achievements", "technologies", mode="before")
    @classmethod
    def coerce_list_fields(cls, v: object) -> list:
        return v if isinstance(v, list) else []

    # All role titles ever used for this position across different CVs/applications.
    # Enables the CV tailoring engine to pick the most relevant title per application
    # (e.g. "Team Lead" for leadership roles, "2nd Level Support" for technical roles).
    role_aliases: list[str] = Field(default_factory=list)
    location: str | None = None
    # str — LLM returns partial dates like "2020-01"; not valid ISO date
    start_date: str | None = None
    end_date: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    industry_context: str | None = None
    team_size: int | None = None
    budget_managed: str | None = None


class EducationEntry(BaseModel):
    institution: str
    degree: str
    field: str = ""

    @field_validator("institution", "degree", "field", mode="before")
    @classmethod
    def coerce_str_fields(cls, v: object) -> str:
        return v if isinstance(v, str) else ""

    @field_validator("relevant_coursework", mode="before")
    @classmethod
    def coerce_list_fields(cls, v: object) -> list:
        return v if isinstance(v, list) else []

    start_date: str | None = None
    end_date: str | None = None
    grade: str | None = None
    thesis_title: str | None = None
    relevant_coursework: list[str] = Field(default_factory=list)


class Certification(BaseModel):
    name: str
    issuing_organization: str | None = None
    date_obtained: date | None = None
    expiry_date: date | None = None  # None means doesn't expire
    credential_id: str | None = None
    credential_url: str | None = None


_PROFICIENCY_ALIASES: dict[str, str] = {
    # Generic aliases
    "beginner": "basic",
    "novice": "basic",
    "junior": "basic",
    "elementary": "basic",
    "proficient": "advanced",
    "senior": "advanced",
    "fluent": "advanced",
    "native": "expert",
    "expert": "expert",
    "master": "expert",
    # LinkedIn language proficiency levels
    "elementary proficiency": "basic",
    "limited working proficiency": "basic",
    "professional working proficiency": "intermediate",
    "professional working": "intermediate",
    "full professional proficiency": "advanced",
    "full professional": "advanced",
    "native or bilingual proficiency": "expert",
    "native or bilingual": "expert",
    "bilingual": "expert",
}


class Skill(BaseModel):
    name: str
    category: Literal["technical", "soft", "language", "domain"] = "technical"
    proficiency: Literal["basic", "intermediate", "advanced", "expert"] = "intermediate"
    years_experience: int | None = None
    source: str | None = None  # which role/interview surfaced this
    last_used: date | None = None
    work_entry_refs: list[str] = Field(default_factory=list)

    @field_validator("work_entry_refs", mode="before")
    @classmethod
    def coerce_work_entry_refs(cls, v: object) -> list:
        return v if isinstance(v, list) else []

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, v: object) -> object:
        if isinstance(v, str):
            lowered = v.lower()
            if lowered in {"technical", "soft", "language", "domain"}:
                return lowered
        return v

    @field_validator("proficiency", mode="before")
    @classmethod
    def normalize_proficiency(cls, v: object) -> object:
        if v is None:
            return "intermediate"
        if isinstance(v, str):
            normalized = _PROFICIENCY_ALIASES.get(v.lower())
            if normalized:
                return normalized
            # Unknown proficiency string — default to intermediate rather than
            # raising a validation error (e.g. future LinkedIn/Xing terminology).
            _valid = {"basic", "intermediate", "advanced", "expert"}
            if v.lower() not in _valid:
                return "intermediate"
            return v.lower()
        return v


class Language(BaseModel):
    language: str
    level: str | None = None


class Publication(BaseModel):
    title: str
    type: Literal["publication", "patent"] = "publication"
    co_authors: list[str] = Field(default_factory=list)
    venue: str | None = None  # journal, conference, or patent office
    published_date: date | None = None
    doi: str | None = None
    url: str | None = None
    patent_number: str | None = None


class VolunteerActivity(BaseModel):
    role: str
    organization: str
    location: str | None = None
    start_date: date | None = None
    end_date: date | None = None  # None means ongoing
    description: str | None = None
    cause: str | None = None  # e.g. "Education", "Environment"


# ─── Merge conflict model (stored in metadata, resolved by user) ──────────────

class Conflict(BaseModel):
    conflict_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    section: str
    field: str
    existing_value: Any
    incoming_value: Any
    source: str  # which CV/import caused this
    suggested_resolution: str | None = None
    resolved: bool = False


# ─── Enrichment tracking ──────────────────────────────────────────────────────

class FieldChange(BaseModel):
    section: str
    field: str
    action: Literal["added", "updated", "merged"]
    old_value: Any | None = None
    new_value: Any = None
    # ADR-040: a human-readable "why" note shown on the "what changed & why" surfaces.
    # `rationale` is an English fallback / audit string; the frontend localizes via
    # `rationale_key` (ADR-038 — the surface follows the user's UI language). Legacy
    # records without a key fall back to the stored `rationale` prose.
    rationale: str | None = None
    rationale_key: str | None = None


class EnrichmentRecord(BaseModel):
    timestamp: datetime
    source: Literal["cv_upload", "cv_paste", "linkedin_import", "xing_import", "interview", "manual_edit", "manual_role_add"]
    source_session_id: str | None = None
    changes: list[FieldChange] = Field(default_factory=list)
    confidence: float | None = None  # for LLM-extracted data


# ─── Profile metadata ─────────────────────────────────────────────────────────

class ProfileChangesResponse(BaseModel):
    """US145 / ADR-040 — the "what changed & why" surface data contract.

    Sourced from the Master Profile only (decision trail + flagged conflicts), never
    from the source uploads, which hard-delete after 7 days (ADR-005).
    """
    enrichment_history: list["EnrichmentRecord"] = Field(default_factory=list)
    pending_conflicts: list["Conflict"] = Field(default_factory=list)


class ProfileMetadata(BaseModel):
    completeness_score: float = 0.0  # 0.0 to 1.0
    created_via: Literal["cv_upload", "cv_paste", "linkedin_import", "xing_import", "interview", "manual"] = "manual"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    application_count: int = 0
    enrichment_history: list[EnrichmentRecord] = Field(default_factory=list)
    pending_conflicts: list[Conflict] = Field(default_factory=list)


# ─── Completeness calculation ─────────────────────────────────────────────────

_COMPLETENESS_WEIGHTS: dict[str, float] = {
    "work_experience": 0.30,
    "education": 0.20,
    "skills": 0.20,
    "personal_info": 0.15,
    "languages": 0.10,
    "professional_summary": 0.03,
    "certifications": 0.01,
    "publications": 0.005,
    "volunteer_activities": 0.005,
}


def _has_meaningful_data(profile: "MasterProfileData", section: str) -> bool:
    value = getattr(profile, section, None)
    if value is None:
        return False
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, PersonalInfo):
        return bool(value.name or value.email)
    if isinstance(value, ProfessionalSummary):
        return bool(value.de or value.en)
    return bool(value)


# ─── Master profile data ──────────────────────────────────────────────────────

class MasterProfileData(BaseModel):
    personal_info: PersonalInfo = Field(default_factory=PersonalInfo)
    professional_summary: ProfessionalSummary = Field(default_factory=ProfessionalSummary)
    work_experience: list[WorkEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    publications: list[Publication] = Field(default_factory=list)
    volunteer_activities: list[VolunteerActivity] = Field(default_factory=list)
    metadata: ProfileMetadata | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_fields(cls, data: Any) -> Any:
        """Migrate old JSONB keys → new field names so existing DB records load cleanly."""
        if not isinstance(data, dict):
            return data

        # work_history → work_experience; bullets → responsibilities
        if "work_history" in data and "work_experience" not in data:
            migrated = []
            for e in data.pop("work_history"):
                entry = dict(e)
                if "bullets" in entry and "responsibilities" not in entry:
                    entry["responsibilities"] = entry.pop("bullets")
                migrated.append(entry)
            data["work_experience"] = migrated

        # contact → personal_info; linkedin → linkedin_url
        if "contact" in data and "personal_info" not in data:
            c = dict(data.pop("contact"))
            if "linkedin" in c and "linkedin_url" not in c:
                c["linkedin_url"] = c.pop("linkedin")
            data["personal_info"] = c

        # skills: list[str] → list[Skill]
        if "skills" in data and isinstance(data["skills"], list):
            skills = []
            for s in data["skills"]:
                if isinstance(s, str):
                    skills.append({"name": s, "category": "technical", "proficiency": "intermediate"})
                else:
                    skills.append(s)
            data["skills"] = skills

        return data

    def calculate_completeness(self) -> float:
        score = 0.0
        for section, weight in _COMPLETENESS_WEIGHTS.items():
            if _has_meaningful_data(self, section):
                score += weight
        return round(score, 2)

    def calculate_stats(self) -> "ProfileStats":
        """Derive the gap-page summary tiles from real profile data.

        Replaces the hard-coded persona example numbers (5 / 12 / 3 / 47).
        - positions:      number of work-experience entries
        - projects:       total achievements across all roles (concrete wins)
        - certifications: number of certifications
        - data_points:    every atomic fact held in the profile
        """
        positions = len(self.work_experience)
        projects = sum(len(w.achievements) for w in self.work_experience)
        certifications = len(self.certifications)

        data_points = (
            positions
            + sum(len(w.responsibilities) for w in self.work_experience)
            + projects
            + sum(len(w.technologies) for w in self.work_experience)
            + len(self.skills)
            + len(self.education)
            + sum(len(e.relevant_coursework) for e in self.education)
            + certifications
            + len(self.languages)
            + len(self.publications)
            + len(self.volunteer_activities)
        )

        return ProfileStats(
            positions=positions,
            projects=projects,
            certifications=certifications,
            data_points=data_points,
        )


# ─── API response models ──────────────────────────────────────────────────────

class ProfileStats(BaseModel):
    """Summary tile counts for the gap page, derived from real profile data."""
    positions: int = 0
    projects: int = 0
    certifications: int = 0
    data_points: int = 0


class MasterProfileResponse(BaseModel):
    id: uuid.UUID
    profile: MasterProfileData
    completeness: float  # 0.0 to 1.0
    stats: ProfileStats = Field(default_factory=ProfileStats)
    merge_conflicts: list[Conflict] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class LinkedInImportRequest(BaseModel):
    linkedin_json: dict


class ConflictResolutionRequest(BaseModel):
    """
    Payload for POST /api/profile/conflicts/{conflict_id}/resolve.

    resolution:
        "existing" — keep the existing value as-is
        "incoming" — replace with the incoming value
        "manual"   — supply a custom value via `value`
    """
    resolution: Literal["existing", "incoming", "manual"]
    value: Any | None = None


class ConflictSummary(BaseModel):
    """Lightweight conflict summary for CVUploadResponse (avoids leaking full values)."""
    conflict_id: str
    section: str
    field: str
    source: str


class CVUploadResponse(BaseModel):
    """Response for POST /api/profile/upload (ADR 014).

    Distinct from MasterProfileResponse — provides upload-specific feedback:
    transient DRAFT/COMPLETE status, GDPR expiry, and traceability back to
    the EnrichmentRecord. MasterProfileResponse remains unchanged.
    """
    profile_id: uuid.UUID
    status: Literal["DRAFT", "COMPLETE"]
    completeness_score: float
    conflicts: list[ConflictSummary] = Field(default_factory=list)
    enrichment_record_id: uuid.UUID
    expires_at: datetime


class UploadHistoryItem(BaseModel):
    id: uuid.UUID
    original_filename: str
    mime_type: str
    byte_size: int
    created_at: datetime
    completeness_score: float | None = None  # TODO: join to EnrichmentRecord when score persistence is wired
