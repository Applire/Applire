# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""UX audit round 4 (Emma, Sonnet 5.5) — a role's end is a FACT, never a dispute.

#822 made the committer close every role whose end date has passed
(``close_ended_roles``). On a four-vintage merge Sonnet did not write the end
date at all: it emitted ``flag_conflict`` on ``is_current`` ("true (current
position, no end date)" vs "false (ended 2024-02)") and on ``end_date``
("none (current position)" vs "2024-02"). The two disputes parked the fact the
code computes (ADR-062 cl. 1) behind the candidate's answer, the role stayed
"→ heute", and the prose values were later written verbatim into the boolean
field (a 409 on every answer).

Two rules, one module:

* **Typed values.** A conflict on a typed field (``is_current``, a date) holds
  values of that type. ``coerce_conflict_value`` reads the model's value (a
  bare literal, or the literal leading a parenthetical gloss); a value it
  cannot read is refused, never stored as prose.
* **The end of a role.** One side "current / no end date", the other a PAST end
  date for the same role, is not a conflict: ``role_end_verdict`` says
  ``"fill_end"`` (write the end date into the empty slot) or ``"settled"`` (the
  stored end date has passed, so ``is_current`` → false is what the committer
  does anyway). Two different past end dates, or a later source calling an
  ended role current again, stay real disputes.

``alembic/versions/0089_role_end_conflict_heal.py`` carries a verbatim copy of
the primitives below (a migration never imports app code); the parity test
pins that the two agree.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from applire.schemas.profile import normalise_partial_date

#: Fields whose conflict values have a type the profile enforces.
BOOL_CONFLICT_FIELDS = frozenset({"is_current"})
DATE_CONFLICT_FIELDS = frozenset({"start_date", "end_date"})
DATED_ENTRY_SECTIONS = ("work_experience", "projects", "volunteer_activities")

_TRUE = frozenset({"true", "yes", "ja", "current", "aktuell"})
_FALSE = frozenset({"false", "no", "nein", "ended", "beendet"})
#: Words that state "no end date" for a date field.
_NO_DATE = frozenset({"none", "null", "present", "heute", "current", "aktuell", "today", "ongoing"})
_LEAD = re.compile(r"\s*([^\s(),;]+)")

#: Returned when a value cannot be read as the field's type.
UNREADABLE = object()


def coerce_conflict_value(field: str, value: Any) -> Any:
    """``value`` as the type ``field`` holds, or ``UNREADABLE``.

    A bool field reads a bool, or a word for one leading the value
    ("false (ended 2024-02)" → False). A date field reads a partial date
    ("2024-02", "02/2024"), or a no-date word ("none (current position)" →
    None), at the start of the value. Any other field is returned unchanged."""
    if field in BOOL_CONFLICT_FIELDS:
        if isinstance(value, bool):
            return value
        if not isinstance(value, str):
            return UNREADABLE
        m = _LEAD.match(value)
        word = m.group(1).casefold() if m else ""
        if word in _TRUE:
            return True
        if word in _FALSE:
            return False
        return UNREADABLE
    if field in DATE_CONFLICT_FIELDS:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        norm = normalise_partial_date(value)
        if norm:
            return norm
        if not isinstance(value, str):
            return UNREADABLE
        m = _LEAD.match(value)
        word = m.group(1) if m else ""
        if word.casefold() in _NO_DATE:
            return None
        return normalise_partial_date(word) or UNREADABLE
    return value


def _passed(end_date: Any, today: date) -> bool:
    norm = normalise_partial_date(end_date)
    if not norm:
        return False
    if len(norm) == 10:
        return norm < today.isoformat()
    if len(norm) == 7:
        return norm < today.strftime("%Y-%m")
    if len(norm) == 4:
        return norm < str(today.year)
    return False


def role_end_verdict(
    field: str, entity_end_date: Any, incoming: Any, today: date, entity_is_current: Any = None
) -> str | None:
    """``"fill_end"`` / ``"settled"`` when a conflict on a dated entry only
    states that the role has ended, or restates what the entry already holds,
    else ``None`` (a real dispute).

    ``incoming`` is the COERCED incoming value. ``"fill_end"``: the entry has no
    end date and the incoming end date has passed — the end date is written.
    ``"settled"``: the incoming side says not current and the entry's own end
    date has passed — the committer closes the role (#822); OR (ruling MD7-14)
    the incoming value equals the entry's stored value (end date normalised
    equal; the same ``is_current``) — a dispute the vault already answers is
    not a question. A "current" restated on a role whose end date has passed
    is not settled by equality: the committer closes that role."""
    has_end = normalise_partial_date(entity_end_date) is not None
    if field == "end_date":
        if not has_end and incoming is not None and _passed(incoming, today):
            return "fill_end"
        if normalise_partial_date(incoming) == normalise_partial_date(entity_end_date):
            return "settled"
        return None
    if field == "is_current":
        if incoming is False and has_end and _passed(entity_end_date, today):
            return "settled"
        if (
            isinstance(incoming, bool)
            and isinstance(entity_is_current, bool)
            and incoming == entity_is_current
            and not (incoming and _passed(entity_end_date, today))
        ):
            return "settled"
        return None
    return None
