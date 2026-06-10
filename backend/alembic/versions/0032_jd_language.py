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

"""job_analyses.jd_language — JD document language (ADR-038)

Adds the language the JD is written in ('de'/'en'), detected
deterministically in code at analysis time. Document outputs (tailored CV,
cover letter) route on this column instead of language_requirement, which
describes the candidate requirement (e.g. "Bilingual DE/EN") and misroutes.

Existing rows stay NULL — resolve_jd_language() falls back to detecting
from raw_text at use time, so no backfill is required.

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_analyses",
        sa.Column("jd_language", sa.String(length=5), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_analyses", "jd_language")
