# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The import path's alternate-name writer (ADR-046 amended 2026-10-07, cl. 2(a);
#709 #716) and the receipt payload the #717 undo needs.

Runs in ``import_bridge.reconcile_import`` after ``apply_ops``, on the merged
profile the import is about to install through ``ApplyImportMerge`` — i.e. it is
part of the import's ONE write (ADR-063), receipted on the import's own
``changes`` and undone by ADR-042 like every other import write.

What it copies, and from where. For every ``match_existing`` op the import
witness's own target-first binder (``import_witness.match_existing_bindings``)
binds to EXACTLY ONE incoming document entry, each natural-key field of that
entry whose value is a NEW name for the target (``aliases.add_alias``: not the
target's own value, not already recorded, not the target's OTHER key field) is
recorded as that field's alias. Engagements additionally need both start months
stated and equal. The text is the document's — never the op's free-text
``incoming`` (adversarial BLOCKER, 2026-10-07) and never a translation.

Writer (b) — a TARGETED engagement upsert (``upsert_work`` /
``upsert_volunteer`` with ``target``) — runs here too, never in the shared
applier (adversarial finding 6, 2026-10-07): the name recorded is the ONE
incoming document entry's own employer / organisation (same stated start month
as the target, and tied to the op by its own org or role text), never the op's
``company`` — which is the model's spelling and may be a translation no
document states.

Every alias CARRY — the witness's alternate-name arm, even when the model
emitted no op at all — is receipted here as ``basis="alias"`` on the import's
``matched`` (finding 2; ADR-046 am. cl. 7), so the import summary shows it and
"Nicht dasselbe" can take it back. A language pair the DE/EN table names is
receipted as ``name_table`` and never aliased (finding 5).

