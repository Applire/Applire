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

"""Pre-merge Master Profile snapshots for undo-last-merge (US168 / ADR-042).

Before every additive merge commits, the current ``profile_json`` is snapshotted
here so an accidental bad merge is recoverable. Cascades on profile delete, so
snapshots are purged with the profile under existing GDPR erasure (ADR-040) — no
new retention surface. Bounded per profile in application code.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0036"
down_revision: str = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_snapshots",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.UUID(),
            sa.ForeignKey("master_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enrichment_record_id", sa.Text(), nullable=False),
        sa.Column("profile_json", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_profile_snapshots_profile_id",
        "profile_snapshots",
        ["profile_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_profile_snapshots_profile_id", table_name="profile_snapshots"
    )
    op.drop_table("profile_snapshots")
