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

"""ADR-046 — the deterministic op applier.

``apply_ops`` folds a list of typed reconciliation ops (``ops.py``) into a
Master Profile. It is a PURE function: it operates on a deep copy, never
touching the input, the DB, or any LLM. The caller owns persistence and the
ADR-042 pre-merge snapshot.

Op semantics implement ADR-013 additive-merge rules (alias-fold, case-insensitive
bullet dedup) but trust the LLM's entity-matching choices — the applier never
re-decides whether two entities are "the same"; it merges into the ``target`` it
was given and creates a new entity when ``target`` is ``None``.
"""
from __future__ import annotations

import logging
import types
import typing
from typing import Any, Union

from pydantic import BaseModel, TypeAdapter, ValidationError

from applire.schemas.profile import (
    Certification,
    Conflict,
    EducationEntry,
    ExperienceBase,
    FieldChange,
    Language,
    MasterProfileData,
    ProjectEntry,
    Publication,
    SignatureStory,
    Skill,
    VAULT_SECTIONS,
    VolunteerActivity,
    WorkEntry,
    _coerce_partial_date,
)

from applire.services.profile.role_facts import project_profile_role_facts

from applire.services.profile.reconcile.dedupe import (
    classify_certification_dupe,
    classify_dupe,
    classify_engagement_dupe,
)
from applire.services.profile.reconcile.ops import (
    AddBullets,
    AddRole,
    ApplyImportMerge,
    CloseRole,
    CommitOp,
    DemoteSkill,
    FlagConflict,
    ReplaceSection,
    RequestConfirmation,
    ResolveConfirmation,
    ResolveField,
    SetField,
    SetPersonalInfo,
    SetSummary,
    UpsertCertification,
    UpsertEducation,
    UpsertLanguage,
    UpsertProject,
    UpsertPublication,
    UpsertSkill,
    UpsertStory,
    UpsertVolunteer,
    UpsertWork,
)

logger = logging.getLogger(__name__)

_PROFICIENCY_ORDER = {"basic": 0, "intermediate": 1, "advanced": 2, "expert": 3}


def _promote_to_confirmed(entity: Any, op_status: str) -> bool:
    """THE promote-only merge rule, in one place — used by all four merge sites
    (skill user-confirmed merge, skill near-dupe auto-merge, certification
    merge, language merge). Returns True iff it moved ``entity.status``.

    ADR-061 clause 3: a merge only ever PROMOTES an existing entry to
    ``confirmed``, never demotes one — a later, weaker mention must not erase
    an already-established vault fact.

    ADR-061 amended 2026-08-08 (#485), and this is why the rule is a function
    rather than four copies of an ``if``: with ``denied`` in the skill status
    vocabulary, the original condition (``existing.status != "confirmed"``)
    reads a retracted entry as merely "not yet confirmed" and silently
    resurrects it — a CV re-import naming the skill suffices, which is the
    live vector the adversarial pass found. **Nothing leaves ``denied`` except
    the explicit ADR-059 un-denial act**, which does not exist yet, so no
    ordinary op may move a denied entry at all.

    The certification and language sites cannot hold ``denied`` today (#485
    scopes the taxonomy change to skills), but they call the same helper: the
    invariant is stated once, so a later widening of the taxonomy cannot land
    with three of the four sites still promoting out of it.
    """
    if op_status != "confirmed":
        return False
    if entity.status == "confirmed":
        return False
    if entity.status == "denied":
        return False
    entity.status = "confirmed"
    return True


def _merge_declared_proficiency(existing: str, incoming: str | None) -> str:
    """ADR-061 clause 5 — a declared proficiency is a ceiling, not a floor.

    ``existing`` is the tier already recorded on the profile's skill. A prior
    incident (#304) had this merge keep-the-higher-of-two, which let a second
    write (interview or import) silently ratchet a deliberately modest
    self-declaration (e.g. ``"Anwender"`` → ``basic``) up to ``expert`` on the
    next import. An explicit self-declaration is the strongest evidence this
    system has about a claim's strength, so once ``existing`` carries a
    recognised tier it is **never raised** by a later write — regardless of
    what ``incoming`` says.

    ``incoming`` only fills the slot when ``existing`` carries no recognised
    tier at all (``_PROFICIENCY_ORDER.get(existing)`` is ``None``) — "the page
    was silent" — which also closes the sibling defect named alongside this
    one: the old code's ``.get(existing.proficiency, 1)`` fallback silently
    asserted *at least intermediate* for an unrecognised value. That default
    is gone; an unrecognised existing value is genuinely unknown, not a floor.
    """
    if incoming is None:
        return existing
    if _PROFICIENCY_ORDER.get(existing) is None:
        return incoming
    return existing


class ApplyResult(BaseModel):
    """The outcome of applying a batch of ops (no persistence)."""

    profile: MasterProfileData
    changes: list[FieldChange] = []
    conflicts: list[Conflict] = []
    pending_confirmations: list[RequestConfirmation] = []
    # #485 — receipts for `demote_skill` ops, kept OFF `changes` on purpose.
    #
    # `bool(applied.changes)` is read by four gates that all mean the same
    # thing: "this turn produced positive, gap-addressing content" — the
    # interview's `addressed` flag (F8: a denial must never read as "resolved
    # this gap"), the `_derive_status` wire status on both agent doors, and —
    # sharpest — `agent_bridge`'s `upgrade=bool(applied.changes)` ledger gate,
    # where a pure retraction counting as a change would request an upgrade
    # with the candidate's own denial sentence as the backing evidence (the
    # ADR-059 run-#7 blocker, #352).
    #
    # A demotion is the exact opposite of gap-addressing content, so it gets
    # its own list — the same separation #231 already makes for `record_denials`
    # receipts, one layer down. Callers FOLD this into the turn's
    # `EnrichmentRecord` (ADR-059 clause 1: negative testimony is receipted
    # like positive) and must never fold it into an addressed/upgrade gate.
    demotions: list[FieldChange] = []
    # #480 PR 2 — receipt metadata only an intake can compute, carried out to
    # the committer so it can land on the `EnrichmentRecord` that intake's write
    # mints. Set exclusively by `ApplyImportMerge` (US161 merge statistics,
    # ADR-041 amended); `None` for every other batch, which is what
    # `EnrichmentRecord.reconciliation` already means ("merge records only").
    reconciliation: dict[str, dict[str, int]] | None = None


def _norm(value: object) -> str:
    return (str(value) if value is not None else "").strip().casefold()


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


# A sentinel distinct from None ("don't write — uncoercible") so callers can tell
# "coerced to None" apart from "could not coerce, skip the write".
_SKIP = object()


def _field_annotation(model_or_instance: Any, field_name: str) -> Any:
    """Return the declared annotation for ``field_name`` on a model/instance.

    ``None`` when the field is unknown (the caller should not write).
    """
    # model_fields lives on the class (instance access is deprecated in V2.11).
    model_cls = (
        model_or_instance
        if isinstance(model_or_instance, type)
        else type(model_or_instance)
    )
    fields = getattr(model_cls, "model_fields", None)
    if not isinstance(fields, dict):
        return None
    info = fields.get(field_name)
    return getattr(info, "annotation", None) if info is not None else None


def _scalar_options(annotation: Any) -> list[type]:
    """The concrete scalar types a (possibly Optional/Union) annotation allows.

    ``str | None`` → ``[str]``; ``int | None`` → ``[int]``; ``str`` → ``[str]``.
    Non-scalar members (list, dict, BaseModel, Literal, date, …) are left out —
    we only special-case str/int/float coercion and defer everything else to
    Pydantic's own validation (see ``_coerce_to_field_type``).
    """
    origin = typing.get_origin(annotation)
    if origin in (Union, getattr(types, "UnionType", ())):
        members = typing.get_args(annotation)
    else:
        members = (annotation,)
    return [m for m in members if m in (str, int, float)]


