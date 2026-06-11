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

"""ADR-039: nullable ats_report JSONB on generated_cvs and generated_cover_letters."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0033"
down_revision: str = "0032"
branch_labels = None
depends_on = None

_JSON = JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.add_column("generated_cvs", sa.Column("ats_report", _JSON, nullable=True))
    op.add_column("generated_cover_letters", sa.Column("ats_report", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("generated_cover_letters", "ats_report")
    op.drop_column("generated_cvs", "ats_report")
