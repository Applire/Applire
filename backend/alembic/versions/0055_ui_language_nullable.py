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

"""ui_language nullable — NULL means never explicitly chosen (ADR-038 amended, #400)

The 'en' server default stood in for a choice no headless journey ever makes,
sending English interview questions into fully German agent-channel runs.
Existing 'en' rows migrate to NULL (an explicit-en chooser is indistinguishable
from a default-created row and re-materialises on next UI load); 'de' rows keep
their value — only an explicit write can have produced them.

Revision ID: 0055
Revises: 0054
Create Date: 2026-08-01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0055"
down_revision: Union[str, None] = "0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.alter_column(
            "ui_language",
            existing_type=sa.String(5),
            nullable=True,
            server_default=None,
        )
    op.execute("UPDATE user_settings SET ui_language = NULL WHERE ui_language = 'en'")


def downgrade() -> None:
    op.execute("UPDATE user_settings SET ui_language = 'en' WHERE ui_language IS NULL")
    with op.batch_alter_table("user_settings") as batch_op:
        batch_op.alter_column(
            "ui_language",
            existing_type=sa.String(5),
            nullable=False,
            server_default="en",
        )
