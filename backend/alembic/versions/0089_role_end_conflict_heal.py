# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A stored dispute that only states a role's end is the fact itself (Hazelnut build 7, UX audit round 4).

UX audit round 4 (Emma, Sonnet 5.5): on a four-vintage merge the reconciler parked
``flag_conflict`` disputes on ``is_current`` ("true (current position, no end date)"
vs "false (ended 2024-02)") and ``end_date`` ("none (current position)" vs
"2024-02") instead of writing the end date. The role stayed "→ heute", and an
answer wrote the prose into the boolean field (409 on every answer).

Build 7 stops parking that shape: ``apply_ops`` runs
``reconcile.apply.settle_role_end_conflicts`` over new AND stored disputes on every
write (ruling MD7-12 follows the 0088 precedent: heal at upgrade, not at some later
write). This step does the same for every stored vault now:

* a dispute on ``end_date`` whose entry has no end date and whose incoming end date
  has passed → the end date is written, the dispute dropped;
* a dispute on ``is_current`` whose incoming side says "not current" and whose entry's
  end date has passed → dropped;
* then every entry with a passed end date AND ``is_current = true`` is closed (the
  0087 repair, which the committer's ``close_ended_roles`` does on every write).

Two different past end dates, a later source calling an ended role current, a future
end date, and an unreadable value all stay disputes. Same rules, same order as the app.

This file holds a VERBATIM copy of ``reconcile/role_end.py``'s primitives and
``schemas.profile.normalise_partial_date`` (a migration never imports application
code — the 0086/0087/0088 precedent). ``backend/tests/unit/test_migration_0089_role_end_heal.py``
pins that the copy and the app agree.

* Only live vaults (``deleted_at IS NULL``). No receipt is written (as 0087): the
  repair resolves a contradiction already stored. No timestamp column moves.
* Idempotent: a healed dispute is gone; a second run changes nothing.
* Logs counts, never content.

**Downgrade is a no-op**: the dropped disputes restated a fact the vault now holds.

Chain: 0088 (build 7, older-wording denial heal) -> 0089.
"""

import json
import logging
import re
from datetime import date, datetime
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0089"
down_revision: Union[str, None] = "0088"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

# ── verbatim copy: schemas/profile.py normalise_partial_date ──────────────────

_PRECISION_FORMS: tuple[tuple[str, Any, str], ...] = (
    (r"\d{4}", "%Y", "%Y"),
    (r"\d{4}-\d{1,2}", "%Y-%m", "%Y-%m"),
    (r"\d{4}/\d{1,2}", "%Y/%m", "%Y-%m"),
    (r"\d{1,2}[./]\d{4}", None, "%Y-%m"),
    (r"\d{4}-\d{1,2}-\d{1,2}", "%Y-%m-%d", "%Y-%m-%d"),
    (r"\d{1,2}\.\d{1,2}\.\d{4}", "%d.%m.%Y", "%Y-%m-%d"),
    (r"\d{1,2}/\d{1,2}/\d{4}", "%d/%m/%Y", "%Y-%m-%d"),
)


def normalise_partial_date(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*", s):
        s = s[:10]
    for pattern, fmt, out in _PRECISION_FORMS:
        if not re.fullmatch(pattern, s):
            continue
        if fmt is None:
            month, year = re.split(r"[./]", s)
            s, fmt = f"{year}-{month}", "%Y-%m"
        try:
            return datetime.strptime(s, fmt).strftime(out)
        except ValueError:
            return None
    return None


# ── verbatim copy: services/profile/reconcile/role_end.py ─────────────────────

BOOL_CONFLICT_FIELDS = frozenset({"is_current"})
DATE_CONFLICT_FIELDS = frozenset({"start_date", "end_date"})
DATED_ENTRY_SECTIONS = ("work_experience", "projects", "volunteer_activities")

_TRUE = frozenset({"true", "yes", "ja", "current", "aktuell"})
_FALSE = frozenset({"false", "no", "nein", "ended", "beendet"})
_NO_DATE = frozenset({"none", "null", "present", "heute", "current", "aktuell", "today", "ongoing"})
_LEAD = re.compile(r"\s*([^\s(),;]+)")

UNREADABLE = object()


def coerce_conflict_value(field: str, value: Any) -> Any:
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
    field: str, entity_end_date: Any, incoming: Any, today: date
) -> str | None:
    has_end = normalise_partial_date(entity_end_date) is not None
    if field == "end_date":
        if not has_end and incoming is not None and _passed(incoming, today):
            return "fill_end"
        return None
    if field == "is_current":
        if incoming is False and has_end and _passed(entity_end_date, today):
            return "settled"
        return None
    return None


# ── the heal (dict form of reconcile.apply.settle_role_end_conflicts + close_ended_roles) ──


def heal(data: Any, today: date | None = None) -> tuple[int, int]:
    """Heal one stored vault in place. Returns (disputes dropped, roles closed)."""
    if not isinstance(data, dict):
        return 0, 0
    today = today or date.today()
    entries: dict[Any, tuple[str, dict]] = {}
    for section in DATED_ENTRY_SECTIONS:
        items = data.get(section)
        if not isinstance(items, list):
            continue
        for e in items:
            if isinstance(e, dict) and e.get("id"):
                entries[e["id"]] = (section, e)
    meta = data.get("metadata")
    stored = meta.get("pending_conflicts") if isinstance(meta, dict) else None
    dropped: set[str] = set()
    if isinstance(stored, list):
        for wanted in ("end_date", "is_current"):
            for c in stored:
                if not isinstance(c, dict) or c.get("resolved") or c.get("field") != wanted:
                    continue
                cid = c.get("conflict_id")
                if cid in dropped:
                    continue
                hit = entries.get(c.get("entity_id"))
                if hit is None or c.get("section") != hit[0]:
                    continue
                incoming = coerce_conflict_value(wanted, c.get("incoming_value"))
                if incoming is UNREADABLE:
                    continue
                verdict = role_end_verdict(wanted, hit[1].get("end_date"), incoming, today)
                if verdict is None:
                    continue
                if verdict == "fill_end":
                    hit[1]["end_date"] = incoming
                dropped.add(cid)
        if dropped:
            meta["pending_conflicts"] = [
                c for c in stored if not (isinstance(c, dict) and c.get("conflict_id") in dropped)
            ]
    closed = 0
    for section in DATED_ENTRY_SECTIONS:
        items = data.get(section)
        if not isinstance(items, list):
            continue
        for e in items:
            if isinstance(e, dict) and e.get("is_current") is True and _passed(e.get("end_date"), today):
                e["is_current"] = False
                closed += 1
    return len(dropped), closed


_profiles = sa.table(
    "master_profiles",
    sa.column("id"),
    sa.column("profile_json", sa.JSON().with_variant(JSONB(), "postgresql")),
    sa.column("deleted_at"),
)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(_profiles.c.id, _profiles.c.profile_json).where(_profiles.c.deleted_at.is_(None))
    ).fetchall()
    vaults = disputes = roles = 0
    for row_id, raw in rows:
        data = json.loads(raw) if isinstance(raw, str) else raw
        n_disputes, n_roles = heal(data)
        if n_disputes or n_roles:
            bind.execute(_profiles.update().where(_profiles.c.id == row_id).values(profile_json=data))
            vaults += 1
            disputes += n_disputes
            roles += n_roles
    log.info("0089: healed %d vault(s): %d role-end dispute(s) dropped, %d role(s) closed", vaults, disputes, roles)


def downgrade() -> None:
    log.info("0089 downgrade: nothing to restore (the dropped disputes restated a stored fact)")
