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

"""add interviewing to UserStatus (E039/US218)

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-08

The Application.user_status column is stored as VARCHAR(20), not a Postgres
ENUM type, so this migration is a no-op at the database level (same pattern
as 0028, which added 'hired'). We keep the no-op migration to keep the
revision chain coherent and to document the schema-level intent.
"""
from typing import Sequence, Union

from alembic import op  # noqa: F401

revision: str = "0044"
down_revision: Union[str, None] = "0043"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: user_status is VARCHAR(20); valid values enforced at app level.
    pass


def downgrade() -> None:
    pass
