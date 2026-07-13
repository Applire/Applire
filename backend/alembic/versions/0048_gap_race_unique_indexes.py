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

"""race-safe gap analysis: partial unique indexes + duplicate cleanup

Revision ID: 0048
Revises: 0047
Create Date: 2026-07-13

Two simultaneous gap-analysis kickoffs (7 ms apart; Spaghettieis UAT follow-up
flow) slipped past the check-then-insert dedups in create_gap_job and
analyze_gaps, producing duplicate LLM runs and two gap_analyses rows with the
SAME input fingerprint but different scores — and the gaps page hung reading
the mid-clustering duplicate. The DB becomes the arbiter:

1. uq_gap_jobs_live_kickoff — at most one live (pending/processing) job per
   job_analysis_id.
2. uq_gap_analyses_live_fingerprint — at most one live row per
   (job_analysis_id, input_fingerprint); legacy NULL fingerprints exempt.

Existing duplicates are cleaned up first (soft-delete all but the newest per
group; flow_sessions/interview_sessions FKs pointing at a soft-deleted
duplicate are repointed to the survivor), otherwise the unique index cannot be
created.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0048"
down_revision: Union[str, None] = "0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- 1. Soft-delete duplicate live gap_analyses (keep the newest per group) ---
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY job_analysis_id, input_fingerprint
                       ORDER BY created_at DESC, id DESC
                   ) AS rn,
                   FIRST_VALUE(id) OVER (
                       PARTITION BY job_analysis_id, input_fingerprint
                       ORDER BY created_at DESC, id DESC
                   ) AS survivor_id
            FROM gap_analyses
            WHERE deleted_at IS NULL AND input_fingerprint IS NOT NULL
        ),
        losers AS (
            SELECT id, survivor_id FROM ranked WHERE rn > 1
        ),
        repoint_flows AS (
            UPDATE flow_sessions f
            SET gap_analysis_id = l.survivor_id
            FROM losers l
            WHERE f.gap_analysis_id = l.id
            RETURNING f.id
        ),
        repoint_sessions AS (
            UPDATE interview_sessions s
            SET gap_analysis_id = l.survivor_id
            FROM losers l
            WHERE s.gap_analysis_id = l.id
            RETURNING s.id
        )
        UPDATE gap_analyses g
        SET deleted_at = NOW()
        FROM losers l
        WHERE g.id = l.id
        """
    )

    # --- 2. Resolve duplicate live gap jobs (keep the newest, expire the rest) ---
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY job_analysis_id
                       ORDER BY created_at DESC, id DESC
                   ) AS rn
            FROM gap_analysis_jobs
            WHERE status IN ('pending', 'processing') AND deleted_at IS NULL
        )
        UPDATE gap_analysis_jobs j
        SET status = 'expired'
        FROM ranked r
        WHERE j.id = r.id AND r.rn > 1
        """
    )

    # --- 3. The arbiters ---
    op.create_index(
        "uq_gap_jobs_live_kickoff",
        "gap_analysis_jobs",
        ["job_analysis_id"],
        unique=True,
        postgresql_where="status IN ('pending','processing') AND deleted_at IS NULL",
    )
    op.create_index(
        "uq_gap_analyses_live_fingerprint",
        "gap_analyses",
        ["job_analysis_id", "input_fingerprint"],
        unique=True,
        postgresql_where="deleted_at IS NULL AND input_fingerprint IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_index("uq_gap_analyses_live_fingerprint", table_name="gap_analyses")
    op.drop_index("uq_gap_jobs_live_kickoff", table_name="gap_analysis_jobs")
    # Soft-deletes / expirations are not reversed — they are data cleanup.
