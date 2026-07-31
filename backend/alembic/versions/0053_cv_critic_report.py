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

"""add critic_report to generated_cvs (ADR-060 third amendment / E049 49.6)

Revision ID: 0053
Revises: 0052
Create Date: 2026-07-31

ADR-060 amended 2026-07-31: one critic engine, two mounts. Pass A judges the
ASSEMBLED CV for single-document coherence before it is presented and
persists its verdict here — the CV-side mirror of 0052's letter column. One
nullable JSONB ADD COLUMN — NULL = not yet computed (pre-two-mount row, or
the pass did not run; OutcomeCriticReport.reason says why). Never gates
delivery.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite test databases.
_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "generated_cvs",
        sa.Column("critic_report", _JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generated_cvs", "critic_report")
