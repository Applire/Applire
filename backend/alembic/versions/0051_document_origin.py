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

"""add document origin columns (E044 / US250)

Revision ID: 0051
Revises: 0050
Create Date: 2026-07-18

ADR-054 (BYOI): documents rendered from agent-authored content via
render_document must never be presented as Applire-authored. `origin`
distinguishes 'pipeline' (the built-in LLM writer) from 'agent'
(render_document). server_default backfills every existing row as
'pipeline' — all pre-E044 rows came from the writer by definition.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generated_cvs",
        sa.Column("origin", sa.String(20), nullable=False, server_default="pipeline"),
    )
    op.add_column(
        "generated_cover_letters",
        sa.Column("origin", sa.String(20), nullable=False, server_default="pipeline"),
    )


def downgrade() -> None:
    op.drop_column("generated_cover_letters", "origin")
    op.drop_column("generated_cvs", "origin")
