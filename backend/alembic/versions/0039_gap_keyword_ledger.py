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

"""Add gap_analyses.keyword_ledger (ADR-048, E037/US198).

The Keyword Ledger is the single source of truth for every JD expectation
(concept + surface_forms + sources + fit_weight + status + evidence + claimable),
read by fit scoring, both document generators, both reviewers, the ATS panel,
and honest-gap interview routing. Nullable: rows created before E037 stay NULL
and consumers fall back to pre-ledger behaviour.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0039"
down_revision: str = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "gap_analyses",
        sa.Column(
            "keyword_ledger",
            JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("gap_analyses", "keyword_ledger")
