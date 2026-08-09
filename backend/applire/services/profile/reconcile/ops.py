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

from applire.schemas.profile import FieldChange, MasterProfileData


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


class DemoteSkill(BaseModel):
    """ADR-063 clause 8(e) (amended 2026-08-08) — the candidate retracts a skill
    their vault holds as ``confirmed``; its ``status`` moves to ``denied``
    (ADR-061 amended 2026-08-08, #485).

    **Mark, don't delete.** The entry stays in the vault with its name,
    provenance and history — the record that the skill was once claimed is
    preserved per ADR-059's correction-is-a-new-fact rule — and the applier
    receipts the move like any other write.

    **Never emitted by the reconciler LLM.** Demotion is an *assert*-class act
    (it writes a negative statement about the candidate), so its trigger is a
    deterministic read of the model's own atomic ``denials`` declarations
    against the persisted vault — a fact, per ADR-062 clause 1 — computed by
    ``stance.demote_ops_for_denials`` inside the shared reconcile core. That is
    also why this ships as its own op rather than as ``UpsertSkill(status=
    "denied")``, the parametrised form ADR-063 leaves open: widening the
    upsert's status Literal would put ``denied`` in the *model's* vocabulary,
    where one hallucinated field could mint a denial nobody testified to. Ops
    the model emits are validated against this union, so an ``upsert_skill``
    carrying ``status: "denied"`` still fails validation and is dropped —
    fail-closed.

    ``name`` is the PERSISTED entry's own name, never the declared term, so the
    applier matches exactly and a demotion can never widen by containment.
    ``declared_denial`` records WHICH declared term demoted it (longest match
    first) for the receipt's provenance.
    """

    op: Literal["demote_skill"] = "demote_skill"
    name: str
    declared_denial: str | None = None


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


class ApplyImportMerge(BaseModel):
    """The import path's whole-merge act — ADAPTER-ONLY, IMPORT-ONLY.

    ADR-063 amended 2026-08-09 (second entry) clause 1, ruled after #480 PR 2's
    code contact refuted the design's assumption that the import writers were
    op-expressible. They are not, and cannot be made so:

    * ``reconcile_import`` returns a FINISHED merged profile, not ops — its
      segmented fallback (ADR-047) folds N reconcile calls into one accumulated
      result and keeps no single applicable batch;
    * two of its deterministic post-passes write **computed provenance** —
      ``_union_certifications`` (#190) and ``_carry_skill_enrichment`` (#327,
      which sets ``skills[].years_experience`` / ``source``). ADR-062 reserves
      computed provenance for code, which is exactly WHY the model-emittable
      ``UpsertSkill`` deliberately carries neither field. No sequence of the
      reconciler's ops can reproduce an import.

    So the act itself becomes the op: this carries the bridge's computed merged
    profile plus the receipts (``changes``) and the US161 merge statistics
    (``reconciliation``) only the intake can compute, and the applier installs
    it wholesale. Three properties make that safe:

    1. **Model-unemittable by construction.** It lives in ``DecisionOp``, and
       ``engine._parse_ops`` validates raw model JSON against ``ReconcileOp``
       alone — a hallucinated ``{"op": "apply_import_merge", …}`` is dropped
       before it is ever an object (regression-pinned, exactly as ``DemoteSkill``
       is since PR 1).
    2. **ADR-062 is satisfied**: deterministic code computed every field of
       ``merged``; no LLM ever emits this shape.
    3. **It is not the laundering shape PR 3 closes.** Laundering is *hand-typed
       document text* reaching the vault unguarded. This is the trusted
       deterministic merge whose output ALREADY was the persisted state before
       PR 2 — now with the committer's invariant set, the ADR-042 snapshot
       parameter and the ADR-063 clause-6 write token around it.

    Rejected alternative (recorded so it is not re-proposed): a
    ``profile_override`` parameter on ``commit_ops``. Same effect, but it would
    break the "``apply_ops`` is the only path" claim as a sanctioned BYPASS
    rather than as a typed, auditable act inside the vocabulary.

    Deliberately powerful, therefore deliberately narrow: import intakes only.
    """

    op: Literal["apply_import_merge"] = "apply_import_merge"
    #: The finished merged profile. Installed wholesale — the applier does not
    #: re-decide any of it.
    merged: MasterProfileData
    #: The merge's per-decision receipts (``MergeResult.changes``, or the
    #: summary fallback ``_enrichment_from_merge`` substitutes when the merge
    #: produced no structured change).
    changes: list[FieldChange] = Field(default_factory=list)
    #: US161 (ADR-041 amended) — per-entity {extracted, stored, delta}, captured
    #: at merge time so silent data loss (FMEA JF-M-3.3) stays detectable on the
    #: profile-health surface. Merge records only; the committer copies it onto
    #: the ``EnrichmentRecord`` it mints because no other intake can compute it.
    reconciliation: dict[str, dict[str, int]] | None = None


