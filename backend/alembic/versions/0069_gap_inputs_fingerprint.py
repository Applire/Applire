# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""gap_analyses.gap_inputs_fingerprint — what the gap view compares to say
"your profile changed since this check" (ADR-090 clause 8, gap half).

A gap analysis is a model judgement, and ADR-090 rules that it never re-runs
by itself because of a change made outside its own view. The gap view instead
tells the user the analysis is stale and offers "Re-check gaps". To say that
without noise, the comparison covers only the part of the profile the analysis
reads: the whole-profile ``input_fingerprint`` changes on a phone number, a
photo or an application counter, and it stays what it is (ADR-089's
idempotency key).

NULL means the row predates this migration. Readers then fall back to the
whole-profile ``input_fingerprint`` (noisier, never silent), and the next
unchanged-input ``analyze_gaps`` call fills the column. Downgrade drops the
column; nothing else reads it.

Chain: 0067 (seniority_level nullable) -> 0068 (education_requirement) -> 0069.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0069"
down_revision: Union[str, None] = "0068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "gap_analyses",
        sa.Column("gap_inputs_fingerprint", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("gap_analyses") as batch_op:
        batch_op.drop_column("gap_inputs_fingerprint")
