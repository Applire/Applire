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

"""#615 — the import doors' carried-predicate (ADR-063 amended 2026-08-28,
second entry of the day; ADR-041 amended the same day for the count side).

A CV import that merges into an existing profile answered 200 ``DRAFT`` while
whole sections of the document never reached the vault (root cause fixed by
ADR-078 amended, `prompts/reconcile.py` / `services/prompt_view.py`). This
module is the FACT-checker: for every incoming list-section entry, was it
CARRIED into the merged profile — never "was it applied correctly", never
"should it have applied" (ADR-062 clause 1 — a fact, not a judgement).

**FACTS only. No item here is proof of loss** — mirrors
``reconcile.witness``'s own doctrine for the testimony door (#370) exactly,
and one shape is NAMED, not merely disclaimed: the reconciler splitting an
incoming COMPOUND skill into two atoms on merge (the real captured case,
"SAP CO/FI" -> "SAP CO" + "SAP FI") lists the compound's own label as
``not_applied`` even though both halves landed — the profile is RICHER, not
lossier. See ``test_615_import_witness.py::test_local11_sap_split_pins_
exactly_one_not_applied_item`` for the pinned real-provider replay.

**The carried-predicate — one function, section-aware.** For each incoming
entry of a list-valued content section (natural key from
``reconcile/apply.py:_ENTRY_NATURAL_KEYS`` — the committer's own "same entry"
table, imported here rather than copied), CARRIED when:

  (a) the natural key is present in the merged profile, or
  (b) the merge's OWN near-dupe instrument matches it to a merged entry of
      that section — ``dedupe.classify_dupe`` on the label, for the four FLAT
      sections with no section-aware instrument of their own (skills,
      languages, publications, signature_stories); the section-aware
      instruments the real applier itself uses for the other two FLAT
      sections — ``dedupe.classify_certification_dupe`` (certifications) and
      ``dedupe.classify_education_dupe`` (education, #618) — so this witness
      can never report a pair the applier just merged as a loss (the N1
      shape, ADR-066: one logical operation, one implementation); the
      date-aware ``dedupe.classify_engagement_dupe``, MATCH ONLY (an
      AMBIGUOUS verdict does not rescue — the entry stays listed, the caller
      judges), for the three ENGAGEMENT sections (work_experience, projects,
      volunteer_activities); or
  (c) an emitted op for that section carries it: an ``upsert_<section>`` op
      whose own declared natural-key field(s), normalised, equal the
      incoming entry's — this closes the gap arm (b) cannot: an op that
      landed as an AMBIGUOUS ``RequestConfirmation`` at apply time never
      reaches ``merged`` at all, but it is VISIBLE on the confirmation
      channel, not silently gone, so it is not treated as lost here either —
      **or**, for engagement sections specifically, an op with a `target`
      (``add_bullets``, ``set_field``, or an ``upsert_*`` that itself carries
      a `target`) whose target — a real id, or a local `ref` assigned by an
      entity op earlier in the SAME batch — resolves to a merged entity
      whose organisation is a SAME-verdict near-dupe of the incoming entry's
      organisation (the identical org matcher ``classify_engagement_dupe``
      itself uses — ``dedupe._field_relation``, imported here, never a new
      token heuristic). This closes refuter B's BLOCKER1: a second-source
      merge legitimately arrives as ``add_bullets`` against an existing
      entity that restates no key at all (prompt rule 7's own economical
      form — "never use add_bullets merely to restate a title"), and a
      label-only rule (arms a/b alone) reports that clean, prompt-endorsed
      merge as a loss — **or**, for FLAT sections specifically (#602/#620), a
      ``set_field`` op whose `target` resolves BY ID to a merged entry of that
      section which shares at least one non-empty natural-key field
      (normalised) with the incoming entry. Flat entities are addressed by id
      directly (ADR-077 clause 1), so no org/date correlation is needed the
      way engagement sections require it — the real case: a LinkedIn import
      states "German Diploma" for an education entry the vault already holds
      as "Diplom"; the reconciler correlates them and emits
      ``set_field(target=<id>, field="degree", ...)`` rather than an
      ``upsert_education``, so sub-clause 1's key-restating check cannot see
      it, and the CHANGED field is itself the one that breaks the natural-key
      match arm (a) needs. Scoped to entries sharing a field with the touched
      entry (not "any set_field anywhere in this section") so one targeted
      edit cannot blanket-rescue an unrelated loss in the same batch.

Otherwise -> ``not_applied`` item, reason ``no_op_carried_entry``. Every raw
op in ``rejected_ops`` (``ReconcileResult.rejected_ops`` — a model op that
failed schema validation at ``engine._parse_ops``) is its own item, reason
``op_rejected``, ``section=None`` — a parse-time drop that is not tied to any
one incoming entry, so it is not folded into any section's count (see
``reconciliation.compute_merge_reconciliation``, which sums only
``no_op_carried_entry`` items — counting an ``op_rejected`` item too would
double-count the SAME underlying loss when the corresponding incoming entry
independently fails arms a/b/c as well).

**A literal duplicate incoming entry is checked once, not once per
occurrence** — the predicate dedupes by natural key before evaluating arms
a/b/c, matching ``compute_merge_reconciliation``'s own ``extracted`` count
(a set of distinct keys), so the two numbers can never disagree about how
many distinct things were extracted.

**Known limitation — arm (c)'s local-ref resolution does not distinguish "the
ref's own entity op landed" from "it parked as an ambiguous confirmation".**
An ``add_bullets`` targeting a local ref whose entity op turned out AMBIGUOUS
at apply time (and so never populated ``apply_ops``'s internal ``ref_map``)
is still read here as touching that op's OWN declared organisation — a
narrow, accepted false-NEGATIVE risk (a real loss going unreported), the
opposite direction from every other named shape in this module. Not
observed in the captured/replayed record; named here rather than engineered
around, per this module's own "no item is proof" doctrine cutting both ways.

**A clean `not_applied` is not proof of a clean merge** (refuter B MINOR4):
identity itself can still duplicate an entry (a translation/abbreviation pair
that clears the near-dupe bar for "not lost" while the APPLIER's OWN write-
time near-dupe check disagreed and appended a second row) — that is the
health hub's count to see, not this fact's.
"""
from __future__ import annotations

