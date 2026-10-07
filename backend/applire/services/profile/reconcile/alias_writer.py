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

Turn doors (interview, testimony, agent claims) never call this: they have no
incoming document entry to copy from, and their ``bool(changes)`` means "the gap
is addressed" (ADR-046 am. 2026-09-16, refuter BLOCKER).
"""
from __future__ import annotations

from typing import Any, Sequence

from applire.schemas.profile import FieldChange, MasterProfileData, MatchReceipt
from applire.services.profile.reconcile import aliases as _aliases
from applire.services.profile.reconcile.apply import _ENTRY_NATURAL_KEYS, _merged, _norm
from applire.services.profile.reconcile.import_witness import match_existing_bindings
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
            if not alias_map:
                continue
            if section in _aliases.ENGAGEMENT_SECTIONS and not _aliases.months_agree(
                getattr(entry, "start_date", None), getattr(target, "start_date", None)
            ):
                continue
            for field, alias_field in alias_map.items():
                value = getattr(entry, field, None)
                if isinstance(value, str) and _aliases.add_alias(target, field, section, value):
                    changes.append(_merged(section, alias_field, None, value.strip()))
                    if receipt is not None:
                        receipt.aliases_added[alias_field] = value.strip()

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
