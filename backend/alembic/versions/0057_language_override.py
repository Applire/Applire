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

"""E054: user document-language override + per-document language pinning

Revision ID: 0057
Revises: 0056
Create Date: 2026-08-23

ADR-038 amendment 2026-08-23. Three additive nullable String(5) columns:

- applications.language_override — the user's document-language choice
  ('de'/'en'; NULL = automatic). On the per-user applications row, NOT on the
  hash-deduplicated job_analyses row (cross-user write in a shared DB).
- generated_cvs.document_language / generated_cover_letters.document_language
  — the language the document was generated in, pinned at generation
  (clause 3b). NULL = pre-migration row, resolved at read time via
  resolve_document_language — the jd_language / migration-0032 pattern.

No back-fill; existing rows resolve NULL at use time.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0057"
down_revision: Union[str, None] = "0056"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("language_override", sa.String(length=5), nullable=True),
    )
    op.add_column(
        "generated_cvs",
        sa.Column("document_language", sa.String(length=5), nullable=True),
    )
    op.add_column(
        "generated_cover_letters",
        sa.Column("document_language", sa.String(length=5), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generated_cover_letters", "document_language")
    op.drop_column("generated_cvs", "document_language")
    op.drop_column("applications", "language_override")
