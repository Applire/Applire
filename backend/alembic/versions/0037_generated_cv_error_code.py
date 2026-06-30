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

"""Add error_code to generated_cvs (ADR-047 §4 / PQ F6, honest failure UX).

A failed CV generation persists the raw exception text in error_message (internal,
for ops) AND a stable machine code in error_code (e.g. 'llm_truncated'). The API
surfaces only the code, which the frontend maps to a localized human message + retry
— the raw 'Raise max_tokens or reduce reasoning' guidance is never shown to the user.
Nullable: pre-Strawberry rows and successful generations have no code.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0037"
down_revision: str = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generated_cvs", sa.Column("error_code", sa.String(length=40), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("generated_cvs", "error_code")