Turn doors (interview, testimony, agent claims) never call this: they have no
incoming document entry to copy from, and their ``bool(changes)`` means "the gap
is addressed" (ADR-046 am. 2026-09-16, refuter BLOCKER).
"""
from __future__ import annotations

from typing import Any, Sequence

from applire.schemas.profile import FieldChange, MasterProfileData, MatchReceipt
from applire.services.profile.reconcile import aliases as _aliases
from applire.services.profile.reconcile.apply import _ENTRY_NATURAL_KEYS, _merged, _norm
from applire.services.profile.language_names import same_language
from applire.services.profile.reconcile.import_witness import alias_carries, match_existing_bindings
from applire.services.profile.reconcile.ops import UpsertVolunteer, UpsertWork
from applire.services.prompt_view import prompt_incoming_view

_SECTIONS: tuple[str, ...] = (
    "skills", "certifications", "languages", "education", "publications",
    "signature_stories", "work_experience", "projects", "volunteer_activities",
)


def _entry_payload(entry: Any) -> dict[str, Any]:
    """The incoming entry as the document stated it, bookkeeping stripped (the
    same view the reconcile prompt renders, ADR-078)."""
    dumped = entry.model_dump(mode="json") if hasattr(entry, "model_dump") else dict(entry)
    view = prompt_incoming_view(dumped)
    return view if isinstance(view, dict) else {}


def record_bound_aliases(
    incoming: MasterProfileData,
    merged: MasterProfileData,
    ops: Sequence[Any],
    matched: list[MatchReceipt],
    changes: list[FieldChange],
) -> None:
    """Mutates ``merged`` (aliases), ``matched`` (``aliases_added`` /
    ``incoming_entry``) and ``changes`` (one ``merged <f>_aliases`` per alias)."""
    receipts_by_op_target: dict[tuple[str, str], list[MatchReceipt]] = {}
    for receipt in matched:
        receipts_by_op_target.setdefault((receipt.entity_id, _norm(receipt.incoming)), []).append(receipt)

    for section in _SECTIONS:
        bindings = match_existing_bindings(
            section, getattr(incoming, section), getattr(merged, section), ops
        )
        alias_map = _aliases.ALIAS_FIELDS.get(section, {})
        for op, target, entry in bindings:
            receipts = receipts_by_op_target.get((str(op.target), _norm(op.incoming)), [])
            receipt = receipts[0] if receipts else None
            if receipt is not None and receipt.incoming_entry is None:
                receipt.incoming_entry = _entry_payload(entry)
            if section == "languages" and same_language(
                getattr(entry, "language", None), getattr(target, "language", None)
            ):
                # The table names this pair: a fact, never an alias (finding 5).
                if receipt is not None:
                    receipt.basis = "name_table"
                continue
            if not alias_map:
                continue
            if section in _aliases.ENGAGEMENT_SECTIONS and not _aliases.months_agree(
                getattr(entry, "start_date", None), getattr(target, "start_date", None)
            ):
                continue
            for field, alias_field in alias_map.items():
                value = getattr(entry, field, None)
                if isinstance(value, str) and _aliases.add_alias(
                    target, field, section, value, siblings=getattr(merged, section)
                ):
                    changes.append(_merged(section, alias_field, None, value.strip()))
                    if receipt is not None:
                        receipt.aliases_added[alias_field] = value.strip()

    _record_targeted_upsert_aliases(incoming, merged, ops, changes)
    _record_alias_carries(incoming, merged, matched)

    # Applier-made receipts (`alias` / `name_table` basis) carry no op to bind
    # through; attach the ONE incoming entry whose own label or key field equals
    # the receipt's `incoming`, so an undo can add back what the document said.
    for receipt in matched:
        if receipt.incoming_entry is not None or receipt.section not in _SECTIONS:
            continue
        fields = _ENTRY_NATURAL_KEYS.get(receipt.section, ())
        wanted = _norm(receipt.incoming)
        hits = [
            e for e in getattr(incoming, receipt.section)
            if _norm(" / ".join(str(getattr(e, f, "") or "").strip() for f in fields if getattr(e, f, None))) == wanted
            or any(_norm(getattr(e, f, "") or "") == wanted for f in fields)
        ]
        if len(hits) == 1:
            receipt.incoming_entry = _entry_payload(hits[0])


_ORG_FIELD: dict[str, str] = {"work_experience": "company", "volunteer_activities": "organization"}


def _record_targeted_upsert_aliases(
    incoming: MasterProfileData,
    merged: MasterProfileData,
    ops: Sequence[Any],
    changes: list[FieldChange],
) -> None:
    """Writer (b), ADR-046 am. cl. 2(b), import door only (finding 6). For a
    targeted engagement upsert, the ONE incoming document entry with the
    target's stated start month whose own org or role text equals the op's
    gives the employer's other name — the DOCUMENT's text, never ``op.company``."""
    for op in ops:
        if isinstance(op, UpsertWork):
            section = "work_experience"
        elif isinstance(op, UpsertVolunteer):
            section = "volunteer_activities"
        else:
            continue
        if op.target is None:
            continue
        org_field = _ORG_FIELD[section]
        target = next((e for e in getattr(merged, section) if getattr(e, "id", None) == op.target), None)
        if target is None:
            continue
        op_org, op_role = _norm(getattr(op, org_field, "") or ""), _norm(getattr(op, "role", "") or "")
        hits = [
            e for e in getattr(incoming, section)
            if _aliases.months_agree(getattr(e, "start_date", None), getattr(target, "start_date", None))
            and (
                (op_org and _norm(getattr(e, org_field, "") or "") == op_org)
                or (op_role and _norm(getattr(e, "role", "") or "") == op_role)
            )
        ]
        if len(hits) != 1:
            continue
        value = getattr(hits[0], org_field, None)
        if isinstance(value, str) and _aliases.add_alias(
            target, org_field, section, value, siblings=getattr(merged, section)
        ):
            changes.append(_merged(section, _aliases.ALIAS_FIELDS[section][org_field], None, value.strip()))


def _label(entry: Any, section: str) -> str:
    fields = _ENTRY_NATURAL_KEYS.get(section, ())
    return " / ".join(
        str(getattr(entry, f, "") or "").strip() for f in fields if str(getattr(entry, f, "") or "").strip()
    )


def _record_alias_carries(
    incoming: MasterProfileData, merged: MasterProfileData, matched: list[MatchReceipt]
) -> None:
    """Finding 2: every entry the witness carries THROUGH a recorded alias gets a
    ``basis="alias"`` receipt on this import, unless a receipt for that entity
    and name is already there (the applier's own, or the model's
    ``match_existing``). Same predicate as the witness (``alias_carries``)."""
    for section in _aliases.ALIAS_FIELDS:
        fields = _ENTRY_NATURAL_KEYS.get(section, ())
        for entry, carrier in alias_carries(section, getattr(incoming, section), getattr(merged, section)):
            label = _label(entry, section)
            names = {_norm(label)} | {_norm(getattr(entry, f, "") or "") for f in fields}
            carrier_id = str(getattr(carrier, "id", ""))
            if any(r.entity_id == carrier_id and _norm(r.incoming) in names for r in matched):
                continue
            existing = _label(carrier, section) or carrier_id
            matched.append(MatchReceipt(
                section=section, entity_id=carrier_id, incoming=label, existing=existing,
                basis="alias", incoming_entry=_entry_payload(entry),
            ))
