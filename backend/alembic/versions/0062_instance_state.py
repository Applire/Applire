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

"""Add instance_state (#687, US310 / US314, ADR-087 clause 5).

One key/value table for facts about THIS installation: `last_seen_version` and
`upgrade_notice_dismissed_for` (US310's version-jump notice) and `last_backup_at`
(written by `scripts/backup.sh`, read by US312's ops probe).

No user_id column and no FK: the row is about the instance, not about a person.
It carries no personal data, so — unlike every model with PII (AGENTS.md) — it
needs neither `expires_at` nor a retention rule (ADR-005). `updated_at` is
NOT NULL with a server default so a row written by raw SQL from `backup.sh`
(which has no ORM and no timezone helper) is well-formed without naming it.

The table starts EMPTY on purpose. An absent `last_seen_version` is exactly how
"fresh install" is recognised in the lifespan, so back-filling a row here would
make every existing install look like a fresh one and suppress the first notice
this story exists to produce.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

revision: str = "0062"
down_revision: Union[str, None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite.
_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "instance_state",
        sa.Column("key", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("value", _JSON, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("instance_state")