def _coerce_to_field_type(model_or_instance: Any, field_name: str, value: Any) -> Any:
    """Coerce ``value`` to ``field_name``'s declared type, or return ``_SKIP``.

    The reconciler's ``SetField``/``SetPersonalInfo`` ops carry an untyped ``Any``
    value, and ``setattr`` on a Pydantic instance bypasses validation — so a type
    mismatch (an ``int`` into a ``str | None`` field) silently corrupts the
    profile and only blows up on the next ``model_validate`` (the UAT bug).

    Rules:
    - ``None``/empty (``""``, ``[]``) passes through unchanged.
    - target ``str`` ← a number is stringified cleanly (``1800000`` → ``"1800000"``,
      ``1800000.0`` → ``"1800000"``).
    - target ``int`` ← a clean numeric string / ``float`` → ``int``
      (``"6"`` → ``6``, ``6.0`` → ``6``).
    - target ``float`` ← a clean numeric string / ``int`` → ``float``.
    - anything Pydantic already accepts for the field is written as-is.
    - a genuinely uncoercible value returns ``_SKIP`` (never corrupt the field).
    """
    annotation = _field_annotation(model_or_instance, field_name)
    if annotation is None:
        return _SKIP  # unknown field — never write
    # Empty/None stays as-is (callers gate on emptiness separately).
    if value is None or value == "" or value == []:
        return value

    targets = _scalar_options(annotation)

    # str target ← number: stringify cleanly (drop a whole-number float's ".0").
    if str in targets and isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    # int target ← clean numeric string / float.
    if int in targets and str not in targets and not isinstance(value, bool):
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            s = value.strip()
            try:
                return int(s)
            except ValueError:
                try:
                    f = float(s)
                except ValueError:
                    return _SKIP
                return int(f) if f.is_integer() else _SKIP
        return _SKIP

    # float target ← clean numeric string / int.
    if float in targets and str not in targets and not isinstance(value, bool):
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return _SKIP
        return _SKIP

    # No scalar special-case applied. Defer to Pydantic: if the field's own
    # annotation already accepts this value, write it; otherwise skip rather than
    # corrupt (e.g. a free-text string into a date/Literal field).
    try:
        return TypeAdapter(annotation).validate_python(value)
    except (ValidationError, ValueError, TypeError):
        return _SKIP


def _append_dedup(existing: list[str], incoming: list[str]) -> bool:
    """Case-insensitively append non-dup strings; return True if the list grew."""
    seen = {_norm(x) for x in existing}
    grew = False
    for item in incoming:
        if not isinstance(item, str):
            continue
        key = _norm(item)
        if key and key not in seen:
            existing.append(item)
            seen.add(key)
            grew = True
    return grew


def _section_for(entity: Any) -> str:
    if isinstance(entity, WorkEntry):
        return "work_experience"
    if isinstance(entity, ProjectEntry):
        return "projects"
    if isinstance(entity, VolunteerActivity):
        return "volunteer_activities"
    return ""


def apply_ops(
    profile: MasterProfileData, ops: list[CommitOp], source: str
) -> ApplyResult:
    """Apply ``ops`` to a deep copy of ``profile`` in order.

    Returns the new profile state plus the field-change trail, flagged conflicts,
    and any pending confirmations. The input profile is never mutated.
    """
    new_profile = profile.model_copy(deep=True)
    changes: list[FieldChange] = []
    conflicts: list[Conflict] = []
    pending: list[RequestConfirmation] = []
    demotions: list[FieldChange] = []  # #485 — see ApplyResult.demotions
    reconciliation: dict[str, dict[str, int]] | None = None

    # Local ref ("w1") → the entity object created/resolved by an entity op.
    ref_map: dict[str, ExperienceBase] = {}

    def resolve(handle: str | None) -> ExperienceBase | None:
        """Resolve a target/parent/evidence handle to an entity, or None."""
        if handle is None:
            return None
        if handle in ref_map:
            return ref_map[handle]
        for entry in (
            *new_profile.work_experience,
            *new_profile.projects,
            *new_profile.volunteer_activities,
        ):
            if getattr(entry, "id", None) == handle:
                return entry
        return None

    for op in ops:
        if isinstance(op, UpsertWork):
            _apply_upsert_work(op, new_profile, ref_map, changes, pending)
        elif isinstance(op, UpsertProject):
            _apply_upsert_project(op, new_profile, ref_map, resolve, changes, pending)
        elif isinstance(op, UpsertVolunteer):
            _apply_upsert_volunteer(op, new_profile, ref_map, changes, pending)
        elif isinstance(op, AddBullets):
            _apply_add_bullets(op, resolve, changes, pending)
        elif isinstance(op, UpsertSkill):
            _apply_upsert_skill(op, new_profile, resolve, changes, pending)
        elif isinstance(op, DemoteSkill):
            _apply_demote_skill(op, new_profile, demotions)
        elif isinstance(op, UpsertCertification):
            _apply_upsert_certification(op, new_profile, changes, pending)
        elif isinstance(op, UpsertLanguage):
            _apply_upsert_language(op, new_profile, changes, pending)
        elif isinstance(op, UpsertEducation):
            _apply_upsert_education(op, new_profile, changes, pending)
        elif isinstance(op, UpsertPublication):
            _apply_upsert_publication(op, new_profile, changes, pending)
        elif isinstance(op, UpsertStory):
            _apply_upsert_story(op, new_profile, resolve, source, changes)
        elif isinstance(op, SetField):
            _apply_set_field(op, resolve, changes)
        elif isinstance(op, SetPersonalInfo):
            _apply_set_personal_info(op, new_profile, changes)
        elif isinstance(op, SetSummary):
            _apply_set_summary(op, new_profile, source, changes, conflicts)
        elif isinstance(op, FlagConflict):
            _apply_flag_conflict(op, resolve, source, conflicts)
        elif isinstance(op, RequestConfirmation):
            pending.append(op)
        elif isinstance(op, ReplaceSection):
            # #480 PR 3 — the manual section edit, as a typed act. Returns a
            # freshly validated profile because a section replace re-shapes a
            # whole branch of the tree (the PATCH intake always round-tripped
            # the dict through `MasterProfileData`; that is unchanged).
            new_profile = _apply_replace_section(op, new_profile, changes)
        elif isinstance(op, ResolveField):
            # #480 PR 5 — the AUTHORISED overwrite. Returns a freshly validated
            # profile for the same reason `ReplaceSection` does: the write is
            # performed on the dumped dict (a conflict's `field` is the model's
            # own string and may name no schema slot at all).
            new_profile = _apply_resolve_field(op, new_profile, changes)
        elif isinstance(op, ResolveConfirmation):
            _apply_resolve_confirmation(op, new_profile, changes)
        elif isinstance(op, AddRole):
            # #480 PR 6 — the post-hire act. Un-adjudicated by design: see the
            # op's docstring on why the dupe guard belongs in front of model
            # output and not in front of a human filling in a form.
            _apply_add_role(op, new_profile, ref_map, changes)
        elif isinstance(op, CloseRole):
            # #480 PR 6 — the act of ending a role, and the ONE place the #155
            # tri-state convention is implemented.
            _apply_close_role(op, new_profile, ref_map, changes)
        elif isinstance(op, ApplyImportMerge):
            # #480 PR 2 — the import's whole-merge act. Deterministic code
            # already decided every field (see the op's docstring for why no op
            # sequence can reproduce an import), so the applier INSTALLS it
            # rather than re-deciding it. Deep-copied, because `apply_ops`
            # promises never to mutate anything it was handed.
            new_profile = op.merged.model_copy(deep=True)
            changes.extend(op.changes)
            reconciliation = op.reconciliation

    # #328 (option 4) / #382 — the quantified role facts are DERIVED
    # PROJECTIONS of the entry's own bullets, so they are recomputed HERE, on
    # the single committer (ADR-063), after every op has landed: the write path
    # is the only place that can guarantee they never drift from the prose they
    # project. Unconditional and idempotent — it must also correct an entry an
    # unrelated op merely touched, and an entry no op touched at all.
    project_profile_role_facts(new_profile)

    # Defense in depth (ADR-046): the write-time coercion above is the real fix,
    # but apply_ops must NEVER hand back a profile that won't re-load. Round-trip
    # the result through model_validate; if some future op path still slips a
    # schema-rejecting value through, fall back to the untouched input rather than
    # persisting (and later 500-ing on) a corrupt profile.
    new_profile = _ensure_loadable(new_profile, fallback=profile)

    return ApplyResult(
        profile=new_profile,
        changes=changes,
        conflicts=conflicts,
        pending_confirmations=pending,
        demotions=demotions,
        reconciliation=reconciliation,
    )


def _ensure_loadable(
    candidate: MasterProfileData, fallback: MasterProfileData
) -> MasterProfileData:
    """Return ``candidate`` re-validated through the load path, or ``fallback``.

    Mirrors how the profile is reloaded from JSONB (``model_dump(mode="json")`` →
    ``model_validate``). Guarantees the returned profile loads cleanly.
    """
    try:
        return MasterProfileData.model_validate(candidate.model_dump(mode="json"))
    except ValidationError:
        return fallback


