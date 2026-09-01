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

"""add docx_ats_report columns (ADR-079 clause 8 / E057, #637)

Revision ID: 0059
Revises: 0058
Create Date: 2026-09-01

ADR-079 clause 8: the .docx export's own ATS audit report needs its own
storage. generated_cvs.ats_report / generated_cover_letters.ats_report are
single columns already bound to the PDF's audit, and the two can legitimately
diverge (the .docx is a second artefact, not a second renderer — ADR-079
clause 2). Two additive nullable JSONB columns, same shape/pattern as
0052/0053's critic_report pair:

- generated_cvs.docx_ats_report — wired in this migration's companion code
  change (services/cv.py._update_ats_report, US296).
- generated_cover_letters.docx_ats_report — schema only here. No writer
  populates it yet; that wiring is US297 (the letter writer), landing
  separately. The column is added now so the schema moves once, not twice.

NULL = not yet audited (engine error, or a pre-E057 row). The .docx BYTES
themselves are never persisted (ADR-079 clause 8) — only the report.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0059"
down_revision: Union[str, None] = "0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite test databases.
_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.add_column(
        "generated_cvs",
        sa.Column("docx_ats_report", _JSON, nullable=True),
    )
    op.add_column(
        "generated_cover_letters",
        sa.Column("docx_ats_report", _JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generated_cover_letters", "docx_ats_report")
    op.drop_column("generated_cvs", "docx_ats_report")
