# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""generated_cvs.review_degradation + generated_cover_letters.review_degradation —
the review steps of the writing loop that did not run (ADR-081 cl. 9 amended
2026-10-22; #786, Hazelnut build 3, WP-T; id reserved by ruling B3-7).

``review_and_refine`` fails open by contract: a reviewer call that times out,
truncates or returns malformed JSON ships the draft un-reviewed, and a corrector
call that does the same keeps the last known-good draft. Until now that reached
the log only (UX audit 2026-10-20: three provider timeouts in one CV run, nothing
told the user). The document's background render now records each such step and
persists the record in its single ready-commit.

Shape: ``[{"check", "stage", "reason", "attempt", "count"}]``. Nullable JSON,
additive, no backfill: NULL means "not recorded" (every existing row, and an
agent-door render, where no writing loop ran); ``[]`` means "recorded, every
review step answered" — the two are never conflated (ADR-081 cl. 9). Downgrade
drops both columns; documents and reports are untouched.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0084"
down_revision: Union[str, None] = "0083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.add_column("generated_cvs", sa.Column("review_degradation", _JSON, nullable=True))
    op.add_column(
        "generated_cover_letters", sa.Column("review_degradation", _JSON, nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("generated_cover_letters") as batch_op:
        batch_op.drop_column("review_degradation")
    with op.batch_alter_table("generated_cvs") as batch_op:
        batch_op.drop_column("review_degradation")
