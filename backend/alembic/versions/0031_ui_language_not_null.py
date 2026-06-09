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

"""ui_language non-nullable, default 'en' (ADR-038)

Make UserSettings.ui_language NOT NULL with a server default of 'en'.
Any existing NULL rows are backfilled to 'en' before the constraint is applied.
This makes ui_language the authoritative language source for LLM-generated
interview questions (ADR-038).

Revision ID: 0031
Revises: 0030
Create Date: 2026-06-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE user_settings SET ui_language = 'en' WHERE ui_language IS NULL")
    op.alter_column(
        "user_settings",
        "ui_language",
        existing_type=sa.String(length=5),
        nullable=False,
        server_default="en",
    )


def downgrade() -> None:
    op.alter_column(
        "user_settings",
        "ui_language",
        existing_type=sa.String(length=5),
        nullable=True,
        server_default=None,
    )
