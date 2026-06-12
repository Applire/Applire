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

"""One active interview session per job.

create_session's check-then-create idempotency raced under concurrent
requests (React StrictMode double-fire), leaving duplicate active sessions
that broke every later lookup.  Dedupe existing rows (keep the newest active
session per job, mark older ones complete), then enforce uniqueness with a
partial index.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0034"
down_revision: str = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Supersede all but the newest active session per job.
    op.execute(
        sa.text(
            """
            UPDATE interview_sessions
            SET status = 'complete', updated_at = NOW()
            WHERE status = 'active'
              AND deleted_at IS NULL
              AND id NOT IN (
                  SELECT DISTINCT ON (job_analysis_id) id
                  FROM interview_sessions
                  WHERE status = 'active' AND deleted_at IS NULL
                  ORDER BY job_analysis_id, created_at DESC
              )
            """
        )
    )
    op.create_index(
        "uq_interview_sessions_active_per_job",
        "interview_sessions",
        ["job_analysis_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_interview_sessions_active_per_job", table_name="interview_sessions"
    )
