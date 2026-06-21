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

"""Park a gated CV's staged extraction on the upload row (US167 / ADR-041 amended).

When the pre-merge integrity gate holds a merge (not-a-CV or name divergence),
the already-extracted profile JSON is parked on the originating upload so the
user can later resolve it (merge / discard) without re-running the LLM. The
parked item rides the existing 7-day upload TTL (ADR-005).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0035"
down_revision: str = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("uploads", sa.Column("gate_status", sa.Text(), nullable=True))
    op.add_column("uploads", sa.Column("staged_extraction", JSONB(), nullable=True))
    op.create_index(
        "ix_uploads_gate_status", "uploads", ["gate_status"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_uploads_gate_status", table_name="uploads")
    op.drop_column("uploads", "staged_extraction")
    op.drop_column("uploads", "gate_status")
