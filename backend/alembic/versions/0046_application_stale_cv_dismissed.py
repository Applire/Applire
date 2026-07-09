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

"""Add applications.stale_cv_dismissed_at (E039/US221, journey Branch H).

Persists "not now" on the stale-CV re-tailor nudge so a dismissal survives
reloads. The indicator re-arms by itself when a Master-Profile enrichment
lands AFTER this timestamp — the profile grew again, so the nudge is news.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0046"
down_revision: str = "0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("stale_cv_dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applications", "stale_cv_dismissed_at")