# ── Per-op handlers ───────────────────────────────────────────────────────────


def _fill_empties(entity: Any, fields: dict[str, Any]) -> bool:
    """Fill only currently-empty scalar fields; never overwrite non-empty ones.

    Returns ``True`` iff at least one field was actually set — a real, additive
    change. Callers use this to gate an audit-trail ``FieldChange`` for MATCH
    branches that otherwise merge invisibly (#177 review): a pure-duplicate
    upsert with nothing left to fill stays silent, but a merge that filled a
    date/org/DOI is recorded like every other merge in the trail.
    """
    changed = False
    for name, value in fields.items():
        if value is None:
            continue
        if _is_empty(getattr(entity, name, None)):
            setattr(entity, name, value)
            changed = True
    return changed


def _added(section: str, field: str, value: Any) -> FieldChange:
    return FieldChange(
        section=section,
        field=field,
        action="added",
        new_value=value,
        rationale=f"Added {field} to {section} via reconciliation.",
        rationale_key="reconcile_added",
    )


def _merged(section: str, field: str, old: Any, new: Any) -> FieldChange:
    return FieldChange(
        section=section,
        field=field,
        action="merged",
        old_value=old,
        new_value=new,
        rationale=f"Merged {field} into existing {section} via reconciliation.",
        rationale_key="reconcile_merged",
    )


def _updated(section: str, field: str, old: Any, new: Any) -> FieldChange:
    return FieldChange(
        section=section,
        field=field,
        action="updated",
        old_value=old,
        new_value=new,
        rationale=f"Filled empty {field} on {section} via reconciliation.",
        rationale_key="reconcile_updated",
    )


# ── ReplaceSection: the manual section edit, diffed per entry ─────────────────
#
# #480 PR 3 / ADR-063 amended 2026-08-09 clause 8. Until now a manual section
# edit left ONE `FieldChange` carrying the whole section before and after — an
# opaque blob. It is why `discarded_later_edits` is unusable and why a deletion
# was invisible in the trail: "work_experience updated" says nothing about which
# role was dropped. The applier therefore diffs the incoming section against the
# current one and records each entry that appeared, changed or DISAPPEARED as
# its own receipt.
#
# The §7.7 ruling is what this implements: deletion is already expressible
# through today's PATCH, so this is the same capability with a per-entry
# receipt, not a new one. Refusing removal-shaped diffs and demanding an
# explicit `RemoveEntry` act is deferred to Finetuner (#507) — cited, not
# re-argued.

#: How an ENTRY of each list section is named in a receipt, and matched across
#: the diff when it carries no stable id. The fields mirror how the existing
#: appliers key entries: skills/certifications by ``name``, languages by
#: ``language``, engagements by their role + org (the same pair
#: `classify_dupe`/`classify_engagement_dupe` compare), stories/publications by
#: ``title``.
_SECTION_ENTRY_LABEL_FIELDS: dict[str, tuple[str, ...]] = {
    "work_experience": ("role", "company"),
    "education": ("degree", "institution"),
    "certifications": ("name",),
    "skills": ("name",),
    "languages": ("language",),
    "publications": ("title",),
    "volunteer_activities": ("role", "organization"),
    "signature_stories": ("title",),
}


def _entry_label(section: str, entry: Any) -> str:
    """A human-readable name for one entry, used as the receipt's ``field``."""
    if not isinstance(entry, dict):
        return section
    parts = [
        str(entry[name]).strip()
        for name in _SECTION_ENTRY_LABEL_FIELDS.get(section, ())
        if entry.get(name)
    ]
    return " @ ".join(parts) if parts else section


def _entry_id(entry: Any) -> str | None:
    """The entry's stable id, when it has one (work/project/volunteer/education).

    Skills, languages, certifications and publications carry none — they are
    matched by label, which is exactly how the upsert appliers key them.
    """
    if not isinstance(entry, dict):
        return None
    value = entry.get("id")
    return str(value) if value else None


def _pair_entries(
    section: str, old_entries: list[Any], new_entries: list[Any]
) -> tuple[list[tuple[Any, Any]], list[Any], list[Any]]:
    """Match old entries to new ones: ``(pairs, removed, added)``.

    Two passes, because the two doors supply different shapes. The profile page
    round-trips whole entries and keeps their ids; a hand-built agent payload
    (or a pre-#336 snapshot) may carry none, and an id-less entry is re-minted a
    fresh uuid by the schema's default factory on every load — so matching on
    ids alone would read "same role, edited bullets" as a removal plus an
    addition, inventing a deletion receipt for an edit. Ids first (exact,
    authoritative), then labels for whatever is left, and only then is an entry
    genuinely gone.
    """
    remaining_new = list(new_entries)
    pairs: list[tuple[Any, Any]] = []
    unmatched_old: list[Any] = []

    for old in old_entries:
        old_id = _entry_id(old)
        match = None
        if old_id is not None:
            match = next(
                (n for n in remaining_new if _entry_id(n) == old_id), None
            )
        if match is None:
            unmatched_old.append(old)
            continue
        remaining_new.remove(match)
        pairs.append((old, match))

    still_unmatched_old: list[Any] = []
    for old in unmatched_old:
        key = _norm(_entry_label(section, old))
        match = next(
            (n for n in remaining_new if _norm(_entry_label(section, n)) == key), None
        )
        if match is None:
            still_unmatched_old.append(old)
            continue
        remaining_new.remove(match)
        pairs.append((old, match))

    return pairs, still_unmatched_old, remaining_new


def _section_change(
    section: str, field: str, action: str, old: Any, new: Any
) -> FieldChange:
    verbs = {
        "added": ("Added", "manual_section_added"),
        "updated": ("Updated", "manual_section_updated"),
        "removed": ("Removed", "manual_section_removed"),
    }
    verb, key = verbs[action]
    return FieldChange(
        section=section,
        field=field,
        action=action,  # type: ignore[arg-type]
        old_value=old,
        new_value=new,
        rationale=f"{verb} {field} in {section} (manual section edit).",
        rationale_key=key,
    )


def _diff_list_section(section: str, before: Any, after: Any) -> list[FieldChange]:
    """Per-entry receipts for a wholesale list replace, removals included."""
    old_entries = list(before or [])
    new_entries = list(after or [])
    pairs, removed, added = _pair_entries(section, old_entries, new_entries)

    changes: list[FieldChange] = []
    for old, new in pairs:
        if old != new:
            changes.append(
                _section_change(
                    section, _entry_label(section, new), "updated", old, new
                )
            )
    for entry in removed:
        changes.append(
            _section_change(
                section, _entry_label(section, entry), "removed", entry, None
            )
        )
    for entry in added:
        changes.append(
            _section_change(section, _entry_label(section, entry), "added", None, entry)
        )
    return changes


def _diff_object_section(section: str, before: Any, after: Any) -> list[FieldChange]:
    """Per-KEY receipts for a merge-patched object section (#178).

    An explicit null that clears a field is a removal, not an update: the
    receipt must be able to say "you cleared your phone number", which is the
    same distinction the list diff draws for a dropped entry.
    """
    old_obj = before if isinstance(before, dict) else {}
    new_obj = after if isinstance(after, dict) else {}
    changes: list[FieldChange] = []
    for key in list(old_obj) + [k for k in new_obj if k not in old_obj]:
        old_value = old_obj.get(key)
        new_value = new_obj.get(key)
        if old_value == new_value:
            continue
        if _is_empty(old_value):
            action = "added"
        elif _is_empty(new_value):
            action = "removed"
        else:
            action = "updated"
        changes.append(_section_change(section, key, action, old_value, new_value))
    return changes


