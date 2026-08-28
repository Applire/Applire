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

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, field_validator

from applire.schemas.profile import (
    OBJECT_SECTIONS,
    VAULT_SECTIONS,
    FieldChange,
    MasterProfileData,
)


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


class ReplaceSection(BaseModel):
    """A human replaces one whole section of the vault — ADAPTER-ONLY.

    ADR-063 clause 8(e) / the 2026-08-09 amendment clause 1 (#480 design §4.1).
    This is the typed form of the act the PATCH intake has always performed, and
    routing it through the committer is how the CV section editor's remaining
    truthfulness exposure closes: the editor calls ``patch_profile_section``
    (#336), so once THIS is the op that intake emits, every manual document edit
    inherits the committer's invariant set transitively (FMEA SF-VAULT.4's write
    half; the read-side release corpus is PR 4).

    **Semantics — exactly today's PATCH contract, no policy moved:**

    * ``section`` in ``OBJECT_SECTIONS`` (``personal_info``,
      ``professional_summary``) takes RFC-7386-style **merge-patch** (#178):
      supplied keys win, an explicit ``null`` clears a field, an omitted key
      keeps its current value. A partial object must never wipe what it did not
      mention.
    * every other section is a list and is **replaced wholesale** — which is
      what both doors advertise ("always send the complete list").

    **Guarded by the section vocabulary.** ``section`` validates against
    ``VAULT_SECTIONS``, so ``metadata`` — ``denied_concepts``,
    ``enrichment_history``, the parked lists — is structurally unreachable: a
    section replace can never release a persisted denial or forge its own audit
    trail. That is the same reason ``SetProfileMeta`` (PR 7) carries a key enum
    rather than a free-form path.

    **Adapter-only, and that is what makes deletion safe.** It lives in
    ``DecisionOp``; ``engine._parse_ops`` validates raw model JSON against
    ``ReconcileOp`` alone, so a hallucinated ``{"op": "replace_section", …}`` is
    dropped before it is ever an object. The model can therefore never emit a
    deletion — only a human editing a section can (as they always could).

    **Deletions are diffed and receipted, not refused** (§7.7 ruling, ADR-063
    amended 2026-08-09 clause 8). Removal is already expressible through today's
    PATCH — this is the same capability with a per-entry receipt instead of one
    opaque blob, which is the defect it closes. Whether removal-shaped diffs
    should instead be refused and routed through an explicit confirmed
    ``RemoveEntry`` act is **deferred to Finetuner (#507)**; it is not
    re-argued here.
    """

    op: Literal["replace_section"] = "replace_section"
    section: str
    #: The section payload. ``Any`` because the sections are heterogeneous (a
    #: dict for the two object sections, a list of entry dicts otherwise); the
    #: applier round-trips it through ``MasterProfileData`` exactly as the PATCH
    #: intake always did, so schema validation is unchanged.
    value: Any = None
    #: The profile's `updated_at` as the GET the edit was composed against
    #: returned it (ADR-063 amended 2026-08-25, E055 / JF-F-H1.6). `None` —
    #: every pre-E055 caller — keeps last-write-wins. A value is compared by
    #: the COMMITTER against `record.updated_at` before any op is applied and
    #: refused with `StaleEditError` when the profile has moved. The applier
    #: never reads it: `apply_ops` stays pure. (Was `basis_digest`, carried and
    #: enforced by nothing; the product decision it deferred is now taken.)
    basis_updated_at: datetime | None = None

    @field_validator("section")
    @classmethod
    def _section_must_be_editable(cls, value: str) -> str:
        if value not in VAULT_SECTIONS:
            raise ValueError(
                f"Invalid section '{value}'. Valid: {sorted(VAULT_SECTIONS)}"
            )
        return value

    @property
    def is_object_section(self) -> bool:
        """Whether this section merge-patches (#178) instead of replacing."""
        return self.section in OBJECT_SECTIONS


