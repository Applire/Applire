# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""Add user_settings.dismissed_explainers (#679, US309).

One per-user set of first-use explainer ids the user dismissed with
"Nicht mehr anzeigen" — the general mechanism behind #679, whose first
consumer is the fact-pin explainer (`fact_pins_intro`, COPY.md §D/§F).
A set instead of a boolean column per notice: the next explainer costs an
allowlist entry in `routers/settings.py`, not a migration.

`hide_predownload_notice` (migration 0042, ADR-040 §4) is deliberately left
alone. It predates the mechanism, the frontend already reads it, and folding
it in would be a data migration of a live preference for no user-visible
gain.

NOT NULL with server_default '[]' — every existing row is back-filled to the
empty set on upgrade, so a user who has dismissed nothing and a user who
never saw the column are indistinguishable, which is exactly right. The
service still normalises None -> [] for the transient in-Python state before
the default is applied (the 0060 `review_mode` precedent).

Additive column on the 0060 (review_mode) / 0058 (pinned_facts) pattern.
Deliberately not exposed over MCP (ADR-081 clause 8, SF-DOOR.4).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite.
_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column(
            "dismissed_explainers",
            _JSON,
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "dismissed_explainers")