def _apply_replace_section(
    op: ReplaceSection, profile: MasterProfileData, changes: list[FieldChange]
) -> MasterProfileData:
    """Replace (or merge-patch) one section and receipt the diff.

    The section vocabulary guard lives on the op itself (``ReplaceSection``
    validates ``section`` against ``VAULT_SECTIONS``), so ``metadata`` cannot be
    addressed here at all — belt and braces, this refuses an unknown section
    rather than writing a stray key into the dump.

    Semantics are the PATCH intake's, unchanged: object sections merge-patch
    (#178 — supplied keys win, explicit null clears, omitted keys survive), list
    sections are replaced wholesale. The diff is taken between the section as it
    LOADS before and after, so the receipt describes what actually landed
    (schema defaults included) rather than what the payload happened to spell.
    """
    if op.section not in VAULT_SECTIONS:  # pragma: no cover — op-validated
        raise ValueError(f"Invalid section '{op.section}'")

    dumped = profile.model_dump(mode="json")
    before = dumped.get(op.section)

    if op.is_object_section:
        if not isinstance(op.value, dict):
            raise ValueError(
                f"Section '{op.section}' expects an object; supplied keys are "
                f"merged (an explicit null clears a field)."
            )
        merged = dict(before or {})
        merged.update(op.value)
        dumped[op.section] = merged
    else:
        dumped[op.section] = op.value

    # The same round-trip the PATCH intake always performed — a payload the
    # schema rejects raises here (pydantic's ValidationError IS a ValueError,
    # which is what both doors already translate into a 422 / invalid_input).
    updated = MasterProfileData.model_validate(dumped)
    after = updated.model_dump(mode="json").get(op.section)

    if op.is_object_section:
        changes.extend(_diff_object_section(op.section, before, after))
    else:
        changes.extend(_diff_list_section(op.section, before, after))
    return updated


# ── ResolveField / ResolveConfirmation: answering what the system asked ───────
#
# #480 PR 5 (design §4.2 / §4.5). Both acts used to live in
# `services/profile/__init__.py` as bespoke `profile_json` assignments — no
# reconcile, no stance guard, no denial floor, no committer (#512). They are ops
# now, which buys three things at once: the committer's invariant set around the
# write, a typed adapter-only vocabulary the model cannot reach, and — for the
# #218 bullet surgery below — code that can be unit-tested without a database.
#
# All the surgery helpers are FACT-LEVEL: locate a named entity, rewrite a named
# string. No similarity matching and no judgement about whether two bullets say
# the same thing — that verdict is the reconciler model's (ADR-062 clause 1) and
# arrives on the `Conflict` record.


def _entry_for_conflict(entries: list, conflict: Conflict) -> dict | None:
    """The list entry a conflict belongs to: by ``entity_id``, else by value.

    #218. Before it, the resolution path could reach a dict section and — for
    ``work_experience`` only — the FIRST entry whose scalar field still equalled
    the old value, so a bullet-level dispute resolved into nothing and the
    rejected variant stayed in the vault.
    """
    if conflict.entity_id:
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") == conflict.entity_id:
                return entry
        return None
    # Conflicts parked before `entity_id` existed carry no identity — fall back
    # to the pre-#218 behaviour: the first entry still holding the old value.
    old = _norm(conflict.existing_value)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        current = entry.get(conflict.field)
        if isinstance(current, list):
            if any(_norm(item) == old for item in current):
                return entry
        elif current == conflict.existing_value:
            return entry
    return None


def _rewrite_bullet_list(
    bullets: list, existing: Any, incoming: Any, chosen: Any
) -> list:
    """Leave the chosen wording in place of the contested pair, order preserved.

    Both disputed variants may be stored (the reconciler's bullet dedup is
    exact-string, so two wordings of one fact both survive an import — #453).
    Resolving the dispute must therefore also remove the variant the candidate
    rejected; otherwise the vault keeps serving the figure they just ruled out.
    """
    contested = {_norm(existing), _norm(incoming)} - {""}
    chosen_key = _norm(chosen)
    result: list = []
    placed = False
    for bullet in bullets:
        key = _norm(bullet)
        if key in contested:
            if chosen_key and not placed:
                result.append(chosen)
                placed = True
            continue  # the losing variant is dropped, not left alongside
        if chosen_key and key == chosen_key and placed:
            continue  # never leave the chosen wording twice
        result.append(bullet)
    if chosen_key and not placed and chosen_key not in {_norm(b) for b in result}:
        # The disputed wording is no longer stored (edited away, or the incoming
        # variant was never merged). The candidate's choice still has to land.
        result.append(chosen)
    return result


def _apply_resolution_to_list_section(
    entries: list, conflict: Conflict, chosen: Any
) -> tuple[Any, bool]:
    """Write ``chosen`` onto the entry the conflict names, in place.

    Returns ``(old_value, wrote)`` so the caller can receipt what actually
    changed rather than what the op asked for.
    """
    entry = _entry_for_conflict(entries, conflict)
    if entry is None:
        return None, False
    # A `field` the schema has no slot for needs no guard here: the write lands
    # on the dumped dict and `MasterProfileData.model_validate` drops it
    # (pydantic `extra="ignore"`), so the resolution is a quiet no-op either way.
    current = entry.get(conflict.field)
    if isinstance(current, list):
        entry[conflict.field] = _rewrite_bullet_list(
            current, conflict.existing_value, conflict.incoming_value, chosen
        )
    else:
        entry[conflict.field] = chosen
    return current, True


def _open_conflict_for(
    profile: MasterProfileData, op: ResolveField
) -> Conflict | None:
    """THE guard: the dispute that authorises this overwrite, or ``None``.

    Two conditions, both load-bearing (see ``ResolveField``'s docstring):

    1. the ``conflict_id`` names an **open** conflict on this profile — a
       resolved one is spent authority, and an unknown one is no authority at
       all;
    2. the op describes THAT dispute (same section, field and entity), so one
       open conflict cannot authorise an overwrite somewhere else.

    ``metadata`` is refused outright: a dispute may never become a write to
    ``denied_concepts`` or ``enrichment_history``.
    """
    metadata = profile.metadata
    if metadata is None:
        return None
    if op.section == "metadata":
        return None
    conflict = next(
        (
            c
            for c in metadata.pending_conflicts
            if c.conflict_id == op.conflict_id and not c.resolved
        ),
        None,
    )
    if conflict is None:
        return None
    if (
        (conflict.section or "") != (op.section or "")
        or conflict.field != op.field
        or (conflict.entity_id or None) != (op.target or None)
    ):
        return None
    return conflict


def _apply_resolve_field(
    op: ResolveField, profile: MasterProfileData, changes: list[FieldChange]
) -> MasterProfileData:
    """Write the candidate's decision onto the disputed field and close it."""
    conflict = _open_conflict_for(profile, op)
    if conflict is None:
        logger.info(
            "apply_ops: refused resolve_field for conflict %s — no matching OPEN "
            "conflict on the profile (ADR-063 clause 8(e) / #480 §4.2: the "
            "authority for an authorised overwrite is the dispute itself)",
            op.conflict_id,
        )
        return profile

    # The winning value comes from the DISPUTE for the two non-manual
    # resolutions, so an adapter cannot claim "incoming" and write something
    # else. Only `manual` reads the op's own `value` — the candidate's words.
    if op.resolution == "existing":
        chosen = conflict.existing_value
    elif op.resolution == "incoming":
        chosen = conflict.incoming_value
    else:
        chosen = op.value

    dumped = profile.model_dump(mode="json")
    section_data = dumped.get(op.section)
    old_value: Any = None
    wrote = False
    if isinstance(section_data, dict):
        old_value = section_data.get(op.field)
        section_data[op.field] = chosen
        dumped[op.section] = section_data
        wrote = True
    elif isinstance(section_data, list):
        # #218 — every entity section (work_experience, projects,
        # volunteer_activities) resolves the same way: find the entry the
        # conflict names, then write the chosen value onto its field —
        # replacing the contested string in place when that field is a list.
        old_value, wrote = _apply_resolution_to_list_section(
            section_data, conflict, chosen
        )
        dumped[op.section] = section_data
    # `section=""` (the applier could not resolve the flagged entity) leaves
    # nowhere to write. The decision is still the candidate's, so the dispute
    # closes below — an unanswerable question must not stay open forever.

    # The same round-trip the resolution intake always performed — a chosen
    # value the schema rejects raises here exactly as it did before (pydantic's
    # ValidationError IS a ValueError, which the REST door maps to a 422), and
    # `apply_ops` round-trips the whole batch again on the way out.
    updated = MasterProfileData.model_validate(dumped)
    if updated.metadata is not None:
        # Closing the dispute is REMOVAL, exactly as before: both resolve paths
        # delete rather than flag, and `_surviving_parked_items` relies on it
        # (a resolved item is not carried forward across import rounds).
        updated.metadata.pending_conflicts = [
            c
            for c in updated.metadata.pending_conflicts
            if c.conflict_id != op.conflict_id
        ]
    if wrote:
        changes.append(
            FieldChange(
                section=op.section,
                field=op.field,
                action="updated",
                # The DISPUTED value, not the raw slot content: for a bullet
                # list the slot holds the whole list, and the receipt is about
                # the two sides the candidate chose between.
                old_value=conflict.existing_value,
                new_value=chosen,
                rationale=f"Resolved a conflict on {op.field} ({op.resolution}).",
                rationale_key="conflict_resolved",
            )
        )
    return updated


