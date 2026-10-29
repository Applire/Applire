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

import unicodedata
import uuid
import re
from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_serializer,
    model_validator,
)


# ─── Alternate names (ADR-046 amended 2026-10-07, #709 #716) ──────────────────
#
# The other names an entry is known by, recorded from a binding the reconciler
# made on an import (the incoming document's own wording, never a translation
# the code produced) or from a targeted engagement upsert, and removable by the
# candidate. Every list is OMITTED from a dump while empty, so a profile without
# aliases serialises byte-identically to one persisted before these fields
# existed — the reconcile prompt, every writer prompt and the #688 model-matrix
# goldens see no new key until an alias exists. `WorkEntry.role_aliases` predates
# this and keeps its always-present form (the goldens carry it).


def _coerce_alias_list(v: object) -> list:
    if not isinstance(v, list):
        return []
    return [a for a in v if isinstance(a, str) and a.strip()]


def _omit_empty_alias_lists(data: Any, fields: tuple[str, ...]) -> Any:
    if isinstance(data, dict):
        for name in fields:
            if name in data and not data[name]:
                del data[name]
    return data


class OrgFigureDecision(BaseModel):
    """FIG-2 (founder ruling, Hazelnut build 5) — the candidate's decision on one
    organisation-scale figure (company revenue, headcount, market size,
    funding) that a bullet of this entry carries: does it belong in the
    documents? ``include=False``: a writer never states the figure on this
    entry, in any wording. ``include=True``: a writer states it only together
    with ``relation``, the candidate's own words, stored verbatim (never
    paraphrased or translated). ``text`` is the bullet that carried it;
    ``key`` the figure's canonical key (``org_figure.figure_key``)."""

    figure: str
    include: bool
    relation: str | None = None
    text: str
    key: str


# ─── Section sub-models ───────────────────────────────────────────────────────

class ProfessionalSummary(BaseModel):
    """Multilingual professional summary — user's elevator pitch."""
    de: str | None = None
    en: str | None = None


class RoleFactProjection(BaseModel):
    """#328 (PO decision 2026-08-07, option 4) / #382 — the provenance of ONE
    derived quantified role fact.

    Option 4's doctrine: the candidate's own responsibility/achievement bullet
    is where a stated figure LIVES; ``team_size`` / ``budget_managed`` /
    ``industry_context`` are queryable **projections** of it. This model is what
    makes that claim inspectable rather than aspirational — it records which
    bullet the typed value was reconciled against, and, for #382, the unit the
    bullet carries and the typed field's bare number had lost.

    ``provenance`` is deliberately two-valued and never says ``attested``:

    * ``derived`` — the entry's own prose states this figure (a FACT under
      ADR-062 clause 1, settled by ``matchers.figures.extract_figures``).
    * ``uncorroborated`` — the typed value is real testimony (an interview
      answer is not less true for going unrepeated) but no bullet of this entry
      states it, so nothing here may be presented as the candidate's wording.

    ADR-070's ``attested`` facet ({entry, quote, unit}) is a different thing
    with a deliberately identical-looking shape: it is MODEL-cited and
    fail-closed verified, and it is the only quote channel that may lift a
    scope row. A projection is code-derived and must never enter ``bar.attested``
    (``test_role_facts_projection.py`` pins that boundary in both directions).
    """

    value: str
    # #382: the unit/currency the corroborating bullet states ("€", "EUR") —
    # None when the corroboration carries none (a bare headcount) or when the
    # value is uncorroborated. NEVER invented.
    unit: str | None = None
    # The entry's own bullet, verbatim, that states the figure. None ⇒ nothing
    # corroborates the typed value.
    quote: str | None = None
    provenance: Literal["derived", "uncorroborated"] = "uncorroborated"


class PhotoCrop(BaseModel):
    """#781 (founder ruling F-2, 2026-10-16) — how the stored photo is framed.

    The ONE stored original stays exactly as uploaded; this is only the
    framing the candidate chose, normalised to that image so it is
    resolution-independent and re-adjusting is always lossless: the centre of
    the 4:5 crop as fractions of the image's width/height, and the zoom (1 =
    the largest 4:5 rectangle the photo holds, up to 4). Applied at render by
    `services.photo.frame_photo`, the one helper every CV template and the
    DOCX export read their photo bytes through; the browser adjuster
    (`frontend/lib/photo-crop.ts`) computes the same rectangle.
    """

    model_config = ConfigDict(extra="ignore")

    cx: float = Field(ge=0.0, le=1.0)
    cy: float = Field(ge=0.0, le=1.0)
    zoom: float = Field(ge=1.0, le=4.0)


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
    # #781 (ruling F-2) — the framing of the photo at `photo_url`. Owned by the
    # photo endpoints like `photo_url` (USER_MANAGED_PERSONAL_INFO_FIELDS), reset
    # whenever a new photo is uploaded, and OMITTED from a dump while None so a
    # profile without a framing serialises byte-identically to one persisted
    # before this field existed (prompt views and the #688 goldens see no new key).
    photo_crop: PhotoCrop | None = None
    linkedin_url: str | None = None
    xing_url: str | None = None
    website_url: str | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_photo_crop(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict) and data.get("photo_crop") is None:
            data.pop("photo_crop", None)
        return data

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


def _blank_to_none(v: object) -> object:
    """A blank/whitespace-only date string means "unknown" — stored as None."""
    if isinstance(v, str):
        stripped = v.strip()
        return stripped if stripped else None
    return v


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

    @field_validator("role", mode="before")
    @classmethod
    def coerce_role(cls, v: object) -> str:
        """#619 — a null role must not reject the whole entry.

        ``role`` declares its own "absent" value (``""``), and the extraction
        prompts offer no "or null" affordance on it while every neighbouring
        field has one — so a source whose project states no role leaves the
        model with ``null`` as its only honest answer. WorkEntry has coerced
        exactly this since #155; the coercion simply never reached the other
        two subclasses, because it was written on WorkEntry rather than on the
        class that DECLARES the field. Found by the first real import through
        the FLAT door carrying a Projects section (2026-08-31): three projects,
        three ``role: null``, the whole import raising ValidationError.
        """
        return v if isinstance(v, str) else ""

    @field_validator("responsibilities", "achievements", "technologies", mode="before")
    @classmethod
    def coerce_experience_list_fields(cls, v: object) -> list:
        return v if isinstance(v, list) else []

    @field_validator("expected_fields", mode="before")
    @classmethod
    def coerce_expected_fields(cls, v: object) -> object:
        return v if (v is None or isinstance(v, list)) else None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def blank_date_is_unknown(cls, v: object) -> object:
        # E055 / JF-F-H1.9: "unknown" is `null`, never "". An empty or
        # whitespace-only string passes the type but defeats every
        # identity-to-None gate downstream (role_add "is the role open?",
        # close-role, the CV month filter) — normalise it at the schema so no
        # writer, UI or agent, can persist the ambiguous form.
        return _blank_to_none(v)

    def org_label(self) -> str:
        """Human label for the engagement's "where" — subclasses override."""
        return ""


class DatedRoleAlias(BaseModel):
    """R2-4 (Hazelnut build 4, ADR-013 note 2026-10-24) — an ADDITIONAL role
    the candidate holds at this employer since a stated date ("also workstream
    lead for X since 2026-08"). The role text is also in ``role_aliases``
    (ADR-013 rule 1); this record adds WHEN it began and which bullets the same
    write placed first for it, so the profile can show "auch <Rolle> seit
    <Datum>" at the top of the entry."""

    role: str
    since: str
    bullets: list[str] = Field(default_factory=list)


class WorkEntry(ExperienceBase):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    company: str = ""
    #: RULING B-1 (Hazelnut build 5, U-7) — bullets of this entry the candidate
    #: answered "Nein" to on "Ist das erreicht?": the goal, target or mandate
    #: they state is NOT reached yet. The bullet itself stays where it is; this
    #: list is the candidate's testimony about its stance, and a writer phrases
    #: what it names as a goal or an aim, never as a result.
    not_yet_reached: list[str] = Field(default_factory=list)
    #: FIG-2 — the candidate's decisions on organisation figures this entry's
    #: bullets carry (``OrgFigureDecision``); omitted while empty.
    org_figure_decisions: list[OrgFigureDecision] = Field(default_factory=list)

    @field_validator("company", mode="before")
    @classmethod
    def coerce_company(cls, v: object) -> str:
        # ``role`` is coerced by ExperienceBase.coerce_role — same behaviour as
        # when this validator named both, now inherited by every subclass.
        return v if isinstance(v, str) else ""

    @field_validator("role_aliases", mode="before")
    @classmethod
    def coerce_role_aliases(cls, v: object) -> list:
        return v if isinstance(v, list) else []

    # All role titles ever used for this position across different CVs/applications.
    # Enables the CV tailoring engine to pick the most relevant title per application
    # (e.g. "Team Lead" for leadership roles, "2nd Level Support" for technical roles).
    role_aliases: list[str] = Field(default_factory=list)
    # ADR-046 amended 2026-10-07 (#716) — other names of this employer the
    # vault recognised ("Roche" for "Roche Diagnostics GmbH").
    company_aliases: list[str] = Field(default_factory=list)

    @field_validator("company_aliases", mode="before")
    @classmethod
    def _coerce_company_aliases(cls, v: object) -> list:
        return _coerce_alias_list(v)

    # R2-4 (Hazelnut build 4) — see DatedRoleAlias. Omitted while empty, like
    # company_aliases, so a profile without one dumps byte-identically.
    dated_role_aliases: list[DatedRoleAlias] = Field(default_factory=list)

    @field_validator("dated_role_aliases", mode="before")
    @classmethod
    def _coerce_dated_role_aliases(cls, v: object) -> list:
        # A malformed record must never reject the whole profile on load.
        if not isinstance(v, list):
            return []
        return [
            a for a in v
            if isinstance(a, DatedRoleAlias)
            or (
                isinstance(a, dict)
                and isinstance(a.get("role"), str) and a["role"].strip()
                and isinstance(a.get("since"), str) and a["since"].strip()
            )
        ]

    @model_validator(mode="after")
    def _dated_alias_needs_its_alias(self) -> "WorkEntry":
        # R2-4 — a dated record describes a role alias; removing the alias
        # (the chip / line ✕, any door) removes its date with it.
        if self.dated_role_aliases:
            names = {
                unicodedata.normalize("NFC", a).strip().casefold()
                for a in self.role_aliases if isinstance(a, str)
            }
            self.dated_role_aliases = [
                d for d in self.dated_role_aliases
                if unicodedata.normalize("NFC", d.role).strip().casefold() in names
            ]
        return self

    @model_serializer(mode="wrap")
    def _omit_empty_company_aliases(self, handler: Any) -> Any:
        # RULING B-1: `not_yet_reached` is omitted while empty too, so a
        # profile nobody answered "Nein" for dumps (and prompts) byte-identical.
        return _omit_empty_alias_lists(
            handler(self),
            ("company_aliases", "dated_role_aliases", "not_yet_reached", "org_figure_decisions"),
        )

    industry_context: str | None = None
    team_size: int | None = None
    budget_managed: str | None = None
    # #328 option 4 — provenance for the three typed fields above, keyed by
    # field name. Recomputed from this entry's OWN bullets on every write
    # (``services/profile/role_facts.py``, called from the single committer),
    # so it is a projection and never a second, ageing store of the figure.
    role_fact_projections: dict[str, RoleFactProjection] = Field(default_factory=dict)

    @field_validator("role_fact_projections", mode="before")
    @classmethod
    def coerce_role_fact_projections(cls, v: object) -> object:
        # A profile persisted before this field existed carries nothing here;
        # a hand-edited JSONB blob could carry anything. Never fail the load.
        return v if isinstance(v, dict) else {}

    def org_label(self) -> str:
        return self.company


