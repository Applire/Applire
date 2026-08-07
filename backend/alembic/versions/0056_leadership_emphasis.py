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

"""add leadership_emphasis to job_analyses (#271)

Revision ID: 0056
Revises: 0055
Create Date: 2026-08-07

#271: the posting's own leadership-vs-hands-on weighting becomes data —
{emphasis, quote}, model-extracted in the existing analyze_jd call and
deterministically validated in services/job.py. Same additive shape as
migration 0054 (ADR-069's scope_requirements): one nullable JSONB ADD COLUMN,
NULL = pre-migration row OR a posting that names no people-leadership
responsibility. Existing rows are not back-filled; the selection trigger
resolves NULL at use time via the legacy marker check
(services/vault_evidence.py), the jd_language / migration-0032 pattern.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0056"
down_revision: Union[str, None] = "0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite test databases.
_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "job_analyses",
        sa.Column("leadership_emphasis", _JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_analyses", "leadership_emphasis")