def _apply_resolve_confirmation(
    op: ResolveConfirmation, profile: MasterProfileData, changes: list[FieldChange]
) -> None:
    """Record the chosen option and clear the parked ask (design §4.5).

    Bookkeeping, not content: the reconciler already applied its best-effort
    merge when it raised the ambiguity. What this closes is the LIFECYCLE — a
    parked confirmation with no durable clear would be re-asked by every later
    session that reads `metadata.pending_confirmations` (#480 PR 2's
    `park_confirmations` note).

    An unknown or already-resolved id is a quiet no-op: `resolve_confirmation`
    raises `LookupError` at the door for a genuinely unknown id, and the
    interview's resume path deliberately tolerates an already-answered one.
    """
    metadata = profile.metadata
    if metadata is None:
        return
    entry = next(
        (
            c
            for c in metadata.pending_confirmations
            if c.confirmation_id == op.confirmation_id and not c.resolved
        ),
        None,
    )
    if entry is None:
        return
    metadata.pending_confirmations = [
        c
        for c in metadata.pending_confirmations
        if c.confirmation_id != op.confirmation_id
    ]
    changes.append(
        FieldChange(
            section="metadata",
            field="pending_confirmations",
            action="updated",
            old_value=entry.question,
            new_value=op.chosen_option,
            rationale="Recorded your answer to a confirmation question.",
            rationale_key="confirmation_resolved",
        )
    )


def _apply_add_role(
    op: AddRole,
    profile: MasterProfileData,
    ref_map: dict[str, ExperienceBase],
    changes: list[FieldChange],
) -> None:
    """Create the role the candidate just started — at the TOP, un-adjudicated.

    Two properties are the whole reason this op exists rather than an
    ``UpsertWork`` (see the op's docstring for the refutations that produced the
    ruling):

    * **index 0.** Nothing sorts ``work_experience``; the array order is what
      the profile page renders and what the CV generator receives. The newest
      role goes first, as this intake has always placed it.
    * **no dupe classification.** ``classify_engagement_dupe`` guards writes
      whose entity identity the MODEL decided. This one the candidate decided,
      and §7.4 rules that the committer does not re-adjudicate direct user
      input. An internal promotion therefore creates the second role instead of
      parking a confirmation and creating nothing.

    ``is_current=True`` is part of the act (#155). The entry is registered in the
    batch ref-map under its own id so a later op in the same batch can reach it.
    """
    entry = WorkEntry(
        id=op.id,
        company=op.company,
        role=op.role,
        location=op.location,
        start_date=op.start_date,
        end_date=None,
        is_current=True,
        industry_context=op.industry_context,
    )
    profile.work_experience.insert(0, entry)
    ref_map[op.id] = entry
    changes.append(
        FieldChange(
            section="work_experience",
            field=f"[{entry.id}]",
            action="added",
            new_value={
                "company": entry.company,
                "role": entry.role,
                "start_date": entry.start_date,
            },
            rationale="Added the role you have just started.",
            rationale_key="role_added",
        )
    )


def _apply_close_role(
    op: CloseRole,
    profile: MasterProfileData,
    ref_map: dict[str, ExperienceBase],
    changes: list[FieldChange],
) -> None:
    """End a role — and the ONE implementation of the #155 tri-state (§4.3).

    The convention (`is_current`: ``None`` unknown · ``True`` current · ``False``
    known-ended) used to be re-stated by every writer that cared, which is how
    ``role_add`` could set the flag while the reconciler's fill-only
    ``set_field`` could not. Two rules, and the split between them is the point:

    * **the flag is the act** — ``is_current`` is written authoritatively, over a
      populated ``True``. This is the "boolean flip" ADR-063's 2026-07-29
      amendment recorded as inexpressible, and no other op may perform it;
    * **the date is a separate fact** — ``end_date`` is FILL-ONLY, the same rule
      ``_apply_set_field`` enforces everywhere else. An undated close records
      *"ended, date unknown"* and leaves the end-date gap open (only
      ``is_current is True`` suppresses it, `completeness.field_present`), and a
      role that already carries a date keeps it: re-dating an attested fact is a
      correction, which needs `ResolveField`'s authorised overwrite or a human
      section edit.

    Resolves against ``work_experience`` only — see the op's docstring on why the
    reach may not be wider than the act's name, even though ``is_current`` is
    inherited by projects and volunteer activities.
    """
    target: WorkEntry | None = next(
        (w for w in profile.work_experience if w.id == op.target), None
    )
    if target is None:
        candidate = ref_map.get(op.target)
        if isinstance(candidate, WorkEntry):
            target = candidate
    if target is None:
        logger.info(
            "apply_ops: refused close_role for target %s — no such work entry "
            "(#480 §4.3: a close names one role, and only a role)",
            op.target,
        )
        return

    rationale = f"Closed this role ({op.reason})."
    if op.end_date is not None and _is_empty(target.end_date):
        old_end = target.end_date
        target.end_date = op.end_date
        changes.append(
            FieldChange(
                section="work_experience",
                field=f"[{target.id}].end_date",
                action="updated",
                old_value=old_end,
                new_value=op.end_date,
                rationale=rationale,
                rationale_key="role_closed",
            )
        )
    if target.is_current is not False:
        old_flag = target.is_current
        target.is_current = False
        changes.append(
            FieldChange(
                section="work_experience",
                field=f"[{target.id}].is_current",
                action="updated",
                old_value=old_flag,
                new_value=False,
                rationale=rationale,
                rationale_key="role_closed",
            )
        )


def _apply_upsert_work(op, profile, ref_map, changes, pending):
    target = None
    if op.target is not None:
        target = next(
            (w for w in profile.work_experience if w.id == op.target), None
        )
    if target is None and op.target is not None and op.target in ref_map:
        candidate = ref_map[op.target]
        if isinstance(candidate, WorkEntry):
            target = candidate

    if target is None:
        # #177: deterministic near-dup guard. The LLM owns identity (ADR-046) and
        # said "new entry" — but an existing engagement at a near-dupe org with the
        # same start month is the same stint (adopt it); a near-dupe org+role with
        # absent/differing dates is ambiguous (ask, never guess). An ambiguous op's
        # ref stays unmapped, so dependent add_bullets skip defensively — the
        # confirmation context carries the payload for the resolution turn.
        verdict = classify_engagement_dupe(
            org=op.company, role=op.role, start_date=op.start_date,
            existing=profile.work_experience, org_getter=lambda w: w.company,
        )
        if verdict.match is not None:
            target = verdict.match
        elif verdict.ambiguous:
            related = [f"{w.role} at {w.company}" for w in verdict.ambiguous]
            pending.append(RequestConfirmation(
                question=(
                    f"'{op.role} at {op.company}' looks close to an existing "
                    f"position ({'; '.join(related)}). Is it the same position?"
                ),
                options=["Same position — merge them", "Different — keep both"],
                context={"section": "work_experience",
                         "incoming": op.model_dump(exclude={"op"}), "existing": related},
            ))
            return

    if target is None:
        entry = WorkEntry(
            company=op.company or "",
            role=op.role or "",
            start_date=op.start_date,
            end_date=op.end_date,
            is_current=op.is_current,
            location=op.location,
            team_size=op.team_size,
            industry_context=op.industry_context,
            budget_managed=op.budget_managed,
        )
        profile.work_experience.append(entry)
        ref_map[op.ref] = entry
        changes.append(_added("work_experience", "company", entry.company))
        return

    # Merge into existing.
    ref_map[op.ref] = target
    # ADR-013 Rule 1: a differing role becomes a role alias (never overwrite role).
    if (
        op.role
        and _norm(op.role) != _norm(target.role)
        and _norm(op.role) not in {_norm(a) for a in target.role_aliases}
    ):
        target.role_aliases.append(op.role)
        changes.append(_merged("work_experience", "role_aliases", None, op.role))
    # Fill only empties for the rest (never overwrite company/role).
    _fill_empties(
        target,
        {
            "company": op.company,
            "role": op.role,
            "start_date": op.start_date,
            "end_date": op.end_date,
            "is_current": op.is_current,  # #155 — None (unknown) is never written
            "location": op.location,
            "team_size": op.team_size,
            "industry_context": op.industry_context,
            "budget_managed": op.budget_managed,
        },
    )