class EducationEntry(BaseModel):
    # ADR-077 clause 1 — persisted id (see Skill.id)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
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

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def blank_date_is_unknown(cls, v: object) -> object:
        return _blank_to_none(v)  # see ExperienceBase

    start_date: str | None = None
    end_date: str | None = None
    grade: str | None = None
    thesis_title: str | None = None
    relevant_coursework: list[str] = Field(default_factory=list)
    # ADR-046 amended 2026-10-07 (#716) — "Diplom" for "German Diploma".
    institution_aliases: list[str] = Field(default_factory=list)
    degree_aliases: list[str] = Field(default_factory=list)

    @field_validator("institution_aliases", "degree_aliases", mode="before")
    @classmethod
    def _coerce_edu_aliases(cls, v: object) -> list:
        return _coerce_alias_list(v)

    @model_serializer(mode="wrap")
    def _omit_empty_edu_aliases(self, handler: Any) -> Any:
        return _omit_empty_alias_lists(
            handler(self), ("institution_aliases", "degree_aliases")
        )


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


_PRECISION_FORMS: tuple[tuple[str, str, str], ...] = (
    # (regex, strptime format of the matched text, output precision)
    (r"\d{4}", "%Y", "%Y"),
    (r"\d{4}-\d{1,2}", "%Y-%m", "%Y-%m"),
    (r"\d{4}/\d{1,2}", "%Y/%m", "%Y-%m"),
    (r"\d{1,2}[./]\d{4}", None, "%Y-%m"),  # 03.2014 / 03/2014
    (r"\d{4}-\d{1,2}-\d{1,2}", "%Y-%m-%d", "%Y-%m-%d"),
    (r"\d{1,2}\.\d{1,2}\.\d{4}", "%d.%m.%Y", "%Y-%m-%d"),
    (r"\d{1,2}/\d{1,2}/\d{4}", "%d/%m/%Y", "%Y-%m-%d"),
)


