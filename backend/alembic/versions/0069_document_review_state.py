# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""generated_cvs.review_state + generated_cover_letters.review_state — the review
decisions on one generated document (ADR-090 clause 6, 2026-09-23).

Until now the only thing the review surface remembered was a browser-local
"walked" bit (ADR-081 clause 5a, ``localStorage``): nothing the user decided
survived a device change, and the agent door saw none of it. ADR-090 makes each
group-1 finding a decision (added to profile / taken out / edited) and persists
it per generated document, server-side, readable by every door (clause 7's rule:
state is a decision when losing it changes what the user is shown to do next).

Shape: ``{"walked_at": ts|null, "decisions": [{"finding_key", "label", "action",
"at", "undo": {"sections": [{"section_id", "before"}]}|null}]}``. Nullable JSON,
additive, no backfill: NULL means "no decision recorded", which is also the state
of every existing row. A decision never hides a finding — every count derives from
the live report (clause 6) — so an empty column cannot under-count anything.
Downgrade drops both columns; the decisions are labels over the reports, and the
documents and reports themselves are untouched.

Chain: 0067 (seniority_level nullable) -> 0068 (education_requirement) -> 0069.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0069"
down_revision: Union[str, None] = "0068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.add_column("generated_cvs", sa.Column("review_state", _JSON, nullable=True))
    op.add_column(
        "generated_cover_letters", sa.Column("review_state", _JSON, nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("generated_cover_letters") as batch_op:
        batch_op.drop_column("review_state")
    with op.batch_alter_table("generated_cvs") as batch_op:
        batch_op.drop_column("review_state")