class ResolveField(BaseModel):
    """The candidate answers a dispute the system raised — ADAPTER-ONLY.

    ADR-063 clause 8(e) / the 2026-08-09 amendment clause 1 (#480 design §4.2).
    This is the **authorised overwrite**: the one op allowed to write over a
    populated field, which ``_apply_set_field``'s fill-only rule exists to
    refuse. A reconciler that could overwrite would silently replace attested
    facts, so it may only FILL; a real disagreement is parked on the conflict
    channel and comes back here once the human has decided.

    **The load-bearing guard is not a flag on this op — it is the dispute.**
    ``conflict_id`` must resolve to an OPEN (unresolved) ``Conflict`` on the
    profile the applier is writing, and the op's ``section``/``field``/``target``
    must describe THAT dispute. Both halves matter:

    * without the open-conflict lookup, ``ResolveField`` degenerates into a
      free overwrite primitive that any future caller could reach for;
    * without the identity check, one open conflict about a role title would
      authorise an overwrite of an unrelated field — the authority is *this*
      dispute, not "a dispute exists".

    A resolved conflict is spent authority: answering the same dispute twice
    cannot authorise a second overwrite.

    ``metadata`` is refused outright, as it is for ``ReplaceSection`` — a
    dispute may never become a write to ``denied_concepts`` or
    ``enrichment_history``.

    **The winning value comes from the dispute record, not from this op.**
    ``resolution`` names which side won; ``value`` carries the candidate's own
    text and is read **only** for ``"manual"``. So an adapter cannot claim
    "incoming" while smuggling different content — for the two non-manual
    resolutions the applier reads ``existing_value``/``incoming_value`` off the
    conflict it just authenticated.

    **Adapter-only.** It lives in ``DecisionOp``; ``engine._parse_ops``
    validates raw model JSON against ``ReconcileOp`` alone, so a hallucinated
    ``{"op": "resolve_field", …}`` is dropped before it is ever an object — the
    same rule that keeps ``DemoteSkill`` and ``ReplaceSection`` out of the
    model's vocabulary. Only a human answering a question reaches this.

    Absorbs ``services.profile.resolve_conflict``, and with it the #218
    bullet-list surgery, which becomes unit-testable for the first time by
    moving into the applier.
    """

    op: Literal["resolve_field"] = "resolve_field"
    #: The dispute being answered. THE authority — see the class docstring.
    conflict_id: str
    #: The disputed entity's id for list sections (``None`` for object sections
    #: and for pre-#218 conflicts that carry no entity identity).
    target: str | None = None
    #: The conflict's own section. Not validated against ``VAULT_SECTIONS``:
    #: ``_apply_flag_conflict`` records ``""`` when the target did not resolve,
    #: and such a dispute must still be answerable (it writes nothing).
    section: str
    field: str
    #: The candidate's own text. Read only when ``resolution == "manual"``.
    value: Any = None
    resolution: Literal["existing", "incoming", "manual"]


class ResolveConfirmation(BaseModel):
    """The candidate answers a parked N-option confirmation — ADAPTER-ONLY.

    ADR-063 clause 8(e) / the 2026-08-09 amendment clause 1 (#480 design §4.5).
    Bookkeeping plus a receipt: the chosen option is recorded on the enrichment
    trail and the parked ask is cleared from
    ``metadata.pending_confirmations``, so no later session re-asks it.

    **Deliberately not folded into ``SetProfileMeta``.** That op may never
    reach ``metadata`` — its key enum is what stops a metadata write from ever
    being able to release a denial or forge an audit trail. This op's applier
    touches exactly one metadata list through the committer's controlled path
    and nothing else, which is a different (and much narrower) capability than
    "write a metadata key".

    It also completes the **park+clear lifecycle** #480 PR 2 could only half
    build: with a durable clear in the vocabulary, `commit_ops` parks every
    intake's asks unconditionally, and the interview's own in-session
    resolution (#187) clears the park through this op rather than by mutating
    the parked list in place.

    Adapter-only for the usual reason: a hallucinated
    ``{"op": "resolve_confirmation", …}`` would let the model close a question
    the candidate never answered.
    """

    op: Literal["resolve_confirmation"] = "resolve_confirmation"
    confirmation_id: str
    chosen_option: str


