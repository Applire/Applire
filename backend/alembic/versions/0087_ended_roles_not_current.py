# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A role whose end date has passed is no longer stored as current (#822, Hazelnut build 7).

UX audit round 3 (2026-11-07): merging several CV vintages left roles with an end
date in the past AND ``is_current = true`` — an older vintage marked the role
current, a newer one closed it, and the fill-only merge filled the date but kept
the flag. The profile page then showed three jobs at once as "→ heute".

Build 7 stops minting such rows: ``apply_ops`` ends every write with
``close_ended_roles`` (ADR-063 amended 2026-11-07). This step repairs the rows
that already exist: every ``work_experience`` / ``projects`` /
``volunteer_activities`` entry with ``is_current = true`` and an end date that has
passed gets ``is_current = false``.

The predicate is a VERBATIM copy of ``reconcile.apply.end_date_has_passed`` (and
the date normaliser it reads, ``schemas.profile.normalise_partial_date``) because
a migration never imports application code (the 0083 / 0086 precedent);
``backend/tests/unit/test_migration_0087_ended_roles.py`` pins that the two agree.

* Only live vaults (``deleted_at IS NULL``) are touched; no timestamp column moves.
* Idempotent: a repaired entry no longer matches.
* No receipt is written: the repair resolves a contradiction already stored in the
  entry; the next write through ``commit_ops`` would make the same change with a
  receipt.

**Downgrade** is a no-op: the old state was a contradiction, not a value worth
restoring, and which entries it held is not recorded.

Chain: 0086 (offered-choice denial heal) -> 0087.
"""

import json
import logging
import re
from datetime import date, datetime
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0087"
down_revision: Union[str, None] = "0086"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

# ── verbatim copy of the app's predicate (schemas/profile.py, reconcile/apply.py) ──

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


def end_date_has_passed(end_date: Any, today: date) -> bool:
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


_DATED_ENTRY_SECTIONS = ("work_experience", "projects", "volunteer_activities")

# ── the repair ─────────────────────────────────────────────────────────────────


def repair(data: Any, today: date | None = None) -> int:
    """Clear ``is_current`` on every entry whose end date has passed. Returns the
    number of entries changed; mutates ``data`` in place."""
    if not isinstance(data, dict):
        return 0
    today = today or date.today()
    n = 0
    for section in _DATED_ENTRY_SECTIONS:
        entries = data.get(section)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("is_current") is not True:
                continue
            if end_date_has_passed(entry.get("end_date"), today):
                entry["is_current"] = False
                n += 1
    return n


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
    total = 0
    for row_id, raw in rows:
        data = json.loads(raw) if isinstance(raw, str) else raw
        n = repair(data)
        if n:
            bind.execute(_profiles.update().where(_profiles.c.id == row_id).values(profile_json=data))
            total += n
    log.info("0087: cleared is_current on %d ended role(s)", total)


def downgrade() -> None:
    log.info("0087 downgrade: nothing to restore (the repaired state was a contradiction)")
