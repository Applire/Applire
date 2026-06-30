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

"""Add gap_analyses.input_fingerprint (E037 PQ #3 — match-score stability).

A sha256 fingerprint of the analysis inputs (JD fields + master-profile
content). analyze_gaps reuses the latest row for an unchanged (job, profile)
instead of re-running the LLM and inserting a duplicate row with a freshly
re-classified — and therefore wobbling — score. Nullable: rows created before
this fix stay NULL, never match a fresh fingerprint, and recompute once.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0040"
down_revision: str = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gap_analyses",
        sa.Column("input_fingerprint", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_gap_analyses_input_fingerprint",
        "gap_analyses",
        ["input_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_gap_analyses_input_fingerprint", table_name="gap_analyses")
    op.drop_column("gap_analyses", "input_fingerprint")