class AddRole(BaseModel):
    """The candidate started a new job — ADAPTER-ONLY.

    ADR-063 amended 2026-08-09 (third entry), ruled after #480 PR 6's code
    contact refuted the design's assumption that ``add_role`` was expressible
    with the reconciler's ``UpsertWork``. Two independent refutations, both
    reproduced before the ruling:

    * **ordering.** ``_apply_upsert_work`` APPENDS; the post-hire intake has
      always inserted at index 0 — and nothing in the backend or the frontend
      sorts ``work_experience``, so array order is what the profile page renders
      and what the CV generator is handed. Routed through the upsert, a
      just-started job would appear at the BOTTOM of the CV.
    * **identity.** ``_apply_upsert_work`` runs ``classify_engagement_dupe`` for
      every entry the reconciler did not target. On an internal promotion (same
      employer, new title) the verdict is AMBIGUOUS: a confirmation is parked
      and no entry is created at all, leaving the door's required
      ``new_role_id`` with no value.

    The second one is the interesting half, because it is not an accident. The
    dupe guard exists precisely because **the LLM owns entity identity**
    (ADR-046) and must be second-guessed when it says "new entry". Here the
    HUMAN says it — they typed their new employer into the post-hire form — and
    §7.4's ruling already holds that the committer never re-adjudicates direct
    user input (``grounding=None`` → a direct act → ``confirmed``, ADR-061
    clause 2). A guard built for model output has no business in front of an
    act the candidate performed.

    So the act becomes its own op, on the ``ApplyImportMerge`` precedent: when
    an intake is not expressible in the existing vocabulary, the honest remedy
    is a typed, auditable act inside it — never a ``profile_override``-style
    bypass, and never widening a model-emittable op with a more powerful
    parameter (the governing rule below).

    **Deliberately narrow.** It states one role and nothing else: no bullets, no
    skills, no end date, no target to merge into. Everything else about the
    write — the trail, the completeness recompute, ``last_updated``, the
    persisted-denial re-floor — belongs to the committer's invariants, which is
    exactly what routing this writer buys. ``metadata`` is unreachable by the
    op itself.

    **Adapter-only.** It lives in ``DecisionOp``; ``engine._parse_ops``
    validates raw model JSON against ``ReconcileOp`` alone, so a hallucinated
    ``{"op": "add_role", …}`` is dropped before it is ever an object. The
    reconciler keeps ``upsert_work`` — "this role exists", dupe-guarded — and
    does not also get the un-adjudicated form that would mint a job at the top
    of the CV with nothing in front of it.

    ``is_current=True`` is part of the ACT, not a parameter: a just-started role
    IS the current position (#155), and saying so explicitly is what keeps the
    enrichment loop from re-asking an end date that does not exist yet. The
    inverse act is :class:`CloseRole`, and the two travel in one batch.
    """

    op: Literal["add_role"] = "add_role"
    #: The id the new entry WILL carry. Minted by the op so a pure adapter can
    #: answer the door's ``new_role_id`` before the committer runs, without
    #: scraping it back out of the receipts.
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    company: str
    role: str
    start_date: str | None = None
    location: str | None = None
    industry_context: str | None = None