def normalise_partial_date(v: Any) -> str | None:
    """A partial date at the precision the input STATES (ADR-013 amended
    2026-10-20, 783:L65): ``"2010"`` stays ``"2010"``, ``"03.2014"`` becomes
    ``"2014-03"``, a full date stays a full ISO date; unparseable → ``None``
    (as ``_coerce_partial_date`` did). Never pads a month or a day the source
    did not give — the padded ``2010-01-01`` rendered as "01/2010" on a CV
    whose source said "2010" (delivery-3 record 5). For comparisons that need
    a ``date`` (dedupe, span arithmetic) use ``_coerce_partial_date``."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*", s):  # an ISO datetime string
        s = s[:10]
    for pattern, fmt, out in _PRECISION_FORMS:
        if not re.fullmatch(pattern, s):
            continue
        if fmt is None:  # MM.YYYY / MM/YYYY
            month, year = re.split(r"[./]", s)
            s, fmt = f"{year}-{month}", "%Y-%m"
        try:
            return datetime.strptime(s, fmt).strftime(out)
        except ValueError:
            return None
    return None


class Certification(BaseModel):
    # ADR-077 clause 1 — persisted id (see Skill.id)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    issuing_organization: str | None = None
    # ADR-013 amended 2026-10-20 (783:L65): a partial date string at the
    # precision the source states ("2010", "2014-03", "2014-03-15") — was a
    # ``date`` that padded "2010" to 2010-01-01 (rendered "01/2010").
    date_obtained: str | None = None
    expiry_date: str | None = None  # None means doesn't expire
    credential_id: str | None = None
    credential_url: str | None = None
    # ADR-061 clause 3 — "confirmed" (literal/aliased grounding, or a
    # citation-verified LLM adjudication) vs "unconfirmed" (the testimony
    # predicate could not confirm it). An unconfirmed entry is visible and
    # candidate-confirmable but never claimable — it cannot back a CV line, a
    # letter sentence, or a `direct` ledger row. Denials are unaffected: a
    # denied token is still stripped outright (ADR-040, never-claim outranks
    # claim). Default "confirmed" preserves every non-interview write path
    # (CV import, manual edit) exactly as before.
    status: Literal["confirmed", "unconfirmed"] = "confirmed"

    @field_validator("date_obtained", "expiry_date", mode="before")
    @classmethod
    def _coerce_cert_dates(cls, v: Any) -> Any:
        return normalise_partial_date(v)


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
    # German CV proficiency tiers (DACH-native first — ADR-061 clause 5, #304/#317).
    # These are the exact self-declaration words a German CV uses. Before this
    # they fell through to the unknown-string default ("intermediate"), which
    # silently RAISED "Anwender"/"Grundkenntnisse"/"Grundlagen" (all mean the
    # candidate's WEAKEST tier) and silently LOWERED "Verhandlungssicher" /
    # "Fließend"/"Muttersprache" (near the STRONGEST tier) — wrong in both
    # directions, and #304's own case ("SAP (Anwender)") went through exactly
    # this path. Both the German ß and its ASCII "ss" transliteration are
    # listed — a prior incident (#213/#214, U+2019) had a Unicode variant
    # defeat a matcher; do not assume only one spelling reaches this code.
    "anwender": "basic",
    "grundkenntnisse": "basic",
    "grundlagen": "basic",
    "fortgeschritten": "advanced",
    "erfahren": "advanced",
    "verhandlungssicher": "advanced",
    "fließend": "advanced",
    "fliessend": "advanced",
    "muttersprache": "expert",
}


class Skill(BaseModel):
    # ADR-077 clause 1 — a fact pin addresses a vault entry by id, so every
    # pinnable type carries the WorkEntry persisted-id pattern. The factory
    # mints an id only for a blob that lacks one; the value is an identity
    # ONLY once written back through the committer (an unpersisted
    # default_factory id regenerates on every parse — SF-PIN.8).
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    category: Literal["technical", "soft", "language", "domain"] = "technical"
    # ADR-013/061 amended 2026-10-22 (Hazelnut b3 WP-V, ruling V-2 = A): None =
    # the source stated no level. Was a default "intermediate" — every unscaled
    # skill of an 8-year senior read "Intermediate Python" (UX audit, Felix).
    # Values persisted before stay as stored (a stored "intermediate" cannot be
    # told apart from a stated one).
    proficiency: Literal["basic", "intermediate", "advanced", "expert"] | None = None
    years_experience: int | None = None
    # Provenance of ``years_experience`` (ADR-061 clause 7 — transcribed vs
    # computed). Two writers since ADR-061's 2026-09-08 amendment (#684):
    # ``services/skill_enrichment.py`` (all three values) and the reconcile
    # applier (``"transcribed"`` only, for a span the new information itself
    # states — ``UpsertSkill.years_experience``). The precedence between them is
    # part of that amendment and is pinned by test at both sites: a transcribed
    # span WINS, and the deterministic derivation fills only where the
    # transcription is absent. Exactly one of:
    #   "computed"      — derived from the dated roles whose own text evidences
    #                     this skill
    #   "llm_estimated" — the phase-2 estimator produced the number
    #   "transcribed"   — read off the document; nothing was inferred (this is
    #                     also the honest label when no duration is known)
    # The old comment here read "which role/interview surfaced this"; no code
    # ever wrote a role name into it, and a null was indistinguishable from a
    # skill enrichment never visited (#327). The role/interview trail lives in
    # ``experience_refs``.
    source: str | None = None
    last_used: date | None = None

    @field_validator("last_used", mode="before")
    @classmethod
    def _coerce_last_used(cls, v: Any) -> Any:
        # Skill was the one model carrying a bare `date | None` without the
        # shared partial-date coercion Certification and Publication use — yet
        # "last used" is inferred from CVs that state month/year precision at
        # best, and the extraction prompts allow exactly that elsewhere. A
        # "2023" or "2023-06" hit the strict parser and aborted the whole
        # import (see _coerce_partial_date's own docstring). Unparseable text
        # falls to None rather than raising, as it does for the two siblings.
        return _coerce_partial_date(v)
    # Provenance: ids/labels of the experiences (work, project, volunteer) that
    # surfaced this skill. Renamed from work_entry_refs (US172 / ADR-044) now
    # that experiences are unified; legacy JSONB with the old key still loads.
    experience_refs: list[str] = Field(default_factory=list)
    # ADR-061 clause 3 — see Certification.status for the "confirmed" vs
    # "unconfirmed" contract. Set by ``reconcile/stance.py::enforce_stance`` at
    # the interview seam; every other write path (CV import, manual edit)
    # defaults to "confirmed".
    #
    # "denied" (ADR-061 amended 2026-08-08, #485): the candidate RETRACTED a
    # skill the vault held as confirmed. Mark, don't delete — the entry stays
    # with its history, only the status moves, written by the ADR-063 clause
    # 8(e) ``demote_skill`` op. A denied entry is excluded from every claim
    # surface (``reconcile/stance.py::exclude_unconfirmed``), and NOTHING
    # promotes it back except the explicit ADR-059 un-denial act — which does
    # not exist yet, so in code no ordinary op ever moves a denied entry
    # (``apply.py::_promote_to_confirmed``).
    #
    # Skills only: #485 scopes the retraction path to skills, and the
    # reconciler's ``denials`` array carries no entity kind, so minting the
    # value for Certification/Language would create a state nothing can reach.
    status: Literal["confirmed", "unconfirmed", "denied"] = "confirmed"
    # ADR-046 amended 2026-10-07 (#709) — "Machine Learning" for
    # "Maschinelles Lernen", as an import wrote it.
    aliases: list[str] = Field(default_factory=list)

    @field_validator("aliases", mode="before")
    @classmethod
    def _coerce_aliases(cls, v: object) -> list:
        return _coerce_alias_list(v)

    @model_serializer(mode="wrap")
    def _omit_empty_aliases(self, handler: Any) -> Any:
        return _omit_empty_alias_lists(handler(self), ("aliases",))

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
        # An explicit JSON null is not the same as an omitted field: the field
        # default only applies when the key is absent, so `"category": null`
        # reached the Literal raw and raised — aborting the WHOLE import, since
        # _import_from_text does not wrap model_validate. normalize_proficiency
        # below has had this branch all along; this one did not (adversarial
        # pass 2026-09-01, newly reachable through #228's object-shaped skills
        # on the flat/MCP door). Same landing as an unrecognised string, for
        # the nominal-enum reason spelled out below.
        if v is None:
            return "technical"
        if isinstance(v, str):
            lowered = v.lower()
            if lowered in {"technical", "soft", "language", "domain"}:
                return lowered
            # Unknown category string — default to technical rather than raising
            # (e.g. an LLM/import emitting a free-text category like "Cloud
            # Platforms").
            #
            # #319 asked whether this twin should follow proficiency's fall to
            # the lowest tier. It does NOT, deliberately: `category` is a
            # NOMINAL enum (what kind of thing this is), not an ordinal one, so
            # ADR-061's asymmetry — "on uncertainty, never the more permissive
            # state" — has no direction to point in. There is no "lower" among
            # technical/soft/language/domain, and calling an unrecognised skill
            # a "language" to be safe would be a false statement about its kind:
            # a truthfulness cost with nothing on the other side of the ledger.
            #
            # Recorded so the next reader need not re-derive it: this default is
            # not neutral in ONE place. services/skill_enrichment.py gates
            # duration derivation on category — "technical" sits in both
            # _MATCHABLE_CATEGORIES and _ESTIMABLE_CATEGORIES, i.e. it is the
            # most derivation-permissive of the four, so an unrecognised
            # category makes the skill eligible for an ESTIMATED
            # years_experience. That number is a fact carrying its own
            # provenance (clause 7, "llm_estimated") and can no longer become a
            # proficiency (clause 6), which is why it is not treated as a
            # ceiling breach here — but it is a derivation the vault would not
            # otherwise have run, and it is the thing to look at first if this
            # default is ever revisited.
            return "technical"
        return v

    @field_validator("proficiency", mode="before")
    @classmethod
    def normalize_proficiency(cls, v: object) -> object:
        # Two different questions land here and they get two different answers
        # (#319, PO decision 2026-08-07).
        #
        # THE PAGE SAID NOTHING (None, or the field omitted so the model default
        # applies) — None, "not stated" (ruling V-2 = A, 2026-10-22; was
        # "intermediate", the deferral #319 named).
        if v is None:
            return None
        if isinstance(v, str):
            normalized = _PROFICIENCY_ALIASES.get(v.strip().lower())
            if normalized:
                return normalized
            _valid = {"basic", "intermediate", "advanced", "expert"}
            lowered = v.strip().lower()
            if lowered in _valid:
                return lowered
            # An empty slot is the page saying nothing, not the page saying
            # something we could not read — path 1 above, not path 2 below.
            if not lowered:
                return None
            # THE PAGE SAID SOMETHING WE DO NOT RECOGNISE (tomorrow's Xing
            # wording, a CEFR level, an LLM emitting free text) — fall to
            # "basic", the lowest tier, and never raise a validation error.
            #
            # This was "intermediate", i.e. rank 1 of 4, so a stated tier was
            # routinely normalised UPWARD and was gone before any of #317's
            # ratchet sites could apply it as the ceiling ADR-061 clause 5
            # requires. The direction is that ADR's asymmetry: on uncertainty,
            # never the more permissive state (cf. clause 3's `unconfirmed`,
            # and clause 2's adjudication-failure fallback). A wrongly
            # downgraded genuine declaration is recoverable — extend
            # _PROFICIENCY_ALIASES above, and the candidate sees the tier on
            # their own profile page — whereas a silent upgrade defeats the
            # ceiling and leaves nothing downstream able to detect it.
            #
            # ADR-061 as amended 2026-08-02: this value is a CLASSIFICATION
            # input. It may cap what the gap classifier calls a requirement and
            # be recorded in that classification's internal note; it may never
            # be handed to a document writer or reviewer as the candidate's own
            # claim. That constraint binds harder here than for a real
            # declaration — this tier is not the candidate's word at all.
            return "basic"
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
    # ADR-077 clause 1 — persisted id (see Skill.id)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    language: str
    level: str | None = None
    status: Literal["confirmed", "unconfirmed"] = "confirmed"  # ADR-061 clause 3
    # ADR-046 amended 2026-10-07 (#709) — "English" for "Englisch".
    aliases: list[str] = Field(default_factory=list)

    @field_validator("aliases", mode="before")
    @classmethod
    def _coerce_aliases(cls, v: object) -> list:
        return _coerce_alias_list(v)

    @model_serializer(mode="wrap")
    def _omit_empty_aliases(self, handler: Any) -> Any:
        return _omit_empty_alias_lists(handler(self), ("aliases",))


class Publication(BaseModel):
    # ADR-077 clause 1 — persisted id (see Skill.id)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    type: Literal["publication", "patent"] = "publication"
    co_authors: list[str] = Field(default_factory=list)
    venue: str | None = None  # journal, conference, or patent office
    published_date: str | None = None  # partial date, source precision (783:L65)
    doi: str | None = None
    url: str | None = None
    patent_number: str | None = None

    @field_validator("published_date", mode="before")
    @classmethod
    def _coerce_partial_pub_date(cls, v: Any) -> Any:
        return normalise_partial_date(v)


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
    # ADR-046 amended 2026-10-07 (#716) — other names of this organisation.
    organization_aliases: list[str] = Field(default_factory=list)

    @field_validator("organization_aliases", mode="before")
    @classmethod
    def _coerce_organization_aliases(cls, v: object) -> list:
        return _coerce_alias_list(v)

    @model_serializer(mode="wrap")
    def _omit_empty_organization_aliases(self, handler: Any) -> Any:
        return _omit_empty_alias_lists(handler(self), ("organization_aliases",))

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
    #: RULING B-1 (A1-3 gap) — as ``WorkEntry.not_yet_reached``: bullets of this
    #: project the candidate said are not reached yet; a writer phrases them as
    #: a goal or an aim, never as a result.
    not_yet_reached: list[str] = Field(default_factory=list)
    #: FIG-2 — as ``WorkEntry.org_figure_decisions``.
    org_figure_decisions: list[OrgFigureDecision] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _omit_empty_not_yet_reached(self, handler: Any) -> Any:
        return _omit_empty_alias_lists(handler(self), ("not_yet_reached", "org_figure_decisions"))

    def org_label(self) -> str:
        return self.name


# ─── Merge conflict model (stored in metadata, resolved by user) ──────────────

class Conflict(BaseModel):
    """A two-value dispute parked for the candidate (ADR-013 / ADR-046).

    #218 — ``entity_id`` names the entity the disputed value hangs off, when
    there is one: originally work / project / volunteer only (``apply_ops``'s
    ``resolve()`` closure), widened by #633 (2026-09-01) to every id-bearing
    section (education, certifications, languages, publications, skills,
    signature_stories too — see ``apply.py``'s ``resolve_any``). ``section`` +
    ``field`` alone cannot address it: ``work_experience`` / ``achievements``
    is true of every role, so the resolution path had to guess (it updated the
    FIRST entry still holding the old value) and could not reach a bullet
    inside a list at all. Optional and defaulted to ``None`` — profile-level
    disputes (``professional_summary``, ``personal_info``) have no entity, and
    conflicts written before this field existed load unchanged.
    """

    conflict_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    section: str
    field: str
    entity_id: str | None = None
    existing_value: Any
    incoming_value: Any
    source: str  # which CV/import caused this
    suggested_resolution: str | None = None
    resolved: bool = False


# ─── Enrichment tracking ──────────────────────────────────────────────────────

class FieldChange(BaseModel):
    section: str
    field: str
    # "removed" joined the vocabulary with #480 PR 3 (ADR-063 amended
    # 2026-08-09 clause 8): `ReplaceSection` diffs the incoming section against
    # the current one and records **each removal as its own change**. Before
    # that, a manual section edit left one opaque blob ("the whole section, old
    # → new"), so a deletion was invisible in the trail and only recoverable by
    # diffing two JSON dumps by eye. A removal is not a kind of "update":
    # collapsing them would make "this field was cleared" and "this entry is
    # gone" indistinguishable on the "what changed & why" surface.
    action: Literal["added", "updated", "merged", "removed"]
    old_value: Any | None = None
    new_value: Any = None
    # ADR-040: a human-readable "why" note shown on the "what changed & why" surfaces.
    # `rationale` is an English fallback / audit string; the frontend localizes via
    # `rationale_key` (ADR-038 — the surface follows the user's UI language). Legacy
    # records without a key fall back to the stored `rationale` prose.
    rationale: str | None = None
    rationale_key: str | None = None
    # ADR-063 amended 2026-10-22 (Hazelnut b3 WP-V, D-B3V2): the id-bearing
    # entity this change was written onto (a role, a project, a skill …), so a
    # receipt can say WHICH role a bullet joined. Optional and default-None:
    # records persisted before it existed load unchanged, and a profile-level
    # change (summary, personal_info, metadata) has no entity.
    entity_id: str | None = None


class MatchReceipt(BaseModel):
    """One `match_existing` binding the reconciler emitted and the applier
    resolved (#707, ADR-046 / ADR-063 amended 2026-09-16).

    The receipt for something that DID happen without a write: the model judged
    an incoming flat-section entry — ``incoming``, the name exactly as the new
    information wrote it — to be the existing entity ``entity_id`` in
    ``section``, whose own natural-key label is ``existing``. Nothing in the
    vault changed. It rides ``ApplyResult.matched`` → ``ApplyImportMerge.matched``
    → ``EnrichmentRecord.matched`` and is rendered on the profile page's history
    ("Recognised as already present: English → Englisch").

    Deliberately NOT a :class:`FieldChange`: five readers take ``bool(changes)``
    as "the vault changed" — the interview bridge's ``addressed`` (which closes
    the gap and paints the *resolved* badge), the agent bridge's ledger upgrade,
    MCP ``resolve_gap``'s ``status="addressed"``, ``migrate`` — and a restatement
    must never address a gap (refuter BLOCKER, 2026-09-16). A wrong binding — a
    genuinely new skill bound to an unrelated existing id — is a loss the import
    witness cannot see; this receipt is its only detection (System-FMEA
    ``SF-PROFILE.11``).
    """

    model_config = ConfigDict(extra="forbid")

    section: str
    entity_id: str
    incoming: str
    existing: str
    # ADR-046 / ADR-063 amended 2026-10-07 (#709 #716 #717) — all optional, so a
    # receipt persisted before them loads unchanged.
    #: who recognised the pair: the model (`match_existing`), a recorded
    #: alternate name (an applier identity hit), or the closed DE/EN
    #: language-name table. A `name_table` pair is a fact and is not undoable.
    basis: Literal["model", "alias", "name_table"] = "model"
    #: alias field -> the name recorded on the entity by THIS binding (the
    #: import bridge's alias writer); what `SeparateMatch` takes back.
    aliases_added: dict[str, str] = Field(default_factory=dict)
    #: the incoming entry as the import read it (ids stripped) — what
    #: `SeparateMatch` adds back as its own entry. Import path only.
    incoming_entry: dict[str, Any] | None = None
    #: set when the candidate said "Nicht dasselbe" (`SeparateMatch`).
    undone_at: datetime | None = None
    #: FS-5 (ADR-046 amended 2026-10-24) — the evidence refs THIS binding added
    #: to the target (a targeted / alias skill upsert's merge); "Nicht dasselbe"
    #: takes them back. Empty on receipts written before it existed.
    refs_added: list[str] = Field(default_factory=list)
    #: FS-6 (ruling V-4 = A) — set when the candidate answered "Ja" to "Ist A
    #: dasselbe wie B?" (`ConfirmMatch`); the pair question is not asked again.
    confirmed_at: datetime | None = None


class Availability(BaseModel):
    """The candidate's notice period / earliest start (ADR-063 amended
    2026-10-22, D-B3V7; rulings 787:L3 + V-4).

    It belongs to the CURRENT contract, so it is stored once on the profile and
    pre-fills every letter. Free text as the candidate says it ("3 Monate zum
    Quartalsende"); ``recorded_at`` and ``source`` are stamped by the one write
    door (``SetAvailability`` through ``commit_ops``), never by a caller.
    ``reminder_dismissed_at`` is the server-side close of the dashboard
    reminder (ruling V-4 — never browser storage; the user logs in from
    several devices). Read: ``services/profile/availability.py``.
    """

    text: str | None = None
    recorded_at: str | None = None  # ISO date (YYYY-MM-DD)
    source: str | None = None
    reminder_dismissed_at: str | None = None  # ISO date (YYYY-MM-DD)


class RestatedReceipt(BaseModel):
    """One bullet a second source would have added to an existing entry and the
    import restatement check held back as a restatement of a held bullet
    (ADR-046 + ADR-063 amended 2026-10-22, D-B3V6; founder ruling V-3 = A).

    The receipt for something that was NOT written: ``text`` (the new bullet as
    the reconciler wrote it) restates ``restates`` (a bullet ``entity_id``
    already holds) — the model's judgement, ``services/profile/reconcile/
    restatement.py``. Rides ``MergeResult.restated`` → ``ApplyImportMerge.
    restated`` → ``EnrichmentRecord.restated`` and every import door's
    response; "Trotzdem übernehmen" (``POST /api/profile/restated/apply``)
    writes it on the candidate's word. Not a :class:`FieldChange` for the same
    reason :class:`MatchReceipt` is not one: nothing in the vault changed.
    """

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    entity_label: str | None = None
    section: str
    field: Literal["responsibilities", "achievements"]
    text: str
    restates: str


class KeptSeparateReceipt(BaseModel):
    """FS-6 (Hazelnut build 4, ruling V-4 = A) — a skill binding the import's
    #801 guard REFUSED (``binding_guard.refuse_false_skill_bindings``): the
    reconciler named ``existing`` as the same skill as the document's
    ``incoming``, the guard found it names another entry or crosses
    technical/soft, and the incoming skill was kept as its own entry. The
    receipt for a decision that kept two things apart, so the import summary
    can say so ("Separat behalten"). Rides ``MergeResult.kept_separate`` →
    ``ApplyImportMerge.kept_separate`` → ``EnrichmentRecord.kept_separate`` and
    the import doors' response, exactly like :class:`RestatedReceipt`."""

    model_config = ConfigDict(extra="forbid")

    section: str = "skills"
    entity_id: str
    incoming: str
    existing: str
    reason: str = ""


class ImportNotApplied(BaseModel):
    """One incoming CV-import entry the merge's own ops do not carry (#615,
    ADR-063 amended 2026-08-28, second entry of the day).

    Sibling of :class:`applire.schemas.testimony.NotApplied` (#370) for the
    import doors — same doctrine (ADR-062 clause 1: FACTS only, never a
    judgement about whether the entry "really" landed), same shape family
    (an identifying label + a reason), a separate class rather than a shared
    base because the identifying information differs: testimony's witness
    quotes a SPAN of submitted text; the import door names a SECTION and the
    incoming entry's own natural-key LABEL (``services.profile.reconcile.
    import_witness.compute_import_not_applied`` — see its module docstring
    for the carried-predicate and the SAP CO/FI split named as the known
    false-positive shape, not a bug).

    Computed by :func:`compute_import_not_applied`, never by the model. No
    item here is proof of loss: an AMBIGUOUS near-dupe verdict, a compound
    entry the reconciler split in two on merge, or an op that legitimately
    merges into an existing entity without restating its identity fields all
    list an entry that is not actually missing from the vault.
    """

    model_config = ConfigDict(extra="forbid")

    #: One of the nine list-valued content sections (``work_experience``,
    #: ``education``, …). ``None`` for a ``reason="op_rejected"`` item — a raw
    #: op that failed schema validation before ever having a determinable
    #: section (mirrors ``NotApplied``'s ``kind="op"`` items, which carry no
    #: testimony span for the identical reason).
    section: str | None = None
    #: The incoming entry's own natural-key fields, human-readable (e.g.
    #: ``"Universität Stuttgart / M.Sc."``), or — for ``reason="op_rejected"``
    #: — the rejected raw op's own declared ``"op"`` type string.
    label: str
    #: ``summary_populated`` (ADR-061 amended 2026-09-08, #684) is the one
    #: reason a STATEMENT intake can produce, and the only one whose ``section``
    #: is not a list-valued content section: a ``set_summary`` against an
    #: already-populated ``professional_summary`` slot from an interview /
    #: testimony / agent-claims turn is dropped rather than disputed (the
    #: summary is the candidate's own self-description, not an alternative to
    #: their own answer), and ``label`` carries the language slot (``"de"`` /
    #: ``"en"``) instead of a natural-key label. Still a FACT, on the same
    #: doctrine as its two siblings: the slot was non-empty and the incoming
    #: text differed — never a judgement about which text is better.
    #:
    #: Widening this Literal is load-bearing for four response schemas that
    #: carry the type; ``EnrichmentRecord`` deliberately has no
    #: ``extra="forbid"``, so records persisted before this value existed load
    #: unchanged.
    #: ``no_write`` (founder ruling M-1c, 2026-09-09) is the second reason a
    #: STATEMENT intake can produce, and the second whose ``section`` is not a
    #: content section (it is ``None``): the answer STATED something — its
    #: non-denial clauses are non-empty — and the turn produced no vault write,
    #: no confirmation, no conflict and no other receipt at all. ``label``
    #: carries the answer's own positive residue, truncated. Still a FACT
    #: (``reconcile.witness.compute_no_write``): a negation-word test over the
    #: clauses plus "did anything land", never a judgement about whether the
    #: statement was worth recording.
    #: ``no_write_already_known`` / ``no_write_question_only`` (ADR-046 amended
    #: 2026-09-11, ruling M5.1.4) are the same no-write event with the model's
    #: own ``empty_reason`` attached: the answer restated something the profile
    #: already carries, or asked a question and stated no fact. ``no_write``
    #: stays as the third branch AND as the fail-safe for a model that emitted
    #: no reason at all — a receipt persisted before this amendment, or a
    #: model that ignored the ask, loads and renders unchanged.
    #: The three ``confirmation_*`` values (Bug #723, ADR-063 amended
    #: 2026-09-18) are what makes the CONFIRMATION-RESOLUTION intake honest.
    #: The attribution guard (#243) pulls a bullet OUT of the merge and asks
    #: where it belongs; the resolution now writes it (``move`` / ``keep_here``)
    #: — and where it cannot, it must say so instead of minting a
    #: metadata-only receipt that reads as success, which is the harm #723
    #: filed. ``confirmation_discarded``: the candidate chose "discard it".
    #: ``confirmation_held``: the answer names an employer the vault holds under
    #: several roles and nothing in the held text discriminates between them, so
    #: a narrower keyed ask is parked and the content stays held (founder ruling
    #: V-1 / D-6 — never "the most recent role"). ``confirmation_unresolvable``:
    #: the named entry is gone, or the scalar slot it would fill was already
    #: populated by someone else. ``confirmation_already_present``: the entry
    #: the answer named already carries that exact text (`_append_dedup`
    #: suppressed the append, ADR-082), so the decision is satisfied and
    #: ``changes`` is silent — the receipt says it here instead.
    #: ``label`` carries the held field plus its own
    #: text, truncated — the same shape ``no_write`` uses, and the thing that
    #: makes the loss diagnosable from the receipt alone. All three are FACTS
    #: (ADR-062 clause 1): which option key was answered, how many entries carry
    #: the anchor's name, whether a slot was empty.
    #: ``target_unresolved`` (ADR-063 amended 2026-10-20, 783:L7/L60): an
    #: ``add_bullets`` or ``set_field`` op named a ``target`` handle no entity
    #: carries (a mistyped or invented id), so its content was written nowhere.
    #: ``section`` is ``None`` — the target is unknown — and ``label`` carries the
    #: op type plus its first incoming text, truncated. A FACT: the handle
    #: resolved to nothing.
    reason: Literal[
        "no_op_carried_entry",
        "op_rejected",
        "summary_populated",
        "no_write",
        "no_write_already_known",
        "no_write_question_only",
        "confirmation_discarded",
        "confirmation_held",
        "confirmation_unresolvable",
        "confirmation_already_present",
        "target_unresolved",
    ]
    #: UAT-3d (Hazelnut build 5) — a deterministic id for this item inside its
    #: record (``section:<hash of label>``), set where the item is a whole
    #: incoming record. ``None`` on receipts persisted before this field.
    item_key: str | None = None
    #: The incoming entry payload (no id), captured ONLY for an item that is a
    #: whole new record of a list section (work_experience, education, projects,
    #: volunteer_activities, certifications, languages, publications) — what
    #: "Übernehmen" appends. ``None`` for scalar/summary items and for receipts
    #: persisted before this field (those fall back to go-to-section).
    entry: dict[str, Any] | None = None
    #: Set when the candidate adopted this item; an adopted item is no longer
    #: counted or shown by the hub.
    adopted_at: datetime | None = None
    #: Computed when the hub issue is built: has an ``entry`` and no
    #: ``adopted_at``. Never persisted as true.
    adoptable: bool = False
    #: D-2 (Hazelnut build 5 delivery run, main ruling) — the id of the existing
    #: entry of the same section the model judged this item to BE (renamed or
    #: translated institution, employer, degree), set once at the end of the
    #: import. Such an item was not applied because it is already there: it is
    #: silent (class S), never offered for "Übernehmen" (a duplicate).
    already_present_as: str | None = None


#: ADR-062 clause 1 (a FACT over the reason key, never a judgement) / ADR-066
#: (one implementation — no caller re-derives this set) — F-7, #674, founder
#: edge UAT 2026-09-20. `merge_status` used to test `not_applied`'s bare
#: truthiness, which made a batch of purely-cosmetic `no_op_carried_entry`
#: items (this class's own docstring: "the known false-positive shape, not a
#: bug") read as `partial`, and `mcp/AGENT_GUIDE.md` then told the agent to
#: report a false loss to the candidate.
#:
#: Only these six reasons are evidence that incoming content did NOT reach
#: the vault: ``op_rejected`` (a raw op failed schema validation before it
#: could carry anything), ``summary_populated`` (the incoming summary text
#: was dropped rather than disputed), ``no_write`` (the candidate stated
#: something and the turn produced no vault write at all),
#: ``confirmation_held`` (the content is parked pending one more question —
#: not yet written), ``confirmation_unresolvable`` (the named entry is
#: gone, or the slot it would fill was already taken by someone else) and
#: ``target_unresolved`` (ADR-063 amended 2026-10-20, 783:L7/L60: an
#: ``add_bullets`` / ``set_field`` op named a handle no entity carries, so its
#: content was not written anywhere).
#:
#: The other five reasons name entries that are NOT missing:
#: ``no_op_carried_entry`` is the documented false-positive shape;
#: ``no_write_already_known`` / ``no_write_question_only`` restated something
#: already known or asked a question rather than stating something new;
#: ``confirmation_discarded`` is the candidate's own choice; and
#: ``confirmation_already_present`` means the vault already carries that
#: exact text (`_append_dedup` suppressed the append, ADR-082).
#:
#: Any reader that needs "did the candidate lose something" — `merge_status`
#: chief among them — calls :func:`is_loss_reason` rather than testing
#: `not_applied`'s bare truthiness.
LOSS_REASONS: frozenset[str] = frozenset(
    {
        "op_rejected",
        "summary_populated",
        "no_write",
        "confirmation_held",
        "confirmation_unresolvable",
        "target_unresolved",
    }
)


def is_loss_reason(reason: str | None) -> bool:
    """True when ``reason`` is one of :data:`LOSS_REASONS` — the FACT that
    this :class:`ImportNotApplied` item is evidence incoming content did not
    reach the vault. Never a judgement about severity (a held confirmation is
    loss but recoverable; a rejected op is loss and needs a resend). ``None``
    is NOT loss — silence about the reason is not evidence of loss."""
    return reason in LOSS_REASONS


#: 783:L41 (ADR-021 / ADR-063 amended 2026-10-20) — how the ingest's
#: extraction review loop settled, as a FACT read off the loop's own
#: ``ReviewSettle`` (``services.review_issues``), never re-derived.
ExtractionReviewStatus = Literal["approved", "open_findings", "not_reviewed", "disabled"]


class ExtractionReview(BaseModel):
    """The import's extraction review outcome (783:L41, ADR-021 amended 2026-10-20).

    Before this, a CV import whose ``cv_extraction`` review could not run — the
    reviewer call failed (e.g. verdict-budget truncation on a verbose model) or
    returned an unparseable verdict — shipped the unreviewed draft into the
    vault and said so only in a log line ("shipping current draft
    un-reviewed"). ``status`` is derived from the loop's settle path:

    * ``approved`` — ``approved`` / ``minor_only``: the reviewer accepted it;
    * ``open_findings`` — ``exhausted`` / ``cycle_detected`` /
      ``generator_call_failed``: reviewed, blocking findings still open (the
      shipped draft is a reviewed-and-rejected one);
    * ``not_reviewed`` — ``reviewer_call_failed`` / ``review_malformed``: no
      verdict exists for the draft that shipped;
    * ``disabled`` — the review layer did not run (``LLM_REVIEW_MAX_RETRIES``
      is 0: the operator's choice, ``ReviewSettle.path is None``).
    """

    status: ExtractionReviewStatus
    path: str | None = None
    rounds: int = 0
    open_blocking: int = 0
    #: Build-2 adversarial F-F (ruling R3-b): the source handed to the
    #: extraction (and so to this review) was cut at the extraction input cap.
    #: ``source_chars_read`` of ``source_chars_total`` characters were read; the
    #: rest of the document was never extracted. ``False``/``None`` on every
    #: import under the cap and on receipts persisted before the field existed.
    source_truncated: bool = False
    source_chars_read: int | None = None
    source_chars_total: int | None = None


class SourceTruncation(BaseModel):
    """Build-2 adversarial F-F (ruling R3-2 = B): the import read the first
    ``read_chars`` of ``total_chars`` characters of its source; the rest was
    never extracted. Derived from :class:`ExtractionReview` — one fact, two
    readers (the import result and the agent door)."""

    read_chars: int
    total_chars: int


def source_truncation_of(review: "ExtractionReview | None") -> SourceTruncation | None:
    if review is None or not review.source_truncated:
        return None
    if review.source_chars_read is None or review.source_chars_total is None:
        return None
    return SourceTruncation(read_chars=review.source_chars_read, total_chars=review.source_chars_total)


_REVIEW_PATH_STATUS: dict[str | None, str] = {
    "approved": "approved",
    "minor_only": "approved",
    "exhausted": "open_findings",
    "cycle_detected": "open_findings",
    "generator_call_failed": "open_findings",
    "reviewer_call_failed": "not_reviewed",
    "review_malformed": "not_reviewed",
    None: "disabled",
}


#: Founder ruling V-2 = C (2026-10-20): the settle paths on which the review
#: could NOT run — the import is HELD behind the US167 staged gate. Truncation
#: and timeout of the reviewer call settle as ``reviewer_call_failed``.
EXTRACTION_REVIEW_HOLD_PATHS: frozenset[str] = frozenset(
    {"reviewer_call_failed", "review_malformed", "generator_call_failed"}
)
#: The staged-gate value for such a hold (``UploadRecord.gate_status``).
REVIEW_NOT_RUN_GATE = "review_not_run"


def extraction_review_from_settle(path: str | None, rounds: int, open_blocking: int) -> ExtractionReview:
    """THE mapping settle-path → :class:`ExtractionReview` (ADR-066: one
    implementation). An unknown future path reads ``not_reviewed`` — the
    fail-safe direction: the user is told to look, never told it was checked."""
    status = _REVIEW_PATH_STATUS.get(path, "not_reviewed")
    return ExtractionReview(status=status, path=path, rounds=rounds, open_blocking=open_blocking)


class EnrichmentRecord(BaseModel):
    # Stable id so a pre-merge snapshot (US168 / ADR-042) can key to the merge
    # this record represents, and undo can detect whether it is still the head.
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime
    source: Literal["cv_upload", "cv_paste", "linkedin_import", "xing_import", "interview", "agent_interview", "manual_edit", "manual_role_add", "testimony"]
    source_session_id: str | None = None
    changes: list[FieldChange] = Field(default_factory=list)
    confidence: float | None = None  # for LLM-extracted data
    # US161 (ADR-041 amended) — per-entity {extracted, stored, delta} captured at
    # merge time so silent data-loss (FMEA JF-M-3.3) is detectable. Merge records only.
    reconciliation: dict[str, dict[str, int]] | None = None
    # #615 (ADR-063 amended 2026-08-28, second entry) — the same merge's carried-
    # predicate facts, beside the counts they are derived from. Optional and
    # default-empty so a receipt persisted before this field existed loads
    # unchanged (no `extra="forbid"` on this class — see refuter A's C2/(ii)).
    not_applied: list[ImportNotApplied] = Field(default_factory=list)
    # #707 (ADR-046 / ADR-063 amended 2026-09-16) — the `match_existing` bindings
    # of the same batch: entries the reconciler recognised as already present
    # under another surface form. Optional and default-empty for the same
    # reason `not_applied` is; NOT folded into `changes` (see `MatchReceipt`).
    matched: list[MatchReceipt] = Field(default_factory=list)
    #: D-B3V6 (ruling V-3 = A) — second-source bullets held back as restatements.
    restated: list[RestatedReceipt] = Field(default_factory=list)
    kept_separate: list[KeptSeparateReceipt] = Field(default_factory=list)  # FS-6
    #: 783:L41 (ADR-021/ADR-063 amended 2026-10-20) — the extraction review
    #: outcome of the import this record receipts; ``None`` on every
    #: non-import record and on records persisted before the field existed.
    extraction_review: ExtractionReview | None = None


# ─── Profile metadata ─────────────────────────────────────────────────────────

class ProfileChangesResponse(BaseModel):
    """US145 / ADR-040 — the "what changed & why" surface data contract.

    Sourced from the Master Profile only (decision trail + flagged conflicts), never
    from the source uploads, which hard-delete after 7 days (ADR-005).
    """
    enrichment_history: list["EnrichmentRecord"] = Field(default_factory=list)
    pending_conflicts: list["Conflict"] = Field(default_factory=list)


class ReceiptFact(BaseModel):
    """One fact a session wrote, as the receipt summary shows it (ADR-040
    amended 2026-10-22, ruling V-1 = A). One fact = one line: a list value is
    one fact per item, and the same item written to several sections (a role's
    technologies AND a skill) is ONE fact naming every section in ``also_in``.
    """

    #: The normalised text — stable within one summary, for React keys.
    key: str
    #: The section the fact is listed under (its first write in trail order).
    section: str
    #: What kind of entry the fact is — a FACT about the write (which field /
    #: section it went to), never a judgement of importance (ADR-062 cl. 1).
    kind: Literal[
        "story", "result", "position", "project", "certification",
        "responsibility", "tool", "skill", "education", "language",
        "limit", "detail",
    ]
    action: Literal["added", "updated", "merged", "removed"]
    text: str
    #: Label of the entity the fact was written onto ("Leiter Produktion @
    #: Weberit GmbH"), when the receipt names one.
    entity_label: str | None = None
    #: The OTHER sections the same fact was also written to.
    also_in: list[str] = Field(default_factory=list)
    #: The story's result line, shown under its title.
    detail: str | None = None


class ReceiptEntityCount(BaseModel):
    label: str
    count: int


class ReceiptGroup(BaseModel):
    section: str
    count: int
    #: Per-entity counts (work/projects: per role or project).
    entities: list[ReceiptEntityCount] = Field(default_factory=list)
    #: The group's first items (≤ 5), as text.
    preview: list[str] = Field(default_factory=list)
    #: How many items the preview leaves out.
    more: int = 0


class ReceiptSummary(BaseModel):
    """The interview receipt as a summary of ONE session (ADR-040 amended
    2026-10-22). Produced by ``services/profile/receipts.py`` from the
    complete decision trail, which it never rewrites."""

    session_id: str | None = None
    #: Interview turns recorded in the session (one trail record per turn).
    answers: int = 0
    facts_total: int = 0
    groups: list[ReceiptGroup] = Field(default_factory=list)
    top: list[ReceiptFact] = Field(default_factory=list)
    facts: list[ReceiptFact] = Field(default_factory=list)


class ImportReceiptSummary(BaseModel):
    """R2-8 (9) (Hazelnut build 4, ruling V-2 = A) — the last import, in the
    user's terms (``services/profile/receipts.py::summarize_last_import``):
    ``GET /api/profile/import-summary``. Empty (``record_id`` None) when the
    trail holds no import."""

    record_id: str | None = None
    timestamp: datetime | None = None
    source: str | None = None
    #: The uploaded file the record belongs to, when one is known.
    file_name: str | None = None
    added_total: int = 0
    groups: list[ReceiptGroup] = Field(default_factory=list)
    facts: list[ReceiptFact] = Field(default_factory=list)
    #: Recognised pairs, one per (entry, incoming name), not undone.
    matched: list[MatchReceipt] = Field(default_factory=list)
    kept_separate: list[KeptSeparateReceipt] = Field(default_factory=list)
    restated: list[RestatedReceipt] = Field(default_factory=list)
    not_applied_total: int = 0


class DeniedConcept(BaseModel):
    """#231 — an explicit denial the candidate gave testimony for ("I did not
    personally configure the embedding models…"), persisted so it survives
    past the single reconcile turn that produced it.

    ``concept`` is the reconciler's own denied-token verdict (``rc.denials`` —
    the same list ``enforce_stance`` already uses to strip a same-turn op);
    ``statement`` is the verbatim testimony, kept for the transparency
    receipt. Re-denying the same concept (case-insensitively) refreshes the
    existing record in place rather than duplicating the entry — matched by
    ``services.profile.reconcile.stance.record_denials``.

    #348 — ``statement``/``source`` are **write-once**: only the concept's own
    FIRST denial sets them, and no later re-denial may rewrite them. They are
    the candidate's own words, and the record is a citable vault path (the
    Oracle's denial rail, the letter's STATED LIMIT block, the transfer
    bridge), so a rewrite silently re-files one turn's testimony under another
    turn's subject. A re-denial may move only ``date`` and ``denial_level``
    (direct -> partial, never back). The only sanctioned way the content of a
    denial record changes is ADR-059's 2026-07-26 candidate-correction path,
    which is receipted rather than overwritten.

    ADR-064 — ``denial_level``: "direct" = the named form is ruled out,
    adjacent coverage is still unknown; "partial" = adjacent is ruled out
    too, elicitation is exhausted. Default "direct" is correct for
    back-compat: every denial recorded before this field existed denied
    the named form and was never probed further.

    ADR-064 finding-fix — ``probe_asked``: elicitation bookkeeping, NOT
    testimony. Records that the one permitted transfer probe has been
    *issued* for this concept, regardless of what the candidate then
    answered (evidence, another denial, or an unproductive non-answer).
    Only a genuine second denial may still move ``denial_level`` to
    "partial" — that field stays reserved for what the candidate actually
    stated. Gating the probe on ``denial_level == "direct" and not
    probe_asked`` (rather than on ``denial_level`` alone) is what keeps the
    probe terminal when its answer was unproductive: an unproductive answer
    must never be written up as testimony the candidate never gave, but the
    fact that we already asked still has to be durable so a later genuine
    denial on the same gap cannot re-trigger it. Default ``False`` is
    correct for back-compat: every denial recorded before this field
    existed predates the transfer probe and was never issued one.
    """

    concept: str
    statement: str
    source: Literal["interview", "agent_interview", "testimony"]
    date: str  # ISO date (YYYY-MM-DD) — the day the denial was recorded
    denial_level: Literal["direct", "partial"] = "direct"
    probe_asked: bool = False


class WithdrawnDenial(DeniedConcept):
    """G-5 (Hazelnut build 4; ADR-059 amended 2026-10-24, building the
    2026-07-26 un-denial act) — a denial the CANDIDATE withdrew. The record
    moves here verbatim from ``denied_concepts`` (concept, statement, source,
    date, level, probe flag stay the candidate's original testimony) and gains
    the day it was withdrawn: mark, don't erase (cl. 4). Nothing reads this list
    as a denial, so every floor, the letter's STATED LIMIT block and the Oracle
    stop seeing it without a reader change. Only ``reconcile.apply.
    _apply_withdraw_denial`` writes it."""

    withdrawn_at: str  # ISO date (YYYY-MM-DD)


def render_localized_confirmation(
    *,
    question: str,
    options: list[str],
    question_i18n: dict[str, str] | None,
    options_i18n: list[dict[str, str]] | None,
    lang: str,
) -> tuple[str, list[str]]:
    """ONE rendering of a confirmation's language-independent form (#669).

    ADR-063 amended 2026-09-05 clause 2: the STORED form is language-independent
    and rendering happens at the projection against the reader's current
    ``ui_language``. Three shapes carry that form — ``RequestConfirmation`` (the
    op), ``PendingConfirmation`` (the durable park) and the session-state dict —
    and ADR-066 says one capability gets one implementation, so all three
    delegate here rather than each growing its own fallback chain.

    Fallback: ``[lang] ?? de ?? en ?? the plain field``. A record persisted
    before this change carries neither payload and renders exactly as it always
    did. ``options_i18n`` is used only when it is positionally aligned with
    ``options`` — a length mismatch means the record is malformed, and showing
    the plain options is strictly better than pairing the wrong texts with the
    wrong keys on a surface whose answer decides a vault write.
    """
    def pick(payload: dict[str, str] | None, plain: str) -> str:
        if not payload:
            return plain
        return payload.get(lang) or payload.get("de") or payload.get("en") or plain

    rendered_question = pick(question_i18n, question)
    if options_i18n and len(options_i18n) == len(options):
        rendered_options = [
            pick(payload, plain) for payload, plain in zip(options_i18n, options)
        ]
    else:
        rendered_options = list(options)
    return rendered_question, rendered_options


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
    # ADR-063 amended 2026-09-05, BUILT 2026-09-08 (#669) — the STORED form is
    # language-independent, and rendering happens at the projection against the
    # reader's current `ui_language`. A confirmation parked while the candidate
    # was reading German and answered after they switch to English is then
    # simply a different render of the same record — the persisted record is
    # testimony about what was asked, and it may not be testimony in only one
    # language.
    #
    # `option_keys` is the identity the ANSWER resolves on (see
    # `session._skill_confirmation_decision`), positionally paired with
    # `options`. Empty on a record persisted before this change and on any
    # model-emitted confirmation, both of which resolve through the back-compat
    # English substring matcher. All three default so an older persisted record
    # loads unchanged.
    question_i18n: dict[str, str] | None = None
    options_i18n: list[dict[str, str]] | None = None
    option_keys: list[str] = Field(default_factory=list)

    def rendered(self, lang: str) -> tuple[str, list[str]]:
        """This reader's view of the parked ask — see
        :func:`render_localized_confirmation`."""
        return render_localized_confirmation(
            question=self.question,
            options=list(self.options),
            question_i18n=self.question_i18n,
            options_i18n=self.options_i18n,
            lang=lang,
        )


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
    # #231 — explicit denials the candidate gave testimony for, persisted so a
    # later analyze_gaps run cannot re-infer the denied concept via adjacency
    # (the ledger's deterministic override, services.keyword_ledger, reads this).
    denied_concepts: list[DeniedConcept] = Field(default_factory=list)
    # G-5 (ADR-059 amended 2026-10-24) — denials the candidate withdrew; the
    # history of a denial is never erased. See :class:`WithdrawnDenial`.
    withdrawn_denials: list[WithdrawnDenial] = Field(default_factory=list)
    # G-5 / FS-7 (Hazelnut build 4, Lead G; additive) — the CV-contradicts-a-denial
    # Health items the candidate ANSWERED "my answer stands" (denial_holds),
    # keyed by stance.denial_scope_key. The resolution removes the item from
    # pending_confirmations, so this is what keeps the next import from asking
    # the same question again. A withdrawn denial (cv_holds) needs no entry: it
    # is gone from denied_concepts and cannot re-park.
    denial_scope_answered: list[str] = Field(default_factory=list)
    # ADR-099 cl. 11 / ADR-046 am. (Hazelnut build 5, WP-A1, U-9) — two entries
    # the candidate keeps as SEPARATE entries: answered "Nein, getrennt" on the
    # duplicate-entry question, or "Beide behalten" on an entry dispute
    # (RULING A1-i1). The duplicate-entry question never re-asks such a pair.
    separate_entry_pairs: list["EntryPair"] = Field(default_factory=list)
    # U-9 — the model's same-position verdict per candidate pair (identical
    # dates), judged once at the end of a CV import. Only "different" hides a
    # pair from the duplicate-entry question; an unjudged pair is asked.
    entry_identity_verdicts: list["EntryIdentityVerdict"] = Field(default_factory=list)
    # RULING B-1 (U-7) — the model's per-bullet "worded as a goal or a task still
    # to do" judgement for the bullets of current work entries, judged once at
    # the end of a CV import (negatives are kept so a bullet is judged once).
    aspiration_verdicts: list["AspirationVerdict"] = Field(default_factory=list)
    # B-1 — the candidate's answers to "Ist das erreicht?"; an answered bullet
    # is never asked again.
    aspiration_answers: list["AspirationAnswer"] = Field(default_factory=list)
    # FIG-2 — the model's per-bullet organisation-figure judgement (negatives
    # too, so a bullet is judged once), judged at the end of a CV import. The
    # candidate's answer lives on the entry (``org_figure_decisions``).
    org_figure_verdicts: list["OrgFigureVerdict"] = Field(default_factory=list)


class EntryPair(BaseModel):
    """Two entries of one section, ids sorted (an unordered pair)."""

    section: str
    ids: list[str] = Field(min_length=2, max_length=2)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("ids", mode="after")
    @classmethod
    def _sorted(cls, v: list[str]) -> list[str]:
        return sorted(v)


class EntryIdentityVerdict(EntryPair):
    verdict: Literal["same", "different", "unsure"]


class AspirationVerdict(BaseModel):
    """B-1 — one bullet of a current work entry or current project and the
    model's judgement whether it is worded as a goal or a task still to do.
    ``key`` is ``aspiration_key(text)``; the text is kept so the hub can quote it."""

    section: Literal["work_experience", "projects"] = "work_experience"
    entry_id: str
    key: str
    text: str
    aspiration: bool
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AspirationAnswer(BaseModel):
    """B-1 — the candidate's answer for one asked bullet: ``achieved`` (with
    what came out, written as an achievement in their words) or ``goal``."""

    entry_id: str
    key: str
    answer: Literal["achieved", "goal"]
    answered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OrgFigureVerdict(BaseModel):
    """FIG-2 — one figure-carrying bullet and the model's judgement whether it
    carries an organisation-scale figure. ``key`` is the bullet's key
    (``aspiration_key(text)``); ``figure`` the figure as the bullet writes it
    and ``figure_key`` its canonical key, both ``None`` for a negative."""

    section: Literal["work_experience", "projects"] = "work_experience"
    entry_id: str
    key: str
    text: str
    figure: str | None = None
    figure_key: str | None = None
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


ProfileMetadata.model_rebuild()


class ProfileMetaBlock(BaseModel):
    """#505 — the profile JSON's ``_meta`` sidecar block.

    Distinct from :class:`ProfileMetadata` (the ``metadata`` key). ``_meta``
    carries the candidate's own suppressions: ``na_fields`` lists completeness
    gap strings the candidate marked "not applicable"
    (``routers/profile_enrich.mark_gap_na`` writes it;
    ``services/profile/completeness.field_gaps`` and ``services/profile/health``
    read it off the raw JSON).

    Declared verbatim — no policy change, no key rename (the ``_meta`` /
    ``metadata`` near-collision is tracked separately in #509). ``extra="allow"``
    so any future ``_meta`` key survives a round-trip untouched.
    """
    model_config = ConfigDict(extra="allow")

    na_fields: list[str] = Field(default_factory=list)


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
        # ONE predicate with the gap agenda (#675 line 42 / ruling V-1) — the two
        # readers of this fact disagreed for as long as both existed, and the
        # agenda was the one that was wrong.
        from applire.services.profile.completeness import summary_present

        return summary_present(value)
    return bool(value)


# ─── Master profile data ──────────────────────────────────────────────────────

class MasterProfileData(BaseModel):
    # #505 — `_meta` is the only aliased field; populate_by_name keeps
    # `MasterProfileData(meta=...)` working from python instead of silently
    # dropping the block.
    model_config = ConfigDict(populate_by_name=True)

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
    #: D-B3V7 (rulings 787:L3 + V-4) — notice period / earliest start.
    availability: Availability | None = None
    metadata: ProfileMetadata | None = None
    # #505 — the raw JSON's `_meta` sidecar (candidate N/A suppressions). Named
    # `meta` here only because pydantic v2 reserves leading-underscore attribute
    # names for private attrs; the alias keeps the persisted key literally
    # `_meta`, which is what the readers index by. NOT `metadata` — see
    # ProfileMetaBlock. Undeclared, this key was silently dropped on every
    # model_validate() → model_dump() round-trip (#505).
    meta: ProfileMetaBlock | None = Field(default=None, alias="_meta")

    @field_validator("professional_summary", mode="before")
    @classmethod
    def _legacy_string_summary(cls, v: object) -> object:
        """A pre-#178 row holds the summary as a plain string; it crashed the
        read model (GET 500, and the 409 stale-recovery path with it —
        adversarial finding 2026-08-26). Load it into the DE slot: DACH-first
        product, German-first era; the summary editor shows both slots, so a
        mis-slotted legacy text is one move away, never lost."""
        if isinstance(v, str):
            text = v.strip()
            return {"de": text or None, "en": None}
        return v

    @model_serializer(mode="wrap")
    def _serialize_meta_under_its_json_key(self, handler: Any) -> Any:
        """Emit `meta` as `_meta`, and only when the profile actually has one.

        `model_dump()` defaults to `by_alias=False` and every persistence call
        site relies on that default, so the rename happens here rather than via
        `serialize_by_alias`. Absent `_meta` is omitted entirely — a profile
        that never had the key must not grow a null one.
        """
        data = handler(self)
        if not isinstance(data, dict):
            return data
        block = data.pop("meta", None)
        if "_meta" in data:  # dumped with by_alias=True
            block = data.pop("_meta")
        if block is not None:
            data["_meta"] = block
        return data

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
                    skills.append({"name": s, "category": "technical"})
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


# ─── The manually editable section vocabulary ─────────────────────────────────
#
# The sections a human may replace wholesale through the PATCH intake. This is
# the SAME set `services/profile` has enforced since the endpoint existed; it
# lives here (a leaf module both the op vocabulary and the service import) so
# the `ReplaceSection` op can validate against it without an import cycle —
# ADR-063 clause 8(e) / #480 PR 3.
#
# What is NOT in it is the load-bearing half:
#
# * `metadata` — `denied_concepts`, `enrichment_history`, `pending_*`. Reaching
#   it through a section replace would let one edit release a persisted denial
#   (ADR-059) or rewrite its own audit trail. `metadata.*` is reachable by NO
#   op at all; it is written only by the committer-owned invariant path
#   (ADR-063 amended 2026-08-09 clause 1).
# * `_meta` — the candidate's N/A suppressions (#505); written by its own
#   writer, and `SetProfileMeta` (PR 7) is the op that will reach it.
# * `projects` — WAS excluded here ("never had a manual editor; unchanged,
#   deliberately") as #480 PR 3 scope discipline, not as a design reason;
#   ADR-063 amended 2026-08-25 (E055/US292) widens the set by that one list
#   section — natural key + persisted id were already declared.
VAULT_SECTIONS: frozenset[str] = frozenset(
    {
        "personal_info",
        "professional_summary",
        "work_experience",
        "education",
        "certifications",
        "skills",
        "languages",
        "publications",
        "volunteer_activities",
        # ADR-063 amended 2026-08-25 (E055/US292) — first manual editor
        "projects",
        # ADR-055 — stories are list-shaped (replace semantics like other lists)
        "signature_stories",
    }
)

# #178: object-shaped sections take merge-patch semantics (RFC-7386 style) — a
# partial dict must never wipe unsupplied fields; Pydantic re-validation would
# re-default every omitted key ("" / null) and the JSONB write makes that
# permanent (no snapshot on this path). Every other section is a list and is
# replaced wholesale, which is what both doors document.
OBJECT_SECTIONS: frozenset[str] = frozenset({"personal_info", "professional_summary"})

#: `personal_info` fields ANOTHER WRITER owns — no vault door may write them and
#: none may raise a dispute about them, because the candidate is not the party who
#: would answer it. `photo_url` belongs to the photo endpoints
#: (`services/photo.py`: upload records the GDPR consent, delete removes the
#: stored file); a foreign URL there ends up in the CV's `<img src>`, rendered by
#: headless Chromium.
#:
#: ONE constant because it is ONE rule (ADR-066 cl. 2). It was stated twice, in
#: two vocabularies — `field_edit.py` RAISED on the section door and
#: `reconcile/apply.py` SKIPPED on the import door — which is the shape that
#: drifts silently: adding a second user-managed field would have protected one
#: door and not the other, and the two behaviours (refuse vs. drop) are
#: deliberately different, so nothing would have looked wrong. Vault collector
#: #674, PR #663 (D).
#:
#: `photo_crop` (#781, ruling F-2) is the framing OF that photo and has the same
#: one writer: an import that carried a stale framing over a new photo, or an
#: undo that rolled it back onto a later upload, would frame the wrong image.
USER_MANAGED_PERSONAL_INFO_FIELDS: frozenset[str] = frozenset({"photo_url", "photo_crop"})


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
    #: D-B3V7 (ruling V-4) — the dashboard reminder's condition:
    #: ``services/profile/availability.availability_reminder``.
    availability_reminder: Literal["missing", "stale"] | None = None


#: #615 (ADR-063 amended 2026-08-28, second entry) — "the merge ran AND
#: nothing is known to be missing" vs "the merge ran but the carried-predicate
#: names entries it did not carry". Shared across every import door's response
#: shape so the vocabulary cannot drift per adapter (ADR-058 cl. 2 parity).
ImportMergeStatus = Literal["applied", "partial"]


class ProfileImportResponse(MasterProfileResponse):
    """LinkedIn/XING/text import read model — #615 (returned by the import service;
    the async job maps it onto ``CVUploadResponse``; the sync ``POST /api/profile/import``
    that served it is retired, ruling V-4 = A).

    A SEPARATE subclass, not a field added to :class:`MasterProfileResponse`
    itself: that class is also `GET /api/profile` (a plain read, no merge in
    scope) and `PATCH /{section}` (a manual field edit, not a merge) —
    refuter B, MAJOR 1. Neither of those doors can honestly say
    ``merge_status``, so the field lives only where an import call site
    builds this subclass directly (never derived from persisted history,
    which would echo a stale import status on an unrelated later read).
    """

    merge_status: ImportMergeStatus = "applied"
    not_applied: list[ImportNotApplied] = Field(default_factory=list)
    #: F-7 (#674, founder edge UAT 2026-09-20) — the count of `not_applied`
    #: items that are :func:`is_loss_reason`, i.e. the same figure
    #: `merge_status` derives `partial` from. A NEW field rather than a filter
    #: on `not_applied`: the receipt keeps every fact it always has (a
    #: not-loss item is still worth knowing about), and a reader that needs
    #: "how many are actually missing" gets it without re-deriving the set
    #: itself. Optional so a `CVUploadResponse` persisted (as a background
    #: import job's `result`) before this field existed loads unchanged.
    not_applied_loss_count: int | None = None
    #: #674 line 72 (#707/#708, ADR-046 / ADR-063 door parity) — the other half
    #: of the same merge's honesty. `not_applied` names entries the merge's ops
    #: did not carry; `matched` names the ones the reconciler recognised as
    #: already present under another surface form, which is the answer to "why
    #: is my translated entry not in `changes`?". It rode `ApplyResult.matched`
    #: → `EnrichmentRecord.matched` to the profile page's history and stopped
    #: there, so no import DOOR ever stated it.
    matched: list[MatchReceipt] = Field(default_factory=list)
    #: D-B3V6 (ruling V-3 = A) — second-source bullets held back as restatements.
    restated: list[RestatedReceipt] = Field(default_factory=list)
    kept_separate: list[KeptSeparateReceipt] = Field(default_factory=list)  # FS-6
    #: 783:L41 (ADR-021 amended 2026-10-20) — the extraction review outcome of
    #: this import; see :class:`ExtractionReview`.
    extraction_review: ExtractionReview | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_truncated(self) -> SourceTruncation | None:
        """R3-2 = B: how much of an over-long source this import read (``None``
        when nothing was cut) — derived from ``extraction_review``."""
        return source_truncation_of(self.extraction_review)
    # #367 (2026-09-13, ruling V-2) — the same number as `completeness`, under the
    # name every import caller already reads. `ProfileImportView.tsx` posts a
    # LinkedIn/XING export to this door and then reads `data.completeness_score`
    # (the CVUploadResponse spelling); it has been reading `undefined` since the
    # ZIP branch was written. Additive alias, never a second computation.
    completeness_score: float | None = None


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
    # ADR-099 cl. 11 (Hazelnut build 5): + "keep_both" (only where the dispute
    # offers it) and "merge" (bullet lists; ``value`` = the merged sentence,
    # re-verified — a sentence that fails is written as "manual", RULING A1-1).
    resolution: Literal["existing", "incoming", "manual", "keep_both", "merge"]
    value: Any | None = None


class MergeProposalResponse(BaseModel):
    """ADR-099 cl. 11 — "Zusammenführen": the model's one sentence, or ``None``
    when it failed the fact-presence check (then only the other outcomes are
    offered). ``sources`` = [existing, incoming], shown beside the proposal."""

    proposal: str | None = None
    verifier: Literal["passed", "failed"]
    sources: list[str]


class ConflictSummary(BaseModel):
    """Lightweight conflict summary for CVUploadResponse (avoids leaking full values)."""
    conflict_id: str
    section: str
    field: str
    source: str


class CVUploadResponse(BaseModel):
    """Result of an import job (``GET /api/profile/import-jobs/{id}`` → ``result``;
    the retired sync ``POST /api/profile/upload`` returned it directly) (ADR 014).

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
    gate: Literal["none", "not_a_cv", "name_divergence", "review_not_run"] = "none"
    account_name: str | None = None     # existing profile's name (divergence prompt)
    cv_name: str | None = None          # uploaded CV's name (divergence prompt)
    staged_id: uuid.UUID | None = None  # parked upload row to resolve (merge/discard)
    # #615 (ADR-063 amended 2026-08-28) — the carried-predicate fact, on both
    # the sync door and the async job's `result` (CVImportStatusResponse wraps
    # this same class, so no separate adapter is needed there). Defaults keep
    # a GATED response (nothing committed) honestly "applied, []".
    merge_status: ImportMergeStatus = "applied"
    not_applied: list[ImportNotApplied] = Field(default_factory=list)
    # #717 (ADR-063 amended 2026-10-07) — the merge's recognised pairs, the
    # same receipt `ProfileImportResponse.matched` carries (2026-09-18).
    matched: list[MatchReceipt] = Field(default_factory=list)
    #: D-B3V6 (ruling V-3 = A) — second-source bullets held back as restatements.
    restated: list[RestatedReceipt] = Field(default_factory=list)
    kept_separate: list[KeptSeparateReceipt] = Field(default_factory=list)  # FS-6
    #: F-7 (#674) — see `ProfileImportResponse.not_applied_loss_count`. Optional
    #: for the same reason: this class is persisted verbatim as a background
    #: import job's `result` (`CVImportStatusResponse`), and a job stored
    #: before this field existed must load unchanged.
    not_applied_loss_count: int | None = None
    #: 783:L41 (ADR-021 amended 2026-10-20) — how the extraction review of THIS
    #: import settled (see :class:`ExtractionReview`). ``None`` on a held
    #: (GATED) upload and on a job result stored before the field existed.
    extraction_review: ExtractionReview | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_truncated(self) -> SourceTruncation | None:
        """R3-2 = B: how much of an over-long source this import read (``None``
        when nothing was cut) — derived from ``extraction_review``."""
        return source_truncation_of(self.extraction_review)


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
    # #615 (ADR-063 amended 2026-08-28) — set only when action == "merge"; a
    # "discard" resolves nothing, so "applied, []" (the defaults) is honest.
    merge_status: ImportMergeStatus = "applied"
    not_applied: list[ImportNotApplied] = Field(default_factory=list)
    # #717 (ADR-063 amended 2026-10-07) — the merge's recognised pairs, the
    # same receipt `ProfileImportResponse.matched` carries (2026-09-18).
    matched: list[MatchReceipt] = Field(default_factory=list)
    #: D-B3V6 (ruling V-3 = A) — second-source bullets held back as restatements.
    restated: list[RestatedReceipt] = Field(default_factory=list)
    kept_separate: list[KeptSeparateReceipt] = Field(default_factory=list)  # FS-6
    #: F-7 (#674) — see `ProfileImportResponse.not_applied_loss_count`.
    not_applied_loss_count: int | None = None


class ResolveConfirmationRequest(BaseModel):
    """G-5 / ruling V-5 (c) — the candidate answers a parked confirmation from
    the Health hub: ``chosen_option`` is one of the ask's plain ``options``."""

    model_config = ConfigDict(extra="forbid")

    confirmation_id: str = Field(min_length=1, max_length=200)
    chosen_option: str = Field(min_length=1, max_length=1000)


class WithdrawDenialRequest(BaseModel):
    """G-5 (ADR-059 amended 2026-10-24) — the denial the candidate withdraws,
    named exactly as My Profile lists it (``metadata.denied_concepts[].concept``)."""

    model_config = ConfigDict(extra="forbid")

    concept: str = Field(min_length=1, max_length=500)


class HealthUnitRequest(BaseModel):
    """UAT-3d — ``POST /api/profile/health/unit``: the currency for one work
    entry's budget figure that states none."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1, max_length=200)
    unit: Literal["EUR", "USD", "CHF", "GBP"]


class AspirationAnswerRequest(BaseModel):
    """B-1 — ``POST /api/profile/aspirations/answer``: the candidate's answer to
    "Ist das erreicht?" for one ``aspiration:<entry id>:<key>`` issue. ``achieved``
    requires ``outcome`` — what came out, in the candidate's own words."""

    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1, max_length=300)
    achieved: bool
    outcome: str | None = Field(default=None, max_length=500)


