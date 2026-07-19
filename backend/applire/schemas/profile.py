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

    @field_validator("name", mode="before")
    @classmethod
    def _coerce_name(cls, v: object) -> str:
        # The LLM returns explicit null for a nameless document (e.g. a JD uploaded
        # as a CV). Coerce None → "" so extraction doesn't crash with a Pydantic
        # error — the document-type warning (US154/FMEA 2.3) then handles it.
        return v if isinstance(v, str) else ""
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


class ExperienceBase(BaseModel):
    """Shared capability set for any kind of engagement (ADR-044).

    Jobs, projects, and volunteering all carry a time span, applied
    skills/technologies, and achievements — orthogonal to the *kind* of
    engagement. WorkEntry, ProjectEntry, and VolunteerActivity extend this.
    The three remain separate section-mapped lists on the profile (we do NOT
    collapse them into one kind-discriminated list).
    """

    role: str = ""
    location: str | None = None
    # str — LLM returns partial dates like "2020-01"; not valid ISO date
    start_date: str | None = None
    end_date: str | None = None
    # #155 — explicit current/ongoing marker, tri-state: None = unknown,
    # True = current position (end_date stays null by convention),
    # False = known ended. Additive JSONB field — legacy rows load as None.
    is_current: bool | None = None
    responsibilities: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)

    # ADR-041 (amended 2026-06-24): role-aware completeness. LLM-judged at write
    # time (services/profile/expectations.py); None = never annotated → scorer
    # falls back to the floor (under-ask). Stores only the role-conditional fields.
    expected_fields: list[str] | None = None

    @field_validator("responsibilities", "achievements", "technologies", mode="before")
    @classmethod
    def coerce_experience_list_fields(cls, v: object) -> list:
        return v if isinstance(v, list) else []

    @field_validator("expected_fields", mode="before")
    @classmethod
    def coerce_expected_fields(cls, v: object) -> object:
        return v if (v is None or isinstance(v, list)) else None

    def org_label(self) -> str:
        """Human label for the engagement's "where" — subclasses override."""
        return ""