class CloseRole(BaseModel):
    """A role has ended — ADAPTER-ONLY.

    ADR-063 clause 8(e) / the 2026-08-09 amendment clause 1 (#480 design §4.3).
    The 2026-07-29 amendment named three writes the op vocabulary could not
    express; this is the one it called *"a boolean flip (``role_add`` closing a
    role)"*. ``_apply_set_field`` is fill-only by design
    (``if not _is_empty(current): return``), so nothing in the vocabulary could
    move a populated ``is_current`` from ``True`` to ``False``.

    **Named for the act, not the mechanism.** The design considered and rejected
    the obvious primitive: a generic ``SetBool(target, field, value)`` would fix
    the same mechanical gap and hand every future caller a way to flip any
    boolean on any entity. An op's name is a large part of its guard — a caller
    reaching for ``CloseRole`` has to mean *"this role ended"*, and a reviewer
    reading a batch can see what was asserted about the candidate.

    **The #155 tri-state lives here and nowhere else.** ``is_current`` is
    tri-state — ``None`` unknown, ``True`` current, ``False`` known-ended — and
    the convention was previously re-implemented at each writer that cared
    (``role_add``'s close loop, the extraction prompts, the reconciler's
    ``UpsertWork``). Two rules follow from the act, and both are asserted by the
    applier:

    * **the flag is the act, so it is authoritative.** A close writes
      ``is_current = False`` even over a populated ``True``. Nothing else in the
      vocabulary may.
    * **the date is a separate fact, so it stays fill-only.** ``end_date`` is
      optional: *"this role ended and I do not know when"* is a real state, and
      recording it as ``is_current=False`` + ``end_date=None`` keeps the
      end-date GAP open (``completeness.field_present`` only suppresses that gap
      for ``is_current is True``) instead of hiding it behind the flag. And a
      role that already carries an end date is never re-dated here: changing an
      attested date is a CORRECTION, which is ``ResolveField``'s authorised
      overwrite or a human section edit — not a close.

    **Scoped to ``work_experience``.** ``is_current`` is inherited from
    ``ExperienceBase``, so projects and volunteer activities carry the field
    too; this op resolves its ``target`` against work entries alone. Reach wider
    than the act's own name is exactly how a named op decays into the power
    primitive the design rejected.

    **Adapter-only.** It lives in ``DecisionOp``; ``engine._parse_ops``
    validates raw model JSON against ``ReconcileOp`` alone, so a hallucinated
    ``{"op": "close_role", …}`` is dropped before it is ever an object. Ending a
    role is a statement about the candidate's PRESENT — a model that could emit
    one could retire a job the candidate still holds, and the CV built from that
    vault would say so.

    Absorbs ``role_add``'s ``is_current`` close loop.
    """

    op: Literal["close_role"] = "close_role"
    #: The work entry that ended — an existing id, or a local ref from this
    #: batch.
    target: str
    #: The day the role ended, when it is known. ``None`` records *ended, date
    #: unknown* and deliberately leaves the end-date gap open.
    end_date: str | None = None
    #: Why. Required: an adapter-only act states its ground, and this reaches
    #: the candidate's "what changed & why" surface as the receipt's rationale.
    reason: str


class SetProfileMeta(BaseModel):
    """The candidate suppresses a completeness gap — ADAPTER-ONLY.

    ADR-063 clause 8(e) / the 2026-08-09 amendment clause 1 (#480 design §4.4).
    The 2026-07-29 amendment named three writes the op vocabulary could not
    express; this is *"a profile-level metadata write"*. It absorbs the
    ``na_fields`` writer (``routers/profile_enrich.mark_gap_na``), which edited
    ``profile_json`` as a raw dict — no trail, no completeness recompute, no
    denial floor, and no round-trip guarantee.

    **The load-bearing guard is the ``key`` ENUM, and it is the whole point.**
    A free-form ``SetProfileMeta(path, value)`` — the obvious general shape —
    would be able to address ``metadata.denied_concepts`` (releasing a denial
    the candidate gave testimony for) and ``metadata.enrichment_history``
    (forging its own audit trail). Both are catastrophic and neither is
    reviewable, because the reach would be decided at each call site instead of
    by the type. ``Literal["na_fields"]`` is therefore not a convenience: it is
    the mechanism, and it may never gain a member that reaches ``metadata.*``.

    Note the two blocks are one letter apart (#509, deferred): ``key`` addresses
    the ``_meta`` SIDECAR (:class:`~applire.schemas.profile.ProfileMetaBlock` —
    the candidate's own N/A suppressions, #505), never the ``metadata`` block
    that holds the denial record and the trail. ``metadata`` is reachable by no
    op at all; the two narrow exceptions carved out for the interview's
    bookkeeping are :class:`MarkProbeAsked` and :class:`EscalateDenialLevel`,
    each of which reaches exactly one field of one existing record.

    ``mode`` is a second one-member enum, for the same reason. Un-suppressing a
    gap — the candidate deciding an N/A field applies after all — is a real act,
    but it is a different one, and an op that can both add and remove is an op
    whose reach has to be re-argued at every call site. Appending is the only
    thing the writer this absorbs ever did.

    Semantics: **idempotent append-if-absent of one string.** Marking the same
    gap N/A twice leaves one entry and produces no second receipt, exactly as
    the raw writer's ``if current_gap not in existing_na`` did.

    **Adapter-only.** It lives in ``DecisionOp``; ``engine._parse_ops``
    validates raw model JSON against ``ReconcileOp`` alone, so a hallucinated
    ``{"op": "set_profile_meta", …}`` is dropped before it is ever an object.
    Suppressing a gap is the CANDIDATE saying "this does not apply to me"; a
    model that could emit one could silently hide a gap it was unable to fill.
    """

    op: Literal["set_profile_meta"] = "set_profile_meta"
    #: The `_meta` sidecar key being written. THE guard — see the docstring.
    key: Literal["na_fields"]
    #: The completeness-gap string the candidate marked not-applicable.
    value: str
    #: Append-only, by construction. See the docstring on why removal is not a
    #: mode of this op.
    mode: Literal["append"] = "append"


