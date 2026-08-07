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

"""ADR-046 — the typed reconciliation op vocabulary.

The LLM reconciler emits a ``ReconcileResult`` (a discriminated list of these
ops); the deterministic applier (``apply.py``) folds them into the Master
Profile. Each *entity* op carries:

- ``ref``    — a LOCAL handle ("w1", "p1") so later ops in the same batch can
               target a just-created entity before it has a real id.
- ``target`` — an EXISTING entity id to merge into (``None`` = create new).

References inside ops (``target`` / ``parent`` / ``evidence``) are resolved by
the applier against the batch's ref-map first, then against existing entity ids.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


# ── Entity ops ────────────────────────────────────────────────────────────────


class UpsertWork(BaseModel):
    op: Literal["upsert_work"] = "upsert_work"
    ref: str
    target: str | None = None
    company: str
    role: str
    start_date: str | None = None
    end_date: str | None = None
    # #155 — tri-state current-position marker (None = unknown). True records
    # "ongoing role" while end_date stays null (the extraction convention).
    is_current: bool | None = None
    location: str | None = None
    team_size: int | None = None
    industry_context: str | None = None
    budget_managed: str | None = None


class UpsertProject(BaseModel):
    op: Literal["upsert_project"] = "upsert_project"
    ref: str
    target: str | None = None
    name: str
    # An existing work/volunteer id, OR a local ref, OR None (= standalone).
    parent: str | None = None
    role: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    url: str | None = None
    description: str | None = None


class UpsertVolunteer(BaseModel):
    op: Literal["upsert_volunteer"] = "upsert_volunteer"
    ref: str
    target: str | None = None
    organization: str
    role: str
    cause: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None


class AddBullets(BaseModel):
    op: Literal["add_bullets"] = "add_bullets"
    # An existing id OR a local ref of a work/project/volunteer entity.
    target: str
    responsibilities: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)


class UpsertSkill(BaseModel):
    op: Literal["upsert_skill"] = "upsert_skill"
    name: str
    category: str | None = None
    proficiency: str | None = None
    # Existing ids or local refs of experiences that demonstrate the skill.
    evidence: list[str] = Field(default_factory=list)
    # ADR-061 clause 3 — set by ``enforce_stance``, never by the reconciler LLM
    # itself. "confirmed": literal/alias grounding or a citation-verified LLM
    # adjudication. "unconfirmed": the testimony predicate could not confirm the
    # token (LLM said no/unclear, or adjudication failed) — the guard no longer
    # drops the op outright; the vault entity is written but never claimable.
    status: Literal["confirmed", "unconfirmed"] = "confirmed"


class UpsertCertification(BaseModel):
    op: Literal["upsert_certification"] = "upsert_certification"
    name: str
    issuing_organization: str | None = None
    date_obtained: str | None = None
    expiry_date: str | None = None
    credential_id: str | None = None
    credential_url: str | None = None
    status: Literal["confirmed", "unconfirmed"] = "confirmed"  # ADR-061 clause 3


class UpsertLanguage(BaseModel):
    op: Literal["upsert_language"] = "upsert_language"
    language: str
    level: str | None = None
    status: Literal["confirmed", "unconfirmed"] = "confirmed"  # ADR-061 clause 3


class UpsertEducation(BaseModel):
    op: Literal["upsert_education"] = "upsert_education"
    institution: str
    degree: str
    field: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    grade: str | None = None


class UpsertPublication(BaseModel):
    op: Literal["upsert_publication"] = "upsert_publication"
    title: str
    type: Literal["publication", "patent"] = "publication"
    venue: str | None = None
    published_date: str | None = None
    doi: str | None = None
    url: str | None = None
    patent_number: str | None = None
    co_authors: list[str] = Field(default_factory=list)


class UpsertStory(BaseModel):
    """ADR-055 — a signature story: challenge → mechanism → outcome → benchmark.

    Prompt rules only allow this when the SOURCE MATERIAL itself narrates the
    arc (never synthesized from a bare skill/tag). ``evidence`` references the
    experience the story happened in (existing ids or local refs), mirroring
    ``UpsertSkill.evidence``."""

    op: Literal["upsert_story"] = "upsert_story"
    title: str
    challenge: str
    mechanism: str
    outcome: str
    benchmark: str | None = None
    evidence: list[str] = Field(default_factory=list)


# ── Field / scalar ops ────────────────────────────────────────────────────────


class SetField(BaseModel):
    op: Literal["set_field"] = "set_field"
    target: str  # an existing id OR a local ref
    field: str
    value: Any = None


class SetPersonalInfo(BaseModel):
    op: Literal["set_personal_info"] = "set_personal_info"
    field: str
    value: Any = None


class SetSummary(BaseModel):
    op: Literal["set_summary"] = "set_summary"
    lang: Literal["de", "en"]
    text: str


# ── Conflict / confirmation ops ───────────────────────────────────────────────


class FlagConflict(BaseModel):
    """A two-value dispute the model raises instead of overwriting.

    ``field`` names either a SCALAR field of the ``target`` entity (``company``,
    ``end_date``, …) or — #218 — one of its BULLET LISTS (``responsibilities`` /
    ``achievements``), in which case ``existing``/``incoming`` carry the two
    contradicting bullet texts verbatim rather than field values. Both shapes
    land on the same ``Conflict`` channel; the applier attaches the resolved
    entity's id so the resolution endpoint can write back to that bullet.

    Deciding that two differently-worded bullets contradict is the reconciler
    model's judgement (ADR-062 clause 1) — no deterministic matcher may make it,
    which is why this is an op the model emits and not a post-pass.
    """

    op: Literal["flag_conflict"] = "flag_conflict"
    target: str
    field: str
    existing: Any = None
    incoming: Any = None


class RequestConfirmation(BaseModel):
    op: Literal["request_confirmation"] = "request_confirmation"
    question: str
    options: list[str] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)


# ── Discriminated union + result envelope ─────────────────────────────────────

ReconcileOp = Annotated[
    Union[
        UpsertWork,
        UpsertProject,
        UpsertVolunteer,
        AddBullets,
        UpsertSkill,
        UpsertCertification,
        UpsertLanguage,
        UpsertEducation,
        UpsertPublication,
        UpsertStory,
        SetField,
        SetPersonalInfo,
        SetSummary,
        FlagConflict,
        RequestConfirmation,
    ],
    Field(discriminator="op"),
]


class ReconcileResult(BaseModel):
    """The LLM reconciler's output: ordered ops + a parallel ambiguity list.

    Ambiguities may be surfaced both inline (a ``RequestConfirmation`` in
    ``ops``) and here; both are kept. The engine folds ``ambiguities`` into the
    applier's ``pending_confirmations`` at the call site (``apply_ops`` itself
    only consumes ``ops``).
    """

    ops: list[ReconcileOp] = Field(default_factory=list)
    ambiguities: list[RequestConfirmation] = Field(default_factory=list)
    # Tokens the new information explicitly DENIES experience with (#127). The
    # stance guard strips any op content matching these — the model's own
    # denial verdict outranks its ops (never-claim beats claim, ADR-040).
    denials: list[str] = Field(default_factory=list)
