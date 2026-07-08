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

"""Add applications.submitted_cv_id / submitted_cover_letter_id (E039/US219).

Mark as submitted: pins the exact artifact that was sent to the employer.
Pinned artifacts are exempt from the GENERATED_DOCUMENTS_TTL_DAYS retention
purge while their application is not tombstoned (ADR-005 amendment 2026-07-06)
— the retention clock follows the application lifecycle, not a calendar TTL.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0045"
down_revision: str = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("submitted_cv_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("submitted_cover_letter_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_applications_submitted_cv",
        "applications",
        "generated_cvs",
        ["submitted_cv_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_applications_submitted_cover_letter",
        "applications",
        "generated_cover_letters",
        ["submitted_cover_letter_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_applications_submitted_cover_letter", "applications", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_applications_submitted_cv", "applications", type_="foreignkey"
    )
    op.drop_column("applications", "submitted_cover_letter_id")
    op.drop_column("applications", "submitted_cv_id")
