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

"""E056: fact pins — the user's seat at the budget table

Revision ID: 0058
Revises: 0057
Create Date: 2026-08-25

ADR-077 clause 1. One additive nullable JSONB column (same shape as
migrations 0054/0056):

- applications.pinned_facts — list of
  {pin_id, entry_type, entry_id, quote, targets, stale}; each pin a verbatim
  vault quote with a vault address, verified fail-closed at write time,
  capped at MAX_FACT_PINS. NULL = pre-migration row, read as [].

No back-fill. The sibling id back-fill for the five previously id-less vault
types (Skill/Certification/EducationEntry/Language/Publication) is NOT a
migration: ids live inside master_profiles.profile_json and are written back
once through the ADR-063 committer (services/profile/commit.py), never via
raw SQL — the migrate.py precedent.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite.
_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("pinned_facts", _JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("applications", "pinned_facts")
