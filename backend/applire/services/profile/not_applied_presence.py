# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-2 (Hazelnut build 5 delivery run, main ruling on UAT-2 "otherwise not
shown") — a not-applied import item that the vault already holds under another
name.

The import witness marks an incoming entry ``no_op_carried_entry`` when no op
carried it and no alias rule resolves it to an existing entry. A translated or
renamed institution ("University" / "Universität", a school's older company
name) is exactly such an entry: offered for "Übernehmen" it would create a
duplicate, and the true answer ("already there") had no exit.

Whether two entries are the same thing is a judgement (ADR-062): ONE model call
at the end of the import, over the adoptable items and the existing entries of
their sections, before the import's record is written. The verdict is stored on
the item (``already_present_as``); the hub then keeps such an item silent. A
failed call stores nothing — the item stays asked (the safe direction: the
candidate sees it).
"""
from __future__ import annotations

import logging
from typing import Any

from applire.schemas.profile import ImportNotApplied, MasterProfileData

logger = logging.getLogger(__name__)

#: Fields never shown to the judge (ids, bookkeeping); lists are omitted too.
_SKIP = {"id", "status", "source", "expected_fields", "photo_url"}


def _scalars(entry: Any) -> dict[str, Any]:
    data = entry if isinstance(entry, dict) else entry.model_dump(mode="json")
    return {
        k: v for k, v in data.items()
        if k not in _SKIP and isinstance(v, (str, int, float, bool)) and v not in ("", None)
    }


async def mark_already_present(
    items: list[ImportNotApplied], merged: MasterProfileData, provider
) -> int:
    """Set ``already_present_as`` on each adoptable item the model judges to be
    an existing entry of its section; returns how many were marked."""
    from applire.services.profile.decisions import judge_already_present

    asked = [
        i for i in items
        if i.entry and i.section and i.adopted_at is None and i.already_present_as is None
        and getattr(merged, i.section, None)
    ]
    if not asked:
        return 0
    try:
        payload = [
            {"item": f"n{n}", "section": i.section, **_scalars(i.entry)}
            for n, i in enumerate(asked)
        ]
        existing = {
            sec: [{"id": e.id, **_scalars(e)} for e in getattr(merged, sec) if getattr(e, "id", None)]
            for sec in sorted({i.section for i in asked})
        }
        found = await judge_already_present(provider, payload, existing)
    except Exception:  # noqa: BLE001 — never fail the import
        logger.warning("already-present check skipped", exc_info=True)
        return 0
    marked = 0
    for n, item in enumerate(asked):
        match = (found or {}).get(f"n{n}")
        if match:
            item.already_present_as = match
            marked += 1
    return marked