def _apply_upsert_project(op, profile, ref_map, resolve, changes, pending):
    parent_id = None
    parent = resolve(op.parent)
    if parent is not None:
        parent_id = getattr(parent, "id", None)

    target = None
    if op.target is not None:
        target = next((p for p in profile.projects if p.id == op.target), None)
        if target is None and op.target in ref_map:
            candidate = ref_map[op.target]
            if isinstance(candidate, ProjectEntry):
                target = candidate

    if target is None:
        # #177: near-dup guard (see _apply_upsert_work). "Website" ⊂ "Website
        # Relaunch" is not identity for open-ended project names, so containment
        # is never auto-merged here.
        verdict = classify_engagement_dupe(
            org=op.name, role=op.role, start_date=op.start_date,
            existing=profile.projects, org_getter=lambda p: p.name,
        )
        if verdict.match is not None:
            target = verdict.match
        elif verdict.ambiguous:
            related = [f"{p.role} at {p.name}" for p in verdict.ambiguous]
            pending.append(RequestConfirmation(
                question=(
                    f"'{op.role} at {op.name}' looks close to an existing "
                    f"project ({'; '.join(related)}). Is it the same project?"
                ),
                options=["Same project — merge them", "Different — keep both"],
                context={"section": "projects",
                         "incoming": op.model_dump(exclude={"op"}), "existing": related},
            ))
            return

    if target is None:
        entry = ProjectEntry(
            name=op.name or "",
            role=op.role or "",
            start_date=op.start_date,
            end_date=op.end_date,
            url=op.url,
            description=op.description,
            associated_experience=parent_id,
        )
        profile.projects.append(entry)
        ref_map[op.ref] = entry
        changes.append(_added("projects", "name", entry.name))
        return

    ref_map[op.ref] = target
    _fill_empties(
        target,
        {
            "name": op.name,
            "role": op.role,
            "start_date": op.start_date,
            "end_date": op.end_date,
            "url": op.url,
            "description": op.description,
            "associated_experience": parent_id,
        },
    )
    changes.append(_merged("projects", "name", None, op.name))


def _apply_upsert_volunteer(op, profile, ref_map, changes, pending):
    target = None
    if op.target is not None:
        target = next(
            (v for v in profile.volunteer_activities if v.id == op.target), None
        )
        if target is None and op.target in ref_map:
            candidate = ref_map[op.target]
            if isinstance(candidate, VolunteerActivity):
                target = candidate

    if target is None:
        # #177: near-dup guard (see _apply_upsert_work).
        verdict = classify_engagement_dupe(
            org=op.organization, role=op.role, start_date=op.start_date,
            existing=profile.volunteer_activities, org_getter=lambda v: v.organization,
        )
        if verdict.match is not None:
            target = verdict.match
        elif verdict.ambiguous:
            related = [f"{v.role} at {v.organization}" for v in verdict.ambiguous]
            pending.append(RequestConfirmation(
                question=(
                    f"'{op.role} at {op.organization}' looks close to an existing "
                    f"volunteer activity ({'; '.join(related)}). Is it the same activity?"
                ),
                options=["Same activity — merge them", "Different — keep both"],
                context={"section": "volunteer_activities",
                         "incoming": op.model_dump(exclude={"op"}), "existing": related},
            ))
            return

    if target is None:
        entry = VolunteerActivity(
            organization=op.organization or "",
            role=op.role or "",
            cause=op.cause,
            start_date=op.start_date,
            end_date=op.end_date,
            description=op.description,
        )
        profile.volunteer_activities.append(entry)
        ref_map[op.ref] = entry
        changes.append(_added("volunteer_activities", "organization", entry.organization))
        return

    ref_map[op.ref] = target
    # VolunteerActivity has no role_aliases — a differing role can only fill an
    # empty role; it is never folded into an alias list (ADR-013 Rule 1 is
    # WorkEntry-specific).
    _fill_empties(
        target,
        {
            "organization": op.organization,
            "role": op.role,
            "cause": op.cause,
            "start_date": op.start_date,
            "end_date": op.end_date,
            "description": op.description,
        },
    )


def _apply_add_bullets(op, resolve, changes, pending):
    entity = resolve(op.target)
    if entity is None:
        # #177 review: an AMBIGUOUS upsert in this same batch never populates
        # ref_map (see _apply_upsert_work/_project/_volunteer), so a co-batched
        # AddBullets targeting that op's local ref resolves to None here. The
        # confirmation's context carries the op's own `ref` under "incoming" —
        # if one matches, carry the bullets into the confirmation so the
        # resolution turn can apply them instead of losing them silently.
        # Merge (extend), never overwrite: multiple AddBullets ops in one batch
        # may target the same still-unresolved ref.
        for conf in pending:
            if conf.context.get("incoming", {}).get("ref") == op.target:
                carried = conf.context.setdefault("pending_bullets", {})
                for field, incoming in (
                    ("responsibilities", op.responsibilities),
                    ("achievements", op.achievements),
                    ("technologies", op.technologies),
                ):
                    if incoming:
                        carried.setdefault(field, [])
                        carried[field].extend(incoming)
                return
        return  # no matching confirmation either — defensive: unknown ref, skip
    section = _section_for(entity)
    for field, incoming in (
        ("responsibilities", op.responsibilities),
        ("achievements", op.achievements),
        ("technologies", op.technologies),
    ):
        if not incoming:
            continue
        if _append_dedup(getattr(entity, field), incoming):
            changes.append(_merged(section, field, None, incoming))