from typing import Any, Callable, NamedTuple, Sequence

from applire.schemas.profile import ImportNotApplied, MasterProfileData
from applire.services.profile.reconcile.apply import _ENTRY_NATURAL_KEYS, _norm
from applire.services.profile.reconcile.dedupe import (
    _field_relation,
    _SAME,
    classify_certification_dupe,
    classify_dupe,
    classify_education_dupe,
    classify_engagement_dupe,
)
from applire.services.profile.reconcile.ops import (
    AddBullets,
    CommitOp,
    SetField,
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

#: Sections whose identity is a single- or multi-field LABEL, auditable via
#: the generic ``classify_dupe`` — every ``_ENTRY_NATURAL_KEYS`` section that
#: is not an ``ExperienceBase`` engagement.
_FLAT_SECTIONS: tuple[str, ...] = (
    "skills",
    "certifications",
    "languages",
    "education",
    "publications",
    "signature_stories",
)

#: Languages are a closed domain — containment ('German' ⊂ 'German (Native)')
#: IS identity, mirroring `apply.py:_apply_upsert_language`'s own call.
#: Every other flat section is open-ended (default: containment != identity).
_FLAT_CONTAINMENT_IS_SAME: dict[str, bool] = {"languages": True}

_FLAT_OP_TYPES: dict[str, type[CommitOp]] = {
    "skills": UpsertSkill,
    "certifications": UpsertCertification,
    "languages": UpsertLanguage,
    "education": UpsertEducation,
    "publications": UpsertPublication,
    "signature_stories": UpsertStory,
}


class _EngagementSection(NamedTuple):
    name: str
    org_field: str  # "company" / "name" / "organization"
    op_type: type[CommitOp]


_ENGAGEMENT_SECTIONS: tuple[_EngagementSection, ...] = (
    _EngagementSection("work_experience", "company", UpsertWork),
    _EngagementSection("projects", "name", UpsertProject),
    _EngagementSection("volunteer_activities", "organization", UpsertVolunteer),
)
_ENGAGEMENT_OP_TYPES: tuple[type[CommitOp], ...] = tuple(
    s.op_type for s in _ENGAGEMENT_SECTIONS
)

#: The identity the WITNESS (and, through it, the US161 counts) keys on. The
#: committer's `_ENTRY_NATURAL_KEYS` is the prefix for every section; the three
#: engagement sections additionally carry `start_date`, because a repeat stint
#: (same employer, same role, a different year — "Foo Corp / Engineer 2013"
#: and "Foo Corp / Engineer 2020") is a DISTINCT data point of the CV, and a
#: key without the date collapsed the two into one: if either copy was in the
#: merged profile, the other was silently skipped before any arm ran and
#: `extracted` counted 2 CV lines as 1 (adversarial pass 2026-08-28, B1 —
#: reproduced through the real `apply_ops`). US161's original key was
#: (company, start_date); this restores the date as a discriminator while
#: keeping the committer's (org, role) as the shared prefix.
WITNESS_KEYS: dict[str, tuple[str, ...]] = {
    **{name: _ENTRY_NATURAL_KEYS[name] for name in _FLAT_SECTIONS},
    **{s.name: _ENTRY_NATURAL_KEYS[s.name] + ("start_date",) for s in _ENGAGEMENT_SECTIONS},
}
_ENGAGEMENT_SECTION_BY_OP: dict[type[CommitOp], _EngagementSection] = {
    s.op_type: s for s in _ENGAGEMENT_SECTIONS
}


def _getters_for(fields: tuple[str, ...]) -> dict[str, Callable[[Any], str | None]]:
    return {f: (lambda entry, f=f: getattr(entry, f, None)) for f in fields}


def _entry_key(entry: Any, fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_norm(getattr(entry, f, "") or "") for f in fields)


def _format_label(entry: Any, fields: tuple[str, ...]) -> str:
    parts = [str(getattr(entry, f, "") or "").strip() for f in fields]
    return " / ".join(p for p in parts if p)


def _op_natural_keys(ops: Sequence[CommitOp], op_type: type, fields: tuple[str, ...]) -> set[tuple[str, ...]]:
    return {
        tuple(_norm(getattr(op, f, "") or "") for f in fields)
        for op in ops
        if isinstance(op, op_type)
    }


def _flat_set_field_touched_entries(
    ops: Sequence[CommitOp], merged_entries: Sequence[Any]
) -> list[Any]:
    """Arm (c), sub-clause 2 (#602/#620) — merged entries a `set_field` op
    targets directly by id.

    A `set_field` against an EXISTING id is the model correlating the incoming
    information with an entity that already exists — not a loss, even when the
    changed field is itself the one that makes the natural key stop matching
    post-merge (the real case: LinkedIn's "German Diploma" vs the vault's own
    "Diplom" — `set_field(target=<id>, field="degree", ...)`, never an
    `upsert_education`, so sub-clause 1's key-restating check cannot see it).
    Mirrors the engagement sections' own sub-clause 2, minus the org/date
    correlation those sections use instead of an id (flat entities are
    addressed by id directly — ADR-077 clause 1)."""
    targets = {op.target for op in ops if isinstance(op, SetField)}
    if not targets:
        return []
    return [e for e in merged_entries if getattr(e, "id", None) in targets]


def _flat_section_not_applied(
    section: str, incoming: MasterProfileData, merged: MasterProfileData, ops: Sequence[CommitOp]
) -> list[ImportNotApplied]:
    fields = _ENTRY_NATURAL_KEYS[section]
    incoming_entries = getattr(incoming, section)
    merged_entries = getattr(merged, section)
    merged_keys = {_entry_key(e, fields) for e in merged_entries}
    op_keys = _op_natural_keys(ops, _FLAT_OP_TYPES[section], fields)
    touched_entries = _flat_set_field_touched_entries(ops, merged_entries)
    getters = _getters_for(fields)
    containment_is_same = _FLAT_CONTAINMENT_IS_SAME.get(section, False)

    items: list[ImportNotApplied] = []
    seen: set[tuple[str, ...]] = set()
    for entry in incoming_entries:
        key = _entry_key(entry, fields)
        if key in seen:
            continue
        seen.add(key)
        if key in merged_keys:  # arm (a)
            continue
        if section == "certifications":
            # N1 (adversarial pass 2026-08-28): the REAL applier
            # (`_apply_upsert_certification`) decides identity with the
            # cert-aware instrument (credential-id anchor, ®/™ fold, EN/DE
            # fold) — the generic label dupe reported "ITIL® Foundation" vs
            # "ITIL Foundation Level" as lost although the applier merged it.
            verdict = classify_certification_dupe(
                name=getattr(entry, "name", None),
                issuing_organization=getattr(entry, "issuing_organization", None),
                credential_id=getattr(entry, "credential_id", None),
                existing=merged_entries,
                name_getter=lambda c: getattr(c, "name", None),
                org_getter=lambda c: getattr(c, "issuing_organization", None),
                credential_id_getter=lambda c: getattr(c, "credential_id", None),
            )
        elif section == "education":
            # #618 (education half) — same N1 shape as certifications above:
            # the REAL applier (`_apply_upsert_education`) decides identity
            # with the education-aware instrument (institution-alias fold,
            # date-range containment, mechanical degree fold) since this
            # session; the generic label dupe would otherwise report a pair
            # the applier itself just merged as a loss (ADR-066 — one logical
            # operation, one implementation; the exact seam #618's cert half
            # closed for certifications, mirrored here for education).
            verdict = classify_education_dupe(
                institution=getattr(entry, "institution", None),
                degree=getattr(entry, "degree", None),
                start_date=getattr(entry, "start_date", None),
                end_date=getattr(entry, "end_date", None),
                existing=merged_entries,
                institution_getter=lambda e: getattr(e, "institution", None),
                degree_getter=lambda e: getattr(e, "degree", None),
                start_date_getter=lambda e: getattr(e, "start_date", None),
                end_date_getter=lambda e: getattr(e, "end_date", None),
            )
        else:
            incoming_dict = {f: getattr(entry, f, None) for f in fields}
            verdict = classify_dupe(
                incoming_dict, merged_entries, getters, containment_is_same=containment_is_same
            )
        if verdict.match is not None:  # arm (b)
            continue
        if key in op_keys:  # arm (c), sub-clause 1
            continue
        if touched_entries and any(
            any(
                _norm(getattr(entry, f, "") or "") == _norm(getattr(touched, f, "") or "")
                and _norm(getattr(entry, f, "") or "")
                for f in fields
            )
            for touched in touched_entries
        ):  # arm (c), sub-clause 2 — the op must have targeted THIS entry
            continue
        items.append(
            ImportNotApplied(
                section=section, label=_format_label(entry, fields), reason="no_op_carried_entry"
            )
        )
    return items


def _merged_id_to_org(merged: MasterProfileData) -> dict[str, tuple[str, str | None, str | None]]:
    """Every merged engagement entity's id -> (section, its own org value)."""
    result: dict[str, tuple[str, str | None, str | None]] = {}
    for section in _ENGAGEMENT_SECTIONS:
        for entry in getattr(merged, section.name):
            entry_id = getattr(entry, "id", None)
            if entry_id:
                result[entry_id] = (
                    section.name,
                    getattr(entry, section.org_field, None),
                    getattr(entry, "start_date", None),
                )
    return result


def _same_month_or_unknown(a: str | None, b: str | None) -> bool:
    """Two engagement start dates name the same stint when their YYYY-MM prefixes
    agree; a side with no date at all (a LinkedIn text without dates) is a
    wildcard, so a dated existing entity still rescues an undated incoming one.
    Without this, an `add_bullets` against the 2020 stint rescued the CV's
    separate 2013 stint at the same employer (B1)."""
    if not a or not b:
        return True
    return _norm(str(a))[:7] == _norm(str(b))[:7]


def _op_touched_orgs(
    ops: Sequence[CommitOp], merged: MasterProfileData
) -> dict[str, list[tuple[str, str | None]]]:
    """Arm (c), sub-clause 2 — the org string every op-WITH-A-TARGET touches,
    per engagement section. See the module docstring's "known limitation" for
    the local-ref/ambiguous-parking edge this does not attempt to close.
    """
    id_to_org = _merged_id_to_org(merged)

    # local ref -> (section, org) — resolved via the entity op's OWN target
    # (a real id, ground truth from `merged`) or, for a fresh entity, the
    # entity op's own declared org value.
    ref_to_org: dict[str, tuple[str, str | None, str | None]] = {}
    for op in ops:
        section = _ENGAGEMENT_SECTION_BY_OP.get(type(op))
        if section is None:
            continue
        ref = getattr(op, "ref", None)
        if not ref:
            continue
        target = getattr(op, "target", None)
        if target and target in id_to_org:
            ref_to_org[ref] = id_to_org[target]
        else:
            ref_to_org[ref] = (
                section.name,
                getattr(op, section.org_field, None),
                getattr(op, "start_date", None),
            )

    touched: dict[str, list[tuple[str, str | None]]] = {s.name: [] for s in _ENGAGEMENT_SECTIONS}
    for op in ops:
        target: str | None = None
        if isinstance(op, (AddBullets, SetField)):
            target = op.target
        elif isinstance(op, _ENGAGEMENT_OP_TYPES) and op.target is not None:
            target = op.target
        if target is None:
            continue
        resolved = id_to_org.get(target) or ref_to_org.get(target)
        if resolved is None:
            continue
        section_name, org, start = resolved
        if org:
            touched[section_name].append((org, start))
    return touched


def _engagement_section_not_applied(
    section: _EngagementSection,
    incoming: MasterProfileData,
    merged: MasterProfileData,
    ops: Sequence[CommitOp],
    touched_orgs: dict[str, list[tuple[str, str | None]]],
) -> list[ImportNotApplied]:
    fields = WITNESS_KEYS[section.name]  # (org_field, "role", "start_date") — B1
    incoming_entries = getattr(incoming, section.name)
    merged_entries = getattr(merged, section.name)
    merged_keys = {_entry_key(e, fields) for e in merged_entries}
    op_keys = _op_natural_keys(ops, section.op_type, fields)
    section_touched = touched_orgs.get(section.name, [])

    items: list[ImportNotApplied] = []
    seen: set[tuple[str, ...]] = set()
    for entry in incoming_entries:
        key = _entry_key(entry, fields)
        if key in seen:
            continue
        seen.add(key)
        if key in merged_keys:  # arm (a)
            continue
        org = getattr(entry, section.org_field, None)
        role = getattr(entry, "role", None)
        start_date = getattr(entry, "start_date", None)
        verdict = classify_engagement_dupe(
            org=org, role=role, start_date=start_date,
            existing=merged_entries,
            org_getter=lambda e, f=section.org_field: getattr(e, f, None),
        )
        if verdict.match is not None:  # arm (b), MATCH only
            continue
        if key in op_keys:  # arm (c), sub-clause 1
            continue
        if any(
            _field_relation(org, touched_org, containment_is_same=False) == _SAME
            and _same_month_or_unknown(start_date, touched_start)
            for touched_org, touched_start in section_touched
        ):  # arm (c), sub-clause 2 — the op must have targeted THIS stint (B1)
            continue
        items.append(
            ImportNotApplied(
                section=section.name, label=_format_label(entry, fields), reason="no_op_carried_entry"
            )
        )
    return items


def compute_import_not_applied(
    incoming: MasterProfileData,
    merged: MasterProfileData,
    ops: Sequence[CommitOp],
    *,
    rejected_ops: Sequence[str] = (),
) -> list[ImportNotApplied]:
    """Every incoming list-section entry the merge's own ops do not carry,
    plus every raw op ``rejected_ops`` names as parse-dropped.

    Pure and side-effect-free — no DB, no LLM. See the module docstring for
    the full carried-predicate (arms a/b/c) and its named limitations.
    """
    items: list[ImportNotApplied] = [
        ImportNotApplied(
            section=None,
            label=(op_type if isinstance(op_type, str) and op_type else "<unknown>"),
            reason="op_rejected",
        )
        for op_type in rejected_ops
    ]

    for section in _FLAT_SECTIONS:
        items.extend(_flat_section_not_applied(section, incoming, merged, ops))

    touched_orgs = _op_touched_orgs(ops, merged)
    for section in _ENGAGEMENT_SECTIONS:
        items.extend(
            _engagement_section_not_applied(section, incoming, merged, ops, touched_orgs)
        )

    return items