# ── Discriminated unions, split by EMITTER ────────────────────────────────────
#
# ADR-063 (amended 2026-08-09) clause 1 — two unions, and the boundary between
# them is *who constructs the op*:
#
#   ``ReconcileOp``  ops the reconciler LLM may emit. This is the union raw
#                    model JSON is validated against (``engine._parse_ops``), so
#                    anything NOT in it is dropped as a hallucination.
#   ``DecisionOp``   adapter-only ops. Never parsed from model output; only ever
#                    constructed as typed objects by deterministic code.
#   ``CommitOp``     the committer's / applier's vocabulary — the union of both.
#
# Governing rule: **never widen an op the model can emit with a more powerful
# parameter**, and never leave an adapter-only op inside the model's union.
# ``DemoteSkill`` violated the second half on `main`: it sat in ``ReconcileOp``
# while its own docstring said the model never emits it, so a hallucinated
# ``{"op": "demote_skill", …}`` passed validation and demoted a real, attested
# skill to ``denied`` — a negative statement about the candidate nobody
# testified to (proposed FMEA row SF-VAULT.10, #480 PR 1). It now lives in
# ``DecisionOp``; ``stance.demote_ops_for_denials`` constructs it directly and
# is unaffected.
#
# ``ApplyImportMerge`` joined ``DecisionOp`` in PR 2 for the same reason and by
# the same rule: it is constructed only by the import writers, it is far more
# powerful than anything the model may say, and the split is what guarantees a
# hallucinated ``apply_import_merge`` can never reach the applier.

_MODEL_EMITTABLE = (
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
)

# Adapter-only ops. PR 3+ adds ``ReplaceSection``/``ResolveField``/
# ``ResolveConfirmation``/``CloseRole``/``SetProfileMeta`` here.
_ADAPTER_ONLY = (DemoteSkill, ApplyImportMerge)

ReconcileOp = Annotated[Union[_MODEL_EMITTABLE], Field(discriminator="op")]

DecisionOp = Annotated[Union[_ADAPTER_ONLY], Field(discriminator="op")]

CommitOp = Annotated[
    Union[_MODEL_EMITTABLE + _ADAPTER_ONLY], Field(discriminator="op")
]


class ReconcileResult(BaseModel):
    """The reconcile ENGINE's output: ordered ops + a parallel ambiguity list.

    Ambiguities may be surfaced both inline (a ``RequestConfirmation`` in
    ``ops``) and here; both are kept. The engine folds ``ambiguities`` into the
    applier's ``pending_confirmations`` at the call site (``apply_ops`` itself
    only consumes ``ops``).

    ``ops`` is typed ``CommitOp``, not ``ReconcileOp``: this envelope is what
    the engine hands the committer *after* the stance/attribution guards and the
    deterministic ``demote_skill`` emitter have run, so it legitimately carries
    adapter-only ops. The model-validated boundary is ``engine._parse_ops``,
    which uses the narrower ``ReconcileOp`` — never this class.
    """

    ops: list[CommitOp] = Field(default_factory=list)
    ambiguities: list[RequestConfirmation] = Field(default_factory=list)
    # Tokens the new information explicitly DENIES experience with (#127). The
    # stance guard strips any op content matching these — the model's own
    # denial verdict outranks its ops (never-claim beats claim, ADR-040).
    denials: list[str] = Field(default_factory=list)