class OrgFigureAnswerRequest(BaseModel):
    """FIG-2 — ``POST /api/profile/org-figures/answer``: the candidate's answer to
    "Gehört „<figure>“ in Deine Unterlagen?" for one ``org_figure:<entry id>:<key>``
    issue. ``include`` requires ``relation`` — the candidate's own words, stored
    verbatim."""

    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1, max_length=300)
    include: bool
    relation: str | None = Field(default=None, max_length=500)


class AdoptNotAppliedRequest(BaseModel):
    """UAT-3d — ``POST /api/profile/not-applied/adopt``: one receipt item of the
    hub's ``not_applied:<record id>`` issue, taken into the vault."""

    model_config = ConfigDict(extra="forbid")

    issue_id: str = Field(min_length=1, max_length=300)
    item_key: str = Field(min_length=1, max_length=300)


class SeparateMatchRequest(BaseModel):
    """#717 — "Nicht dasselbe": the recognised pair to undo, named as the
    import summary shows it (ADR-063 amended 2026-10-07, ruling V-2 = A)."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    incoming: str = Field(min_length=1)


class UndoLastMergeRequest(BaseModel):
    """ADV-8 — optional basis for POST /api/profile/undo-last-merge: the
    profile's ``updated_at`` right after the merge the client means. Omitted
    (old clients, MCP) = today's behaviour."""

    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime | None = None


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
    # ``confirmation`` (#333) is its own thread because it resolves differently
    # from a ``conflict``: not a 2-value existing/incoming pick, but the parked
    # question + its options, which the profile-review interview already walks.
    # ``unit`` (#382, PO decision 2026-08-08) is its own thread because nothing
    # about it is a mismatch: the stored value is exactly what the candidate
    # said, and only the unit that would let a document state it is missing.
    # Option A omits such a value from every delivered document; this thread is
    # the standing condition on that omission — it must reach the user.
    # ``not_applied`` (#684 / founder ruling V-6, 2026-09-09) is its own thread
    # because nothing about it is a mismatch OR a decision the candidate owes:
    # something they submitted did not reach the vault, and the honest act is to
    # say so and say WHY. It must never be counted as a decision (the gaps
    # page's popup stack filters on `conflict`/`confirmation` for that reason) —
    # there is nothing here to pick between.
    # ``span_provenance`` (#764, ADR-061 cl. 7 amended 2026-10-16) is its own
    # thread because it is neither a mismatch nor a lost item: a skill span is
    # labelled as the candidate's own statement although no source text states
    # it and it equals the role-date derivation — the fingerprint of a span
    # laundered across the import seam before the 2026-10-07 fix. It carries
    # its own one-click action (recompute), never a decision to pick between.
    # ``suspect_binding`` (FS-5, Hazelnut build 4, ADR-046 amended 2026-10-24):
    # a skill pair an import combined across technical↔soft (the #801 guard's
    # category test, run read-only over the recorded receipts) — one question
    # "Ist A dasselbe wie B?" with the existing Ja / Nein-getrennt acts.
    thread: Literal[
        "conflict", "accuracy", "confirmation", "unit", "not_applied", "span_provenance",
        "suspect_binding", "duplicate_entry", "aspiration", "org_figure",
    ]
    profile_mismatch_severity: Literal["info", "review", "critical"]
    # A server-built, English-only fallback (kept for any consumer #626
    # (conflict legibility) could not reach; every updated reader composes its
    # own localized sentence from the structured fields below instead, so it
    # never shows this raw string). For a ``conflict`` issue it is still
    # improved to carry the entity label when one resolves — never worse than
    # before, but not localizable, which is exactly why it is no longer the
    # primary contract.
    summary: str
    field_ref: str | None = None
    source_record_ref: str | None = None
    # ADR-099 cl. 2/11 (UAT-2 fix round): the delivery class of this issue's
    # kind from ``services.finding_registry`` ("A" one action, "S" not shown,
    # …) and the one action's resolver key. None = unclassified (a reader must
    # not treat it as "S").
    delivery_class: Literal["F", "A", "U", "S"] | None = None
    resolver: str | None = None
    # ── founder ruling V-7 (2026-09-09) — structured fields for the
    #    ``not_applied`` thread, so the reader composes the sentence in the
    #    candidate's language instead of rendering the server's English
    #    ``summary``. Same doctrine as #626's conflict fields below and as the
    #    ``unit`` thread's own key: ``summary`` stays as the fallback, and every
    #    updated reader ignores it. `applire-i18n` makes an untranslated
    #    user-facing sentence a defect, and the DE screenshot showed this one
    #    sitting directly beneath a fully-German conflict card.
    #    ``None`` on every other thread.
    not_applied_count: int | None = None
    #: The ``EnrichmentRecord.source`` KEY (``interview`` / ``cv_upload`` / …),
    #: which the frontend already localises through ``profile.sources.*``
    #: (``lib/enrichment-sources.ts``) — never a rendered word.
    not_applied_source: str | None = None
    #: The raw ``ImportNotApplied.reason`` keys, localised by the reader through
    #: ``health.notAppliedReason.*``. Never a sentence.
    not_applied_reasons: list[str] | None = None
    #: The items' own labels, capped at three. A natural key ("Universität
    #: Stuttgart / M.Sc.") is DATA and passes through; a ``professional_summary``
    #: item's label is the language slot (``"de"``/``"en"``), which the reader
    #: maps through the same ``health.fieldLabel.summaryDe/summaryEn`` the
    #: dispute surface uses — one name for one thing.
    not_applied_labels: list[str] | None = None
    #: #705 (founder UAT, 2026-09-15) — the FULL receipt, every item, never
    #: capped: the ``labels``/``reasons`` pair above lets a reader compose a
    #: short sentence but caps at three items and loses the per-item
    #: section↔label↔reason pairing (a merge with items in five sections could
    #: not say which reason applied to which item). Sorted by ``section`` (then
    #: ``label``) so a grouped reader renders sections in a stable order. The
    #: reported defect: the overlay's single action "just randomly moves to
    #: one of the affected sections" because nothing told the user WHICH items
    #: landed where — additive alongside ``not_applied_labels``/``_reasons``,
    #: which keep the V-7 hub sentence working unchanged. Reuses
    #: ``ImportNotApplied`` itself (same fact, no transformation) rather than a
    #: parallel shape.
    not_applied_items: list[ImportNotApplied] | None = None
    # ── #626 (conflict legibility) — structured fields, populated for the
    # ``conflict`` thread only (every other thread leaves them ``None`` and its
    # existing reader is unaffected). The reported defect: a conflict's summary
    # named the FIELD ("work_experience.end_date: '2019-12' vs '2020-01'") but
    # never the ENTRY it hangs off — the user could not tell which job was in
    # dispute. ``Conflict.entity_id`` (#218) already carried the answer; nothing
    # resolved it. See ``services/profile/health.py`` (``_resolve_entity`` /
    # ``_entity_label``) for the resolution and ADR notes on what could NOT be
    # recovered (the existing side's provenance; source CV excerpts).
    #
    # ``entity_label``   human label of the entry in dispute ("Senior Developer
    #                    @ Acme Corp"), or ``None`` for a profile-level dispute
    #                    (``entity_id`` was never set — e.g. professional_summary
    #                    / personal_info) OR a stale ``entity_id`` that no
    #                    longer resolves against the current profile (the entry
    #                    was edited/removed after the conflict was parked;
    #                    nothing sweeps ``pending_conflicts`` when that happens).
    #                    Both cases degrade to ``None`` rather than crashing or
    #                    inventing a label.
    # ``section``        machine section key (``conflict.section`` verbatim,
    #                    e.g. ``"work_experience"``) — the frontend maps this
    #                    through its own translated section-name dictionary.
    # ``field``          machine field key (``conflict.field`` verbatim). For a
    #                    ``professional_summary`` conflict this is the language
    #                    slot ("de"/"en"), not a real field name — the frontend
    #                    special-cases that section.
    # ``existing_value_display`` / ``incoming_value_display``
    #                    the two disputed values, already run through
    #                    ``format_display_value`` (never a raw Python repr).
    # ``existing_source`` / ``incoming_source``
    #                    provenance for each side. ``conflict.source`` names
    #                    only the import that RAISED the conflict — i.e. the
    #                    INCOMING side. The ``Conflict`` record has no symmetric
    #                    field for who/what wrote the EXISTING value, and
    #                    reconstructing it from ``enrichment_history`` would mean
    #                    walking the ``FieldChange`` trail backwards with no
    #                    stored pointer to anchor on — an inference, not a
    #                    stored fact. ``existing_source`` is therefore always
    #                    ``None`` today; left unset rather than fabricated.
    entity_label: str | None = None
    section: str | None = None
    field: str | None = None
    existing_value_display: str | None = None
    incoming_value_display: str | None = None
    existing_source: str | None = None
    incoming_source: str | None = None
    # ── #764 — structured fields for the ``span_provenance`` thread, so the
    #    reader composes the sentence in the candidate's language (the V-7
    #    doctrine above). ``skill_id`` is what the recompute action addresses;
    #    ``span_years`` is the stored (transcribed) value, ``recomputed_years``
    #    what phase 1's arithmetic gives today. ``None`` on every other thread.
    skill_id: str | None = None
    skill_name: str | None = None
    #: FS-5 — ``suspect_binding`` only: the name the import combined INTO
    #: ``skill_name`` (the receipt's ``incoming``).
    incoming_name: str | None = None
    #: G-5 / #604 — ``confirmation`` only: the parked question per language
    #: (``PendingConfirmation.question_i18n``), so the panel asks it in the
    #: reader's language instead of the English ``summary``. Null for a record
    #: persisted before #669.
    question_i18n: dict[str, str] | None = None
    #: G-5 / ruling V-5 (c) — ``confirmation`` only: the parked ask's id, and
    #: for a family the panel answers IN PLACE (``denial_scope``: the answer is
    #: the act, no second dialog) its options, plain and per language, posted
    #: back as ``POST /api/profile/confirmations/resolve``. Null options = the
    #: family is answered in the profile-review interview ("Beheben").
    confirmation_id: str | None = None
    confirmation_kind: str | None = None
    options: list[str] | None = None
    options_i18n: list[dict[str, str]] | None = None
    span_years: int | None = None
    recomputed_years: int | None = None
    #: UAT-3d — ``conflict`` only: the closed outcome list the dispute allows
    #: (``decisions.conflict_shape``), e.g. ``keep_current`` / ``take_new`` /
    #: ``keep_both`` / ``merge``.
    outcomes: list[str] | None = None
    #: UAT-3d — ``unit`` only: the stored budget wording as written, and the id
    #: of the work entry it sits on (the one-action unit input addresses it).
    budget_value: str | None = None
    entry_id: str | None = None
    #: UAT-3d — ``duplicate_entry`` only: [target, other] entry ids of the
    #: recognised pair, and the other entry's label (``entity_label`` is the
    #: target's).
    entry_ids: list[str] | None = None
    other_entity_label: str | None = None
    #: B-1 — ``aspiration`` only: the bullet as written (the candidate's own
    #: words), asked as "Ist das erreicht?"; ``entry_id`` names its entry.
    aspiration_text: str | None = None
    #: FIG-2 — ``org_figure`` only: the figure as the bullet writes it, the
    #: bullet, and the entry label ("Role @ Company" / the project name).
    org_figure: str | None = None
    org_figure_text: str | None = None
    org_figure_entry: str | None = None


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
    # ADR-099 / UAT-3d: A/complete_section when ``field_gaps`` is non-empty,
    # else S/None (a section-level gap alone is a nudge, not an action).
    delivery_class: Literal["F", "A", "U", "S"] | None = None
    resolver: str | None = None


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