class WorkEntry(ExperienceBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    company: str = ""

    @field_validator("company", "role", mode="before")
    @classmethod
    def coerce_company(cls, v: object) -> str:
        return v if isinstance(v, str) else ""

    @field_validator("role_aliases", mode="before")
    @classmethod
    def coerce_role_aliases(cls, v: object) -> list:
        return v if isinstance(v, list) else []

    # All role titles ever used for this position across different CVs/applications.
    # Enables the CV tailoring engine to pick the most relevant title per application
    # (e.g. "Team Lead" for leadership roles, "2nd Level Support" for technical roles).
    role_aliases: list[str] = Field(default_factory=list)
    industry_context: str | None = None
    team_size: int | None = None
    budget_managed: str | None = None

    def org_label(self) -> str:
        return self.company


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


def _coerce_partial_date(v: Any) -> Any:
    """Expand a partial date string to a full ``date`` (issue #70).

    Real CVs / LinkedIn list dates as 'YYYY' or 'YYYY-MM'; the strict date
    parser rejects them and aborts the whole import. Coerce rather than
    raise — fall back to None only if genuinely unparseable. Shared by
    ``Certification`` and ``Publication`` (#177).
    """
    if v is None or isinstance(v, date):
        return v
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        for suffix in ("", "-01", "-01-01"):
            try:
                return datetime.strptime(s + suffix, "%Y-%m-%d").date()
            except ValueError:
                continue
        for fmt in ("%d.%m.%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None
    return v


class Certification(BaseModel):
    name: str
    issuing_organization: str | None = None
    date_obtained: date | None = None
    expiry_date: date | None = None  # None means doesn't expire
    credential_id: str | None = None
    credential_url: str | None = None

    @field_validator("date_obtained", "expiry_date", mode="before")
    @classmethod
    def _coerce_cert_dates(cls, v: Any) -> Any:
        return _coerce_partial_date(v)


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
    # Provenance: ids/labels of the experiences (work, project, volunteer) that
    # surfaced this skill. Renamed from work_entry_refs (US172 / ADR-044) now
    # that experiences are unified; legacy JSONB with the old key still loads.
    experience_refs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_fields(cls, data: Any) -> Any:
        """Map the legacy JSONB key work_entry_refs → experience_refs."""
        if not isinstance(data, dict):
            return data
        if "work_entry_refs" in data and "experience_refs" not in data:
            data["experience_refs"] = data.pop("work_entry_refs")
        return data

    @field_validator("experience_refs", mode="before")
    @classmethod
    def coerce_experience_refs(cls, v: object) -> list:
        return v if isinstance(v, list) else []


    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, v: object) -> object:
        if isinstance(v, str):
            lowered = v.lower()
            if lowered in {"technical", "soft", "language", "domain"}:
                return lowered
            # Unknown category string — default to technical rather than raising
            # (e.g. an LLM/import emitting a free-text category like "Cloud
            # Platforms"); mirrors normalize_proficiency's robustness.
            return "technical"
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


class SignatureStory(BaseModel):
    """A signature story (ADR-055): the narrative evidence unit the schema used
    to flatten into bullets and tags — challenge → mechanism → outcome →
    benchmark. Job-agnostic, reusable across a campaign; anchored to the
    experience it happened in via ``experience_refs`` (the Skill provenance
    pattern). Stories carry NO date span of their own — the referenced
    experience owns time (ADR-044 boundary)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    challenge: str
    mechanism: str
    outcome: str  # the measurable result — figures live here (Oracle provenance)
    benchmark: str | None = None
    experience_refs: list[str] = Field(default_factory=list)
    source: str | None = None  # which interview/edit surfaced this

    @field_validator("experience_refs", mode="before")
    @classmethod
    def coerce_experience_refs(cls, v: object) -> list:
        return v if isinstance(v, list) else []


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

    @field_validator("published_date", mode="before")
    @classmethod
    def _coerce_partial_pub_date(cls, v: Any) -> Any:
        return _coerce_partial_date(v)


class VolunteerActivity(ExperienceBase):
    # id added (ADR-044 conformance, ADR-046): ProjectEntry.associated_experience
    # may hang a project off a volunteer role, but that needs a stable volunteer id
    # to point at. Mirrors WorkEntry/ProjectEntry; additive JSONB, no migration.
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organization: str = ""
    # start_date/end_date inherited from ExperienceBase as str | None (ADR-044
    # refinement): JSONB stores ISO strings, so legacy `date` values load fine.
    description: str | None = None
    cause: str | None = None  # e.g. "Education", "Environment"

    def org_label(self) -> str:
        return self.organization


class ProjectEntry(ExperienceBase):
    """A project — standalone or associated with a job/volunteer engagement (ADR-044)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str | None = None
    url: str | None = None
    # Optional id/label linking to a work OR volunteer entry; None = standalone.
    associated_experience: str | None = None

    def org_label(self) -> str:
        return self.name


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
    # Stable id so a pre-merge snapshot (US168 / ADR-042) can key to the merge
    # this record represents, and undo can detect whether it is still the head.
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime
    source: Literal["cv_upload", "cv_paste", "linkedin_import", "xing_import", "interview", "manual_edit", "manual_role_add"]
    source_session_id: str | None = None
    changes: list[FieldChange] = Field(default_factory=list)
    confidence: float | None = None  # for LLM-extracted data
    # US161 (ADR-041 amended) — per-entity {extracted, stored, delta} captured at
    # merge time so silent data-loss (FMEA JF-M-3.3) is detectable. Merge records only.
    reconciliation: dict[str, dict[str, int]] | None = None


# ─── Profile metadata ─────────────────────────────────────────────────────────

class ProfileChangesResponse(BaseModel):
    """US145 / ADR-040 — the "what changed & why" surface data contract.

    Sourced from the Master Profile only (decision trail + flagged conflicts), never
    from the source uploads, which hard-delete after 7 days (ADR-005).
    """
    enrichment_history: list["EnrichmentRecord"] = Field(default_factory=list)
    pending_conflicts: list["Conflict"] = Field(default_factory=list)


class PendingConfirmation(BaseModel):
    """E037 PQ #4 — an import-time reconciler ambiguity (a ``RequestConfirmation``)
    persisted so the user can answer it later in the profile-review interview.

    Unlike a ``Conflict`` (a 2-value existing-vs-incoming dispute), a confirmation
    carries a free-text ``question`` plus an N-option ``options`` list, each option
    a distinct one-tap answer. Folding one into a ``Conflict`` garbled the dialog
    (empty section, the question swallowed into ``field``, options comma-joined
    into ``incoming_value``); it gets its own shape + channel instead.
    """
    confirmation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question: str
    options: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)
    source: str = ""
    resolved: bool = False
    chosen_option: str | None = None