def _apply_upsert_skill(op, profile, resolve, changes, pending, *, user_confirmed=None):
    # #172: match on the SHARED near-dupe predicate (ats_audit), not just exact
    # _norm equality — so 'Team Leadership and Mentorship' merges into an existing
    # 'Team Leadership' instead of littering the profile with morphological twins.
    #
    # #407 recon note (not fixed here — no vector pinned): the issue reported two
    # duplicate "SAP" skill rows (years_experience 9 and 15) surviving in the
    # run-12 vault for a profile built from TWO source documents (a CV + a XING
    # export, tests/files/panel_review_case/operations_marcus_de/). Read against
    # this function: an exact-name repeat ("SAP" == "SAP") always reaches
    # `len(near) == 1` below via the Jaccard disjunct in skills_near_dupe
    # (identical single-token names -> Jaccard 1.0), which merges via
    # _append_dedup rather than appending a second row — verified against two
    # independent real-provider extractions of the fixture's two source
    # documents (CV text and XING text), both of which returned the skill name
    # "SAP" (exact, same category) for this profile. `ops.py` defines exactly
    # one op type for skills ("upsert_skill"), always routed through this
    # function — there is no second write path that could bypass the guard.
    # The one available ground-truth snapshot (backend/logs/llm/2026-07-31.jsonl,
    # the gap-analysis prompt embedding the full candidate profile, ts
    # 18:05:42Z) shows exactly ONE "SAP" row (experience_refs already unioning
    # both employers — that IS #407 item 2, the extraction-time misattribution,
    # already fixed via the prompt) — not two. The dev-stack Postgres
    # (master_profiles) had 0 rows at investigation time, so the actual run-12
    # persisted vault could not be inspected directly. The duplicate-row vector
    # itself (as opposed to item 2's misattribution) is UNPINNED: reproducing it
    # needs either the real run-12 profile_json or a live end-to-end two-
    # document import observed through the actual reconcile op stream — do not
    # guess-fix this function on the strength of the code read alone.
    from applire.services.ats_audit import (
        skill_tokens,
        skills_near_dupe,
        skills_single_token_containment,
    )

    evidence_ids: list[str] = []
    for handle in op.evidence:
        ent = resolve(handle)
        if ent is not None:
            ent_id = getattr(ent, "id", None)
            if ent_id:
                evidence_ids.append(ent_id)
        # else: leave unresolved handles out (defensive)

    near = [s for s in profile.skills if skills_near_dupe(s.name, op.name)]

    # #187 — the user has RESOLVED a deferred dedupe confirmation for this skill.
    # Apply their choice directly and never re-emit the confirmation: the guards
    # below are stateless, so re-running them would surface the identical question
    # again and loop forever. ``"merge"`` folds the incoming into the matched
    # existing skill; ``"distinct"`` appends it as its own separate skill.
    if user_confirmed in ("merge", "distinct"):
        merge_targets = near or [
            s for s in profile.skills
            if skills_single_token_containment(s.name, op.name)
        ]
        if user_confirmed == "merge" and merge_targets:
            existing = merge_targets[0]
            _append_dedup(existing.experience_refs, evidence_ids)
            if op.proficiency:
                # ADR-061 clause 5 — ceiling, not floor (see _merge_declared_proficiency).
                existing.proficiency = _merge_declared_proficiency(
                    existing.proficiency, op.proficiency.lower()
                )
            # ADR-061 clause 3 + the 2026-08-08 amendment (#485) — promote-only,
            # and never OUT of `denied`. See _promote_to_confirmed.
            _promote_to_confirmed(existing, op.status)
            # Keep the more-specific/longer name only when the incoming strictly
            # contains the existing tokens (mirrors the near==1 auto-merge below).
            if skill_tokens(op.name) > skill_tokens(existing.name):
                existing.name = op.name
            changes.append(_merged("skills", "name", None, existing.name))
            return
        # "distinct", or "merge" with nothing to merge into: append a new skill.
        skill_kwargs: dict[str, Any] = {
            "name": op.name, "experience_refs": evidence_ids, "status": op.status,
        }
        if op.category:
            skill_kwargs["category"] = op.category
        if op.proficiency:
            skill_kwargs["proficiency"] = op.proficiency
        profile.skills.append(Skill(**skill_kwargs))
        changes.append(_added("skills", "name", op.name))
        return

    # An incoming skill that near-dupes MULTIPLE existing skills spans more than one
    # distinct atom. Silently merging would collapse distinct skills or swallow an
    # atom that also exists separately — defer to the user via the confirmations
    # channel (E037 PQ #4) instead of guessing.
    if len(near) >= 2:
        names = [s.name for s in near]
        joined = ", ".join(names)
        pending.append(
            RequestConfirmation(
                question=(
                    f"'{op.name}' overlaps several skills already on your profile "
                    f"({joined}). Should it replace them or be kept as a separate skill?"
                ),
                options=[f"Merge into '{op.name}'", "Keep the existing skills"],
                context={"incoming_skill": op.name, "overlapping_skills": names},
            )
        )
        return

    # No auto-merge match. Before appending, check for BARE single-token containment
    # with any existing skill ('React' vs 'React Native', 'Docker' vs 'Docker &
    # Kubernetes'). These are NOT auto-merged (strict predicate, #172) — silently
    # merging swallowed a genuine skill or renamed an atom into a compound. Ask the
    # user instead; carry enough context that answering later loses nothing.
    if not near:
        containment = [
            s for s in profile.skills
            if skills_single_token_containment(s.name, op.name)
        ]
        if containment:
            related = [s.name for s in containment]
            joined = ", ".join(related)
            pending.append(
                RequestConfirmation(
                    question=(
                        f"'{op.name}' shares a word with skills already on your "
                        f"profile ({joined}) but may be a distinct skill. Add it "
                        f"separately, or merge it into an existing one?"
                    ),
                    options=[
                        f"Add '{op.name}' as a separate skill",
                        "Merge into the existing skill",
                    ],
                    context={
                        "incoming_skill": op.name,
                        "related_skills": related,
                        "category": op.category,
                        "proficiency": op.proficiency,
                        "evidence_refs": evidence_ids,
                    },
                )
            )
            return

    if len(near) == 1:
        existing = near[0]
        _append_dedup(existing.experience_refs, evidence_ids)
        if op.proficiency:
            # ADR-061 clause 5 — ceiling, not floor (see _merge_declared_proficiency).
            existing.proficiency = _merge_declared_proficiency(
                existing.proficiency, op.proficiency.lower()
            )
        # ADR-061 clause 3 + the 2026-08-08 amendment: promote-only, and never
        # out of `denied` (see _promote_to_confirmed for the full rationale).
        _promote_to_confirmed(existing, op.status)
        # Keep the more-specific/longer name ONLY when the incoming strictly
        # contains the existing tokens; otherwise the existing name stays.
        if skill_tokens(op.name) > skill_tokens(existing.name):
            existing.name = op.name
        changes.append(_merged("skills", "name", None, existing.name))
        return

    skill_kwargs: dict[str, Any] = {
        "name": op.name, "experience_refs": evidence_ids, "status": op.status,
    }
    if op.category:
        skill_kwargs["category"] = op.category
    if op.proficiency:
        skill_kwargs["proficiency"] = op.proficiency
    profile.skills.append(Skill(**skill_kwargs))
    changes.append(_added("skills", "name", op.name))


def _apply_demote_skill(op, profile, demotions):
    """ADR-063 clause 8(e) / ADR-061 amended 2026-08-08 (#485) — mark the
    retracted skill ``denied``. Mark, DON'T delete: the entry keeps its name,
    category, proficiency and ``experience_refs``; only ``status`` moves, and
    the move is receipted like every other write (ADR-059 clause 1 — negative
    testimony is receipted exactly like positive).

    Matching is EXACT on the normalised name, never the near-dupe predicate the
    upsert path uses: ``op.name`` is copied verbatim off the persisted entry by
    the emitter, so there is nothing to fuzzy-match, and a demotion that
    widened by similarity would assert testimony the candidate never gave
    (the ADR-059 assert/refuse split, #486).

    Idempotent: an entry already ``denied`` is left alone and produces no
    receipt, so a re-retraction does not litter the enrichment history.
    """
    target = _norm(op.name)
    for skill in profile.skills:
        if _norm(skill.name) != target:
            continue
        if skill.status == "denied":
            return
        old = skill.status
        skill.status = "denied"
        demotions.append(
            FieldChange(
                section="skills",
                field="status",
                action="updated",
                old_value=old,
                new_value="denied",
                rationale=(
                    f"Retracted: {skill.name} marked denied — the candidate "
                    f"stated they have no experience with "
                    f"'{op.declared_denial or skill.name}' (their own "
                    "testimony). The entry and its history are kept."
                ),
            )
        )
        return


def _apply_upsert_certification(op, profile, changes, pending):
    verdict = classify_certification_dupe(
        name=op.name,
        issuing_organization=op.issuing_organization,
        credential_id=op.credential_id,
        existing=profile.certifications,
        name_getter=lambda c: c.name,
        org_getter=lambda c: c.issuing_organization,
        credential_id_getter=lambda c: c.credential_id,
    )
    if verdict.match is not None:
        # #177 review: coerce raw op strings onto date-typed fields BEFORE the
        # fill-only setattr — setattr bypasses Pydantic validation, so an
        # unparseable string would otherwise survive until _ensure_loadable's
        # round-trip silently drops it to None.
        changed = _fill_empties(verdict.match, {
            "issuing_organization": op.issuing_organization,
            "date_obtained": _coerce_partial_date(op.date_obtained),
            "expiry_date": _coerce_partial_date(op.expiry_date),
            "credential_id": op.credential_id,
            "credential_url": op.credential_url,
        })
        # ADR-061 clause 3: a merge only ever PROMOTES to confirmed, never
        # demotes an already-confirmed entry — mirrors the near-dupe skill
        # merge's own one-directional trust rule (see _promote_to_confirmed).
        if _promote_to_confirmed(verdict.match, op.status):
            changed = True
        if changed:
            changes.append(_merged("certifications", "name", None, verdict.match.name))
        return
    if verdict.ambiguous:
        related = [c.name for c in verdict.ambiguous]
        pending.append(RequestConfirmation(
            question=(
                f"'{op.name}' shares a word with certifications already on your "
                f"profile ({', '.join(related)}) but may be distinct. Add it "
                f"separately, or is it the same certification?"
            ),
            options=[f"Add '{op.name}' as a separate certification",
                     "Same certification — merge"],
            context={"section": "certifications", "incoming": op.model_dump(exclude={"op"}),
                     "existing": related},
        ))
        return
    profile.certifications.append(Certification(
        name=op.name,
        issuing_organization=op.issuing_organization,
        date_obtained=op.date_obtained,
        expiry_date=op.expiry_date,
        credential_id=op.credential_id,
        credential_url=op.credential_url,
        status=op.status,  # ADR-061 clause 3
    ))
    changes.append(_added("certifications", "name", op.name))


def _apply_upsert_language(op, profile, changes, pending):
    # Languages are a closed domain — 'German' ⊂ 'German (Native)' IS the same
    # language, so containment auto-merges instead of asking (#177).
    verdict = classify_dupe(
        {"language": op.language}, profile.languages,
        {"language": lambda l: l.language}, containment_is_same=True,
    )
    if verdict.match is not None:
        changed = _fill_empties(verdict.match, {"level": op.level})
        # ADR-061 clause 3: promote-only, same rule as certifications.
        if _promote_to_confirmed(verdict.match, op.status):
            changed = True
        if changed:
            changes.append(_merged("languages", "language", None, verdict.match.language))
        return
    profile.languages.append(Language(language=op.language, level=op.level, status=op.status))
    changes.append(_added("languages", "language", op.language))


