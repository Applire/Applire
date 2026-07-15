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

"""add target_cv_pages / target_pages columns (E042 / US236)

Revision ID: 0049
Revises: 0048
Create Date: 2026-07-15

ADR-051 §1 (length-aware CV tailoring): the region norm registry
(``applire.norms.REGION_NORMS``) is the single source of truth for CV page
length, resolved per-generation via ``resolve_target_pages()``. Two nullable
columns persist the resolved/chosen value:

1. ``user_settings.target_cv_pages`` — per-user default; NULL = "use region
   standard" (REGION_NORMS[region].cv_standard_pages).
2. ``generated_cvs.target_pages`` — the value actually resolved and used at
   CV-row creation time; NULL on legacy/pre-E042 rows, resolved lazily at
   consumption time (Task 1.3).

Both are plain nullable Integer ADD COLUMNs — no backfill, no data migration.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: Union[str, None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("target_cv_pages", sa.Integer(), nullable=True),
    )
    op.add_column(
        "generated_cvs",
        sa.Column("target_pages", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generated_cvs", "target_pages")
    op.drop_column("user_settings", "target_cv_pages")
