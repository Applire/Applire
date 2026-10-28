# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""U-1r (Hazelnut build 5, ruling A1-U1r; ADR-046 amended 2026-10-28): a
bullet conflict must declare the two values it disputes.

Rule 13's test asks the model, before every bullet conflict, to "name the ONE
thing both bullets are about and the two different VALUES they give it". Replays
of the delivery run showed the model skipping that test in about one run in
three. It flagged rewordings of the same work, and two different duties of one
role, as conflicts. Prompt wording did not converge (see the version header
in ``prompts/reconcile.py``).

So the op now carries the declaration (``FlagConflict.values``). Code checks
only what the model itself declared; whether two bullets contradict stays the
model's judgement (ADR-062). A bullet conflict is NOT checkable, and is not
raised, when:

- ``values`` is missing or does not hold exactly two values;
- the two values are equal after normalisation;
- a value does not occur in its own bullet.

Such a conflict is routed the way the engine routes any new bullet: an
``add_bullets`` of the incoming bullet against the same entry. On an import the
restatement check (``restatement.hold_back_restatements``, D-B3V6) then holds
back a bullet that restates a held one and receipts it as restated. A bullet
that states a new fact is written. Either way the existing bullet stays, so
nothing is lost and nothing is doubled.

A scalar conflict is untouched: its two sides ARE the values.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from applire.services.profile.reconcile.ops import AddBullets, FlagConflict

logger = logging.getLogger(__name__)

BULLET_FIELDS = frozenset({"responsibilities", "achievements"})
_SPACE = re.compile(r"\s+")


def _norm(text: Any) -> str:
    folded = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return _SPACE.sub(" ", folded).strip(" .,;:!?\"'()[]")


def unchecked_reason(op: FlagConflict) -> str | None:
    """Why a bullet conflict's own declaration does not hold, or ``None``."""
    values = op.values
    if not isinstance(values, list) or len(values) != 2:
        return "values_missing"
    old, new = (_norm(v) for v in values)
    if not old or not new:
        return "values_missing"
    if old == new:
        return "values_equal"
    if old not in _norm(op.existing) or new not in _norm(op.incoming):
        return "value_not_in_bullet"
    return None


def route_unchecked_conflicts(ops: list) -> list:
    """``ops`` with every bullet conflict whose declaration does not hold
    replaced by an ``add_bullets`` of its incoming bullet."""
    out: list = []
    for op in ops:
        if (
            isinstance(op, FlagConflict)
            and op.field in BULLET_FIELDS
            and isinstance(op.incoming, str)
            and op.incoming.strip()
        ):
            reason = unchecked_reason(op)
            if reason is not None:
                logger.warning(
                    "reconcile: bullet conflict on %s.%s not raised (%s); the incoming "
                    "bullet goes the add_bullets way (U-1r)", op.target, op.field, reason,
                )
                out.append(AddBullets(target=op.target, **{op.field: [op.incoming.strip()]}))
                continue
        out.append(op)
    return out