def _apply_upsert_education(op, profile, changes, pending):
    verdict = classify_dupe(
        {"institution": op.institution, "degree": op.degree},
        profile.education,
        {"institution": lambda e: e.institution, "degree": lambda e: e.degree},
    )
    if verdict.match is not None:
        changed = _fill_empties(verdict.match, {
            "field": op.field,
            "start_date": op.start_date,
            "end_date": op.end_date,
            "grade": op.grade,
        })
        if changed:
            changes.append(_merged("education", "institution", None, verdict.match.institution))
        return
    if verdict.ambiguous:
        existing = [f"{e.degree} — {e.institution}" for e in verdict.ambiguous]
        pending.append(RequestConfirmation(
            question=(
                f"'{op.degree} — {op.institution}' looks close to an education "
                f"entry already on your profile ({'; '.join(existing)}). Is it "
                f"the same qualification?"
            ),
            options=["Same entry — merge them", "Different — keep both"],
            context={"section": "education", "incoming": op.model_dump(exclude={"op"}),
                     "existing": existing},
        ))
        return
    profile.education.append(EducationEntry(
        institution=op.institution,
        degree=op.degree,
        field=op.field or "",
        start_date=op.start_date,
        end_date=op.end_date,
        grade=op.grade,
    ))
    changes.append(_added("education", "institution", op.institution))


def _apply_upsert_publication(op, profile, changes, pending):
    # #177: publications had NO reconciler op at all — they rode the import slice
    # group but were silently un-mergeable. Same three-band policy as education.
    verdict = classify_dupe(
        {"title": op.title}, profile.publications, {"title": lambda p: p.title}
    )
    if verdict.match is not None:
        changed = _fill_empties(verdict.match, {
            "venue": op.venue, "published_date": _coerce_partial_date(op.published_date),
            "doi": op.doi, "url": op.url, "patent_number": op.patent_number,
        })
        if changed:
            changes.append(_merged("publications", "title", None, verdict.match.title))
        return
    if verdict.ambiguous:
        related = [p.title for p in verdict.ambiguous]
        pending.append(RequestConfirmation(
            question=(
                f"'{op.title}' shares its wording with a publication already on "
                f"your profile ({'; '.join(related)}). Is it the same publication?"
            ),
            options=["Same publication — merge", "Different — keep both"],
            context={"section": "publications", "incoming": op.model_dump(exclude={"op"}),
                     "existing": related},
        ))
        return
    profile.publications.append(Publication(
        title=op.title, type=op.type, venue=op.venue,
        published_date=op.published_date, doi=op.doi, url=op.url,
        patent_number=op.patent_number, co_authors=op.co_authors,
    ))
    changes.append(_added("publications", "title", op.title))


def _apply_upsert_story(op, profile, resolve, source, changes):
    # ADR-055. Identity = normalized-title EQUALITY only; a near-dupe title
    # APPENDS instead of asking — deliberately no RequestConfirmation here:
    # both confirmation-resolution channels are skill-shaped (import
    # resolve_confirmation records-only, the interview path gates on
    # incoming_skill), so a story parked behind a question would be silently
    # dropped. A visible duplicate is recoverable; vanished testimony is not.
    evidence_ids: list[str] = []
    for handle in op.evidence:
        ent = resolve(handle)
        if ent is not None:
            ent_id = getattr(ent, "id", None)
            if ent_id and ent_id not in evidence_ids:
                evidence_ids.append(ent_id)
        # else: leave unresolved handles out (defensive, UpsertSkill parity)
    match = next(
        (s for s in profile.signature_stories if _norm(s.title) == _norm(op.title)),
        None,
    )
    if match is not None:
        filled = _fill_empties(match, {"benchmark": op.benchmark})
        if evidence_ids:
            before_refs = list(match.experience_refs)
            for ref in evidence_ids:
                if ref not in match.experience_refs:
                    match.experience_refs.append(ref)
            filled = filled or match.experience_refs != before_refs
        if filled:
            # Full story prose in new_value: the Oracle attaches ADR-046
            # receipts by blob containment over FieldChange.new_value — a
            # title-only record would leave story units receipt-less.
            changes.append(_merged(
                "signature_stories", match.title, None, match.model_dump(mode="json"),
            ))
        return
    story = SignatureStory(
        title=op.title, challenge=op.challenge, mechanism=op.mechanism,
        outcome=op.outcome, benchmark=op.benchmark,
        experience_refs=evidence_ids, source=source,
    )
    profile.signature_stories.append(story)
    changes.append(_added(
        "signature_stories", story.title, story.model_dump(mode="json"),
    ))


def _apply_set_field(op, resolve, changes):
    entity = resolve(op.target)
    if entity is None:
        return
    if not hasattr(entity, op.field):
        return
    current = getattr(entity, op.field)
    if not _is_empty(current):
        return  # a real change goes through FlagConflict, not SetField
    value = _coerce_to_field_type(entity, op.field, op.value)
    if value is _SKIP:
        return  # uncoercible — never setattr a type the schema would reject
    setattr(entity, op.field, value)
    changes.append(_updated(_section_for(entity), op.field, current, value))


def _apply_set_personal_info(op, profile, changes):
    pi = profile.personal_info
    if not hasattr(pi, op.field):
        return
    current = getattr(pi, op.field)
    if not _is_empty(current):
        return
    value = _coerce_to_field_type(pi, op.field, op.value)
    if value is _SKIP:
        return  # uncoercible — never setattr a type the schema would reject
    setattr(pi, op.field, value)
    changes.append(_updated("personal_info", op.field, current, value))


def _apply_set_summary(op, profile, source, changes, conflicts):
    # #113(b) / ADR-061. This was the one write in this applier with no
    # "already populated" gate — its SetField and SetPersonalInfo siblings both
    # have one — so a second CV import replaced the stored summary outright.
    # The summary is editable prose (PATCH /api/profile/{section}), so the text
    # being overwritten can be the candidate's own words: a silent loss of user
    # content. It becomes visible state instead.
    #
    # The model cannot raise this itself — FlagConflict is entity-targeted
    # (`target` resolves to a work/project/volunteer entry) and the summary
    # hangs off the profile, not an entity — so the gate belongs here. It is a
    # fact-level test (is the stored value non-empty, and does it differ?), not
    # a judgement about which summary is better; that judgement is the user's,
    # taken on the existing conflict channel: pending_conflicts →
    # ProfileReviewDrawer → POST /api/profile/conflicts/{id}/resolve, whose
    # generic dict-section branch writes straight back into
    # professional_summary[lang]. No new confirmation path is invented.
    old = getattr(profile.professional_summary, op.lang)
    if _is_empty(op.text):
        return  # absence is not an update, and not a conflict either
    if not _is_empty(old):
        if _norm(old) == _norm(op.text):
            return  # a restatement of what is already stored
        conflicts.append(
            Conflict(
                section="professional_summary",
                field=op.lang,
                existing_value=old,
                incoming_value=op.text,
                source=source,
            )
        )
        return
    setattr(profile.professional_summary, op.lang, op.text)
    changes.append(
        FieldChange(
            section="professional_summary",
            field=op.lang,
            action="added",
            old_value=old,
            new_value=op.text,
            rationale="Set professional summary via reconciliation.",
            rationale_key="reconcile_summary",
        )
    )


def _apply_flag_conflict(op, resolve, source, conflicts):
    # Absence is not a conflict. Only record a conflict when BOTH sides carry
    # competing information (non-empty) AND they actually differ. A None/empty
    # side means "no competing information" — the value should just be filled,
    # not disputed. This is the deterministic guard against false-positive
    # conflicts like ``team_size: '6' vs 'None'``.
    if _is_empty(op.existing) or _is_empty(op.incoming):
        return
    if _norm(op.existing) == _norm(op.incoming):
        return
    entity = resolve(op.target)
    section = _section_for(entity) if entity is not None else ""
    conflicts.append(
        Conflict(
            section=section,
            field=op.field,
            # #218 — carry the entity identity through, so the resolution
            # endpoint writes back to THIS role's field/bullet rather than
            # guessing at the first entry that still matches. The applier
            # itself never re-decides the dispute (`op.field` may name a
            # bullet list — whether two bullets contradict is the reconciler
            # model's judgement, ADR-062 clause 1); it only routes the verdict.
            entity_id=getattr(entity, "id", None),
            existing_value=op.existing,
            incoming_value=op.incoming,
            source=source,
        )
    )
