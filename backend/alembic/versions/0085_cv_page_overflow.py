# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#786 L-3 (founder ruling 2026-10-22) — the CV remembers its x+1 decision.

* ``generated_cvs.page_overflow`` — JSON, NULL = the CV fits its page target. Holds
  the deterministic cut proposal (page ladder) and the person's choice
  ("keep" | "cut"). Additive and nullable: existing rows need no backfill.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0085"
down_revision: Union[str, None] = "0084"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generated_cvs",
        sa.Column(
            "page_overflow",
            postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("generated_cvs") as batch:
        batch.drop_column("page_overflow")
