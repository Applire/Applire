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

"""Create gap_analysis_jobs (E037 N2 — async gap analysis).

Ephemeral handle for the async gap-analysis job+poll lifecycle. The result is a pointer
to the produced gap_analyses row (result_gap_analysis_id); the short TTL (expires_at) is
purged by the Retention Worker. Mirrors 0038_cv_import_jobs.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0041"
down_revision: str = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gap_analysis_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_analysis_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_gap_analysis_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_gap_analysis_jobs_job_analysis_id", "gap_analysis_jobs", ["job_analysis_id"]
    )
    op.create_index("ix_gap_analysis_jobs_user_id", "gap_analysis_jobs", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_gap_analysis_jobs_user_id", table_name="gap_analysis_jobs")
    op.drop_index("ix_gap_analysis_jobs_job_analysis_id", table_name="gap_analysis_jobs")
    op.drop_table("gap_analysis_jobs")
