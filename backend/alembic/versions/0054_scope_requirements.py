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

"""add scope_requirements to job_analyses (ADR-069)

Revision ID: 0054
Revises: 0053
Create Date: 2026-08-01

ADR-069 clause 1: a quantified scope bar the posting states (team size,
budget) becomes data — list of {kind, value, value_max, comparator, quote,
level}, model-extracted, deterministically validated in services/job.py.
One nullable JSONB ADD COLUMN — NULL = pre-migration row or no bar stated;
read as [] everywhere. Existing rows are not back-filled.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite test databases.
_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "job_analyses",
        sa.Column("scope_requirements", _JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_analyses", "scope_requirements")
