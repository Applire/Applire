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

"""`instance_state` — the one home for a fact about THIS installation (ADR-087 cl. 5).

Not a user's data (so not `user_settings` — and ADR-022 makes "the user" singular,
which would make a per-user row a lie about what the fact is) and not configuration
(so not the environment). Key/value, JSON payload, one row per key.

Holds **no personal data**, therefore no TTL and no retention rule (ADR-005). A
future key that *would* hold personal data belongs in a typed table instead.
Deliberately not on the MCP surface (ADR-054; the `review_mode` / SF-DOOR.4
precedent): an ADR-054 BYOI agent has no instance to operate.

The keys are module constants rather than string literals at the call sites,
because the table is schema-less by construction and a typo writes a *new* key
instead of failing (System-FMEA SF-CFG.5). `scripts/backup.sh` writes
`last_backup_at` from shell, outside every type checker — the literal there is
pinned to `KEY_LAST_BACKUP_AT` by a unit test, and that test is the only thing
joining the two languages.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite (unit tests).
_JSON = JSONB().with_variant(JSON(), "sqlite")

#: The release that last ran against this database. Written on the first start
#: of a fresh install, and thereafter advanced ONLY by the upgrade-notice
#: dismissal (ADR-087 cl. 7) — never by the mere fact of having started, or the
#: notice about a silent change would itself be visible for exactly one boot.
KEY_LAST_SEEN_VERSION = "last_seen_version"

#: The release for which the operator dismissed the version-jump notice. Lets
#: `/health.upgrade_notice` clear without a restart.
KEY_UPGRADE_NOTICE_DISMISSED_FOR = "upgrade_notice_dismissed_for"

#: ISO-8601 UTC timestamp of the last successful `scripts/backup.sh` run
#: (US314). Read by the ops layer's probe, which reports "last backup: never /
#: N days" and warns at >= 30 days (US312, WP-O1).
KEY_LAST_BACKUP_AT = "last_backup_at"

#: Every key this table is allowed to carry. New key => new constant here.
KNOWN_KEYS = frozenset(
    {
        KEY_LAST_SEEN_VERSION,
        KEY_UPGRADE_NOTICE_DISMISSED_FOR,
        KEY_LAST_BACKUP_AT,
    }
)


class InstanceState(Base):
    """One fact about this installation, addressed by key."""

    __tablename__ = "instance_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(_JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
