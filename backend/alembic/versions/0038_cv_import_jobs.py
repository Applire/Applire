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

"""Add cv_import_jobs (async CV import — E036 follow-up).

A CV upload runs heavy segmented LLM work (extraction + reconcile + enrichment) that, on
a slow/output-capped model, exceeds the request/proxy timeout → 504 → CV dropped. This
table lets the upload return immediately and the work run in a background task, polled via
GET /api/profile/import-jobs/{id} (mirrors the async CV-generation lifecycle).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0038"
down_revision: str = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_import_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(length=512), nullable=False, server_default="upload"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "result",
            JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cv_import_jobs_user_id", "cv_import_jobs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_cv_import_jobs_user_id", table_name="cv_import_jobs")
    op.drop_table("cv_import_jobs")