class MarkProbeAsked(BaseModel):
    """The ONE permitted transfer probe has been ISSUED — ADAPTER-ONLY.

    ADR-063 amended 2026-08-09 (third entry) / #480 PR 7, deferred here from
    PR 2 by the design's own ruling: ``metadata.*`` is op-unreachable by design
    and ``TurnGrounding`` is candidate testimony, so an ADR-064 bookkeeping
    write needed the metadata-writer family to be designed as a whole before it
    could be routed.

    Absorbs ``session._mark_probe_asked``. What it records is *"we asked"*,
    never *"they denied"* — the distinction ADR-064's finding-fix is built on:
    the flag is written the instant the probe is issued, independent of how the
    answer is later classified, so an abandoned session cannot lose it and a
    later genuine denial of the same concept cannot re-trigger a probe the
    candidate already received.

    **Reach: one boolean, on one EXISTING record.** It cannot create a
    ``DeniedConcept``, cannot delete one, and cannot move ``denial_level`` —
    that last one is :class:`EscalateDenialLevel`'s single act, and even that
    one only goes one way. Addressing is by ``ats_audit._norm``, the same
    normaliser ``record_denials`` dedupes with, so bookkeeping and testimony can
    never disagree about which record they mean.

    **It fails safe rather than minting.** The ADR-064 M4 finding-fix made
    ``_select_denial_probe_concept`` require a durable record before it will
    select a concept, so reaching "no durable entry" here is a contract
    violation by the caller. The response is a quiet no-op, preserved verbatim
    through the routing: inventing a denial record from bookkeeping alone, with
    no candidate statement behind it, would durably attribute testimony the
    candidate never gave — strictly worse than an unrecorded "we asked".

    **Adapter-only.** A hallucinated ``{"op": "mark_probe_asked", …}`` is
    dropped at ``engine._parse_ops``. A model that could emit one could retire
    the candidate's ONE remaining chance to surface adjacent experience, by
    claiming a question that was never asked.
    """

    op: Literal["mark_probe_asked"] = "mark_probe_asked"
    #: The denied concept whose probe was issued. Matched against an EXISTING
    #: ``metadata.denied_concepts`` entry; a miss is a no-op.
    concept: str


