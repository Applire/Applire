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

"""Deterministic match score: nullable match_score + requirement_breakdown (ADR-035)

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-02

Changes:
  gap_analyses:
    - match_score: NOT NULL -> NULL (N == 0 yields NULL)
    - ADD requirement_breakdown JSONB NOT NULL DEFAULT '[]'
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "gap_analyses",
        "match_score",
        existing_type=sa.Float(),
        nullable=True,
    )
    op.add_column(
        "gap_analyses",
        sa.Column(
            "requirement_breakdown", JSONB(), nullable=False, server_default="[]"
        ),
    )


def downgrade() -> None:
    op.drop_column("gap_analyses", "requirement_breakdown")
    # Restore NOT NULL (will fail if any NULL rows exist - acceptable for downgrade)
    op.alter_column(
        "gap_analyses",
        "match_score",
        existing_type=sa.Float(),
        nullable=False,
    )
