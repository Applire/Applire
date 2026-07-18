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

"""add truthfulness_report columns (E043 / US246)

Revision ID: 0050
Revises: 0049
Create Date: 2026-07-18

ADR-052 (Truthfulness Oracle): every generated CV and cover letter carries a
persisted pre-delivery truthfulness self-audit alongside its ATS report. Two
nullable JSONB ADD COLUMNs — NULL = not audited (oracle error or pre-Tiramisu
row); no backfill, no data migration. The report never gates delivery (v1).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0050"
down_revision: Union[str, None] = "0049"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite test databases.
_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "generated_cvs",
        sa.Column("truthfulness_report", _JSON, nullable=True),
    )
    op.add_column(
        "generated_cover_letters",
        sa.Column("truthfulness_report", _JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generated_cover_letters", "truthfulness_report")
    op.drop_column("generated_cvs", "truthfulness_report")
