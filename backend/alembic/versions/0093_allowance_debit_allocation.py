# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-096 phase 2 (amended 2026-11-23, MD-9): a debit names the grant it spent.

``allowance_ledger.grant_id`` — nullable FK to the grant row (``ON DELETE SET
NULL``), set on debits and refunds. The balance becomes the sum, over unexpired
grants, of what is left of each; a non-rolling month then works, because last
month's debits stay with last month's grant. No data migration: phase 1 wrote
no debit on any path.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0093"
down_revision: Union[str, None] = "0092"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("allowance_ledger") as batch:
        batch.add_column(sa.Column("grant_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_allowance_ledger_grant_id",
            "allowance_ledger",
            ["grant_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_allowance_ledger_grant_id", "allowance_ledger", ["grant_id"])


def downgrade() -> None:
    op.drop_index("ix_allowance_ledger_grant_id", table_name="allowance_ledger")
    with op.batch_alter_table("allowance_ledger") as batch:
        batch.drop_constraint("fk_allowance_ledger_grant_id", type_="foreignkey")
        batch.drop_column("grant_id")
