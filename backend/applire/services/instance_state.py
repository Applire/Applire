# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""Typed access to `instance_state` (ADR-087 clause 5).

Two functions and three named keys. Deliberately small: the table is a
key/value store, and the value of having a service module at all is that
every reader and writer goes through the same key constants (SF-CFG.5).

`read_state` tolerates the table being ABSENT. The lifespan runs migrations
before it calls anything here, so in the app that cannot happen — but the ops
layer (WP-O1) and any script may read the table on an instance that has not
been upgraded past 0062 yet, and "the table is not there" must read as
"nothing recorded", never as a 500.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.instance_state import (  # noqa: F401  (re-exported)
    KEY_LAST_BACKUP_AT,
    KEY_LAST_SEEN_VERSION,
    KEY_UPGRADE_NOTICE_DISMISSED_FOR,
    KNOWN_KEYS,
    InstanceState,
)

logger = logging.getLogger("applire.instance_state")


async def read_state(db: AsyncSession, key: str) -> Any | None:
    """Return the stored value for `key`, or None when nothing is recorded.

    Returns None — never raises — when the table does not exist yet, so a
    reader on a pre-0062 instance sees "nothing recorded".
    """
    if key not in KNOWN_KEYS:
        raise ValueError(f"Unknown instance_state key: {key!r}. Add a constant first.")
    try:
        result = await db.execute(
            select(InstanceState.value).where(InstanceState.key == key)
        )
    except DatabaseError:
        # Pre-0062 instance: no table. Nothing is recorded, and that is an
        # answer, not a failure.
        await db.rollback()
        logger.debug("instance_state table absent — %s reads as unset", key)
        return None
    return result.scalar_one_or_none()


async def write_state(db: AsyncSession, key: str, value: Any) -> None:
    """Upsert `key`. The caller commits."""
    if key not in KNOWN_KEYS:
        raise ValueError(f"Unknown instance_state key: {key!r}. Add a constant first.")
    row = await db.get(InstanceState, key)
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(InstanceState(key=key, value=value, updated_at=now))
    else:
        row.value = value
        row.updated_at = now