class EscalateDenialLevel(BaseModel):
    """Elicitation is exhausted for a denied concept — ADAPTER-ONLY.

    ADR-063 amended 2026-08-09 (third entry) / #480 PR 7; deferred from PR 2
    alongside :class:`MarkProbeAsked` and for the same reason. Absorbs the
    ``denial_level`` escalation ``services/session.py`` performed inline: a
    SECOND, genuine denial of the concept a transfer probe was about bumps its
    durable level ``direct → partial`` (ADR-064 — "adjacent is ruled out too,
    the question is exhausted").

    **Reach: one monotonic transition, plus its ``date`` stamp.** The monotonic
    rule is not a validated argument on this op — it is the ABSENCE of one.
    There is no ``level`` parameter, so there is no spelling of this op that
    requests ``partial → direct``: a later, weaker probe (or a caller that
    simply did not run the follow-up) can never erase that elicitation was
    already exhausted on an earlier turn.

    **One implementation of that rule, not two.** The applier delegates to
    ``stance.record_denials(..., level_only=True)``, which has owned the
    no-downgrade invariant since the ADR-064 F1 finding-fix. Inlining the branch
    here would give the vocabulary a second copy of a rule that must not be able
    to drift — so this op is deliberately a *routing* of that function, not a
    reimplementation of it. ``level_only`` also carries the two refusals this
    act needs: a concept with no durable record is a no-op (nothing to escalate
    FROM — never mints), and ``statement``/``source`` are never written (#348 —
    the candidate's verbatim testimony is write-once; the level is bookkeeping,
    not testimony content).

    **The receipt is a DENIAL receipt, never a change** (ruling 3, #231's rule
    extended to the committer). An escalation is the candidate ruling MORE out.
    Landing it on ``changes`` would make it read as "gap addressed" to the four
    gates that read ``bool(changes)`` — including ``agent_bridge``'s ledger
    upgrade, where a retraction counting as a change requests an upgrade with
    the candidate's own denial sentence as its backing evidence (the ADR-059
    run-#7 blocker, #352).

    **Adapter-only.** A hallucinated ``{"op": "escalate_denial_level", …}`` is
    dropped at ``engine._parse_ops``. The trigger for this act is a
    deterministic read of which concept the probe was about against the
    reconciler's own atomic denial declarations — a fact (ADR-062 clause 1),
    never a judgement the model gets to make about its own output.
    """

    op: Literal["escalate_denial_level"] = "escalate_denial_level"
    #: The probed concept the candidate denied a second time. Matched against an
    #: EXISTING ``metadata.denied_concepts`` entry; a miss is a no-op.
    concept: str


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
#
# ``ReplaceSection`` joined in PR 3, and the split is what makes its deletion
# semantics safe: replacing a section can DROP entries, so if the model could
# emit one, a hallucinated ``replace_section`` would be a silent way to delete
# vault facts nobody retracted. Only a human editing a section reaches it.
#
# ``ResolveField`` and ``ResolveConfirmation`` joined in PR 5. Both encode an
# act only a human can perform — answering a question the system asked — so a
# model-emittable form would let the reconciler both raise a dispute and decide
# it, and (for ``ResolveField``) overwrite an attested field while doing so.
#
# ``AddRole`` and ``CloseRole`` joined in PR 6, the two halves of the post-hire
# act. ``AddRole`` is the un-adjudicated form of a write the reconciler may only
# make dupe-guarded: the guard is there because the MODEL owns entity identity,
# and a human filling in the post-hire form owns it themselves. Model-emittable,
# it would be a way to mint a job at the top of the CV with no guard in front of
# it — which is the same reason `ApplyImportMerge` is adapter-only.
#
# ``CloseRole`` is the only op allowed to overwrite a
# populated ``is_current``, and what it asserts is a statement about the
# candidate's PRESENT: a hallucinated ``close_role`` would retire a job they
# still hold, and every document built from that vault would repeat it. The
# reconciler already has the model-side way to say "this role is ongoing"
# (``UpsertWork.is_current`` / a fill-only ``set_field``); it does not get the
# authoritative way to say the opposite.

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

# ``SetProfileMeta``, ``MarkProbeAsked`` and ``EscalateDenialLevel`` joined in
# PR 7 — the metadata-writer family. All three are statements the SYSTEM or the
# CANDIDATE makes and the reconciler never does: suppressing a completeness gap
# ("this does not apply to me"), recording that the one permitted transfer probe
# was issued, and recording that elicitation on a denied concept is exhausted.
# Two of them write the vault's record of what the candidate ruled OUT, which is
# the surface a model must never be able to reach — a hallucinated escalation
# would close down the candidate's own chance to surface adjacent experience,
# and a hallucinated suppression would hide a gap the system failed to fill.

# Adapter-only ops. PR 3 added ``ReplaceSection``; PR 5 added ``ResolveField``
# and ``ResolveConfirmation``; PR 6 added ``AddRole`` and ``CloseRole``; PR 7
# added the metadata-writer family.
_ADAPTER_ONLY = (
    DemoteSkill,
    ApplyImportMerge,
    ReplaceSection,
    ResolveField,
    ResolveConfirmation,
    AddRole,
    CloseRole,
    SetProfileMeta,
    MarkProbeAsked,
    EscalateDenialLevel,
)

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
    # #370 — the RAW `op` field of every model-emitted item `engine._parse_ops`
    # dropped for failing schema validation, in encounter order ("<unknown>"
    # when the item carried no string `op` key at all). A FACT (ADR-062
    # clause 1): pure bookkeeping of what failed validation, never a reading
    # of what the item MEANT. Consumed by the testimony write-loss witness
    # (`reconcile.witness.compute_not_applied`) so a parse-time drop is
    # countable instead of visible only at DEBUG log level. Additive and
    # empty by default — every pre-#370 caller of `reconcile()` is
    # unaffected.
    rejected_ops: list[str] = Field(default_factory=list)
