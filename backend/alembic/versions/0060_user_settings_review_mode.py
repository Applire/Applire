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

"""Add user_settings.review_mode (ADR-081 clause 5, US301).

A three-valued per-user document-review preference: 'auto' | 'overview' |
'guided'. Under 'auto' the CV/cover-letter result screen picks guided while
the document still has unwalked group-1 findings and overview otherwise
(clause 5's own per-document, per-browser resolution — no server state for
that part); 'overview'/'guided' are fixed user overrides that always win
over the per-document default. Default 'auto' — a self-hoster who never
visits Settings gets the same guided-then-overview behaviour clause 5
specifies, not a silently-blank preference.

Deliberately NOT exposed over MCP (ADR-081 clause 8, SF-DOOR.4 carve-out):
this column governs presentation only, and an ADR-054 BYOI agent consumes
the structured report, never the panel.

Additive column on the 0042 (hide_predownload_notice) migration pattern.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "0060"
down_revision: str = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column(
            "review_mode",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'auto'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "review_mode")
