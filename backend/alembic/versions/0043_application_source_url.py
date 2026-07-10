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

"""Add applications.source_url (E039/US216 — application dossier, Emma portfolio).

Where the posting was found: denormalized from JobAnalysis.source_url on create
(URL-tab auto-persist), user-editable via PATCH for pasted JDs. Backfills existing
applications from their JobAnalysis so returning users get links for past scrapes.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0043"
down_revision: str = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("source_url", sa.Text(), nullable=True),
    )
    # Backfill from the linked JobAnalysis (URL-tab scrapes already store it there).
    op.execute(
        """
        UPDATE applications a
        SET source_url = j.source_url
        FROM job_analyses j
        WHERE a.job_analysis_id = j.id
          AND a.source_url IS NULL
          AND j.source_url IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("applications", "source_url")