class ProfileMetadata(BaseModel):
    completeness_score: float = 0.0  # 0.0 to 1.0
    created_via: Literal["cv_upload", "cv_paste", "linkedin_import", "xing_import", "interview", "manual"] = "manual"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    application_count: int = 0
    enrichment_history: list[EnrichmentRecord] = Field(default_factory=list)
    pending_conflicts: list[Conflict] = Field(default_factory=list)
    # E037 PQ #4 — N-option reconciler ambiguities awaiting a user choice. Kept
    # distinct from pending_conflicts (a Conflict cannot represent an N-option ask).
    pending_confirmations: list[PendingConfirmation] = Field(default_factory=list)


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
    projects: list[ProjectEntry] = Field(default_factory=list)
    volunteer_activities: list[VolunteerActivity] = Field(default_factory=list)
    # ADR-055 — signature stories: narrative evidence, reconciler-written
    # (upsert_story), Oracle-indexed. Defaulted list = legacy JSONB loads clean.
    signature_stories: list[SignatureStory] = Field(default_factory=list)
    metadata: ProfileMetadata | None = None

    @property
    def all_experiences(self) -> list[ExperienceBase]:
        """All engagements (work, projects, volunteering) as a flat list (ADR-044).

        Order is stable: work experience, then projects, then volunteering. The
        three remain distinct section-mapped lists; this is a read accessor only.
        """
        return [*self.work_experience, *self.projects, *self.volunteer_activities]

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
        from applire.services.profile.completeness import work_experience_richness
        score = 0.0
        for section, weight in _COMPLETENESS_WEIGHTS.items():
            if section == "work_experience":
                score += weight * work_experience_richness(
                    [e.model_dump() for e in self.work_experience])
            elif _has_meaningful_data(self, section):
                score += weight
        return round(score, 2)

    def completeness_gaps(self) -> list[str]:
        """Weighted sections that still lack meaningful data (US104 / E026).

        The flip side of ``calculate_completeness`` — the sections whose absence
        docks the score. Deterministic and JD-independent; the health endpoint
        surfaces these as a nudge, never as a severity-tagged issue.
        """
        return [
            section
            for section in _COMPLETENESS_WEIGHTS
            if not _has_meaningful_data(self, section)
        ]

    def calculate_stats(self) -> "ProfileStats":
        """Derive the gap-page summary tiles from real profile data.

        Replaces the hard-coded persona example numbers (5 / 12 / 3 / 47).
        - positions:      number of work-experience entries
        - projects:       number of real ProjectEntry items (US172 / ADR-044)
        - certifications: number of certifications
        - data_points:    every atomic fact held in the profile

        data_points is stable for legacy profiles (projects == [] and no
        volunteer achievements/technologies): the old ``projects`` variable
        was Σ work achievements, so we keep that term explicit below.
        New atomic facts from ProjectEntry and volunteer achievements/technologies
        are additive and zero for legacy data.
        """
        positions = len(self.work_experience)
        projects = len(self.projects)  # real project entries, not work achievements
        certifications = len(self.certifications)

        data_points = (
            # work experience section (unchanged from legacy formula)
            positions
            + sum(len(w.responsibilities) for w in self.work_experience)
            + sum(len(w.achievements) for w in self.work_experience)  # was the misnomer `projects`
            + sum(len(w.technologies) for w in self.work_experience)
            # project entries (new; zero for legacy profiles)
            + projects
            + sum(len(p.responsibilities) for p in self.projects)
            + sum(len(p.achievements) for p in self.projects)
            + sum(len(p.technologies) for p in self.projects)
            # volunteer activities (count was already included; responsibilities/achievements/tech new)
            + len(self.volunteer_activities)
            + sum(len(v.responsibilities) for v in self.volunteer_activities)
            + sum(len(v.achievements) for v in self.volunteer_activities)
            + sum(len(v.technologies) for v in self.volunteer_activities)
            # other sections
            + len(self.skills)
            + len(self.education)
            + sum(len(e.relevant_coursework) for e in self.education)
            + certifications
            + len(self.languages)
            + len(self.publications)
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
    # GATED (US167): the pre-merge gate held the merge. profile_id /
    # enrichment_record_id are absent because nothing was committed.
    profile_id: uuid.UUID | None = None
    status: Literal["DRAFT", "COMPLETE", "GATED"]
    completeness_score: float
    conflicts: list[ConflictSummary] = Field(default_factory=list)
    enrichment_record_id: uuid.UUID | None = None
    expires_at: datetime
    # Input-plausibility signals (Input Integrity sprint, issue #43)
    looks_like_cv: bool = True          # US154 / FMEA 2.3
    name_mismatch: bool = False         # US155 / FMEA 2.4 (vs existing profile name)
    undated_positions: int = 0          # US157 / FMEA 2.7
    # Pre-merge gate (US167 / ADR-041 amended). "none" on a clean merge.
    gate: Literal["none", "not_a_cv", "name_divergence"] = "none"
    account_name: str | None = None     # existing profile's name (divergence prompt)
    cv_name: str | None = None          # uploaded CV's name (divergence prompt)
    staged_id: uuid.UUID | None = None  # parked upload row to resolve (merge/discard)


_IMPORT_STATUS = Literal["pending", "processing", "ready", "failed", "expired"]


class CVImportJobResponse(BaseModel):
    """Response for POST /api/profile/import-jobs — the async-import handle.

    The upload returns immediately; the heavy segmented extraction + reconcile runs in a
    background task (so a slow/output-capped model can't 504 the request and drop the CV).
    Poll GET /api/profile/import-jobs/{import_id} until status is ``ready`` or ``failed``.
    """

    import_id: uuid.UUID
    status: _IMPORT_STATUS


class CVImportStatusResponse(BaseModel):
    """Response for GET /api/profile/import-jobs/{import_id} (async-import poll).

    ``result`` carries the same CVUploadResponse the synchronous /upload would have
    returned, populated when status == ``ready``. On ``failed``, ``error_code`` is a
    stable machine code (llm_truncated / llm_timeout / invalid_document / …) the frontend
    localizes — the raw provider text is never surfaced.
    """

    import_id: uuid.UUID
    status: _IMPORT_STATUS
    error_code: str | None = None
    result: CVUploadResponse | None = None


class CVImportJobListItem(BaseModel):
    """One row of GET /api/profile/import-jobs (PQ F1 — truthful dashboard).

    Lightweight, user-scoped listing so the frontend can tell that imports are still
    running server-side (e.g. after a refresh interrupted the onboarding overlay) and
    show an "import in progress" indicator instead of presenting a half-imported
    profile as complete. No ``result`` payload — poll GET /import-jobs/{id} for that.
    """

    import_id: uuid.UUID
    status: _IMPORT_STATUS
    filename: str
    created_at: datetime


class StagedResolveRequest(BaseModel):
    """Request body for POST /api/profile/staged/{id}/resolve (US167)."""

    action: Literal["merge", "discard"]


class StagedResolveResponse(BaseModel):
    """Response for POST /api/profile/staged/{id}/resolve (US167 / ADR-041 amended)."""

    staged_id: uuid.UUID
    action: Literal["merge", "discard"]
    profile_id: uuid.UUID | None = None         # set when action == "merge"
    completeness_score: float | None = None     # set when action == "merge"
    conflicts: list[ConflictSummary] = Field(default_factory=list)


class UndoLastMergeResponse(BaseModel):
    """Response for POST /api/profile/undo-last-merge (US168 / ADR-042)."""

    restored: bool                          # False when there was nothing to undo
    discarded_later_edits: bool = False     # True when edits after the merge were dropped


class HealthIssue(BaseModel):
    """One Tier-2 profile-health issue (US160 / ADR-041 amended).

    ``profile_mismatch_severity`` is the US162 axis (info|review|critical), kept
    deliberately distinct from the ADR-021 *reviewer* severity. The literal is
    inlined (not imported from ``services.profile.severity``) to avoid a schema↔
    service import cycle.
    """

    id: str                                       # stable, deterministic
    thread: Literal["conflict", "accuracy"]
    profile_mismatch_severity: Literal["info", "review", "critical"]
    summary: str
    field_ref: str | None = None
    source_record_ref: str | None = None


class CompletenessBlock(BaseModel):
    """Completeness is a score + the missing sections — never severity-tagged
    (ADR-041 amended): an incomplete profile is a nudge, not a mismatch.

    ``gaps``       — section-level names (e.g. ``["education", "languages"]``);
                     rendered by the frontend as "Missing sections: X, Y".
    ``field_gaps`` — role-aware field-level gap strings (e.g.
                     ``["end_date: Junior Dev @ Acme"]``); populated by
                     ``completeness.field_gaps()`` — the same function the
                     no-JD enrichment interview (Mode C) uses, so the hub's
                     count agrees with the number of questions asked (US179).
    """

    score: float                                  # 0.0 to 1.0
    gaps: list[str] = Field(default_factory=list)
    field_gaps: list[str] = Field(default_factory=list)


class ProfileHealthResponse(BaseModel):
    """GET /api/profile/health — one deterministic read of profile health."""

    issues: list[HealthIssue] = Field(default_factory=list)
    completeness: CompletenessBlock


class UploadHistoryItem(BaseModel):
    id: uuid.UUID
    original_filename: str
    mime_type: str
    byte_size: int
    created_at: datetime
    completeness_score: float | None = None  # TODO: join to EnrichmentRecord when score persistence is wired
    # Pre-merge gate (US167). Open states (not_a_cv / name_divergence) badge the
    # row as "needs resolution"; resolved_* / None are inert. staged_name is the
    # parked CV's extracted name, shown when re-opening the resolve dialog.
    gate_status: str | None = None
    staged_name: str | None = None
