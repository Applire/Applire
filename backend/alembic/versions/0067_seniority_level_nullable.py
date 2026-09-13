# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""job_analyses.seniority_level nullable — NULL means the posting grounds no
tier (#617 axis (b) / Interview collector #675 line 39, WP-J Nougat build 3).

The extractor prompt (``prompts/job_analysis.py``, v6+) already tells the model
to emit ``null`` when none of the three grounds (title rank word, job-board
metadata line, stated experience/leadership bar) is present — "null is the
correct, expected answer ... it is not a missing value". ``services/job.py``
then laundered that honest ``null`` into an empty string via
``data.get("seniority_level") or ""`` to satisfy this column's NOT NULL
constraint, so "the posting stated no tier" and "we lost the tier" became the
same stored value, and ``gap_inference._seniority_threshold_met("")`` silently
withheld the "N years total experience meets seniority bar" category-B signal
from candidates applying to postings that state no tier at all.

Existing empty-string rows are the identical laundering artefact on every
pre-migration row (the corpus census in ``Documents/Runs/Nougat/build-2/a/
evidence-captured-records.md`` found the LLM itself never emits a literal
``""`` for this field) — they migrate to NULL. A non-empty value is left
untouched. Downgrade reverses both the column constraint and the data, in the
NOT NULL direction ``ui_language`` (migration 0055) took for its own nullable
flip, so up/down round-trips without error on sqlite or Postgres.

Chain: 0065 (signature) -> 0066 (signature_document_override) -> 0067.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: Union[str, None] = "0066"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("job_analyses") as batch_op:
        batch_op.alter_column(
            "seniority_level",
            existing_type=sa.Text(),
            nullable=True,
        )
    op.execute(
        "UPDATE job_analyses SET seniority_level = NULL WHERE seniority_level = ''"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE job_analyses SET seniority_level = '' WHERE seniority_level IS NULL"
    )
    with op.batch_alter_table("job_analyses") as batch_op:
        batch_op.alter_column(
            "seniority_level",
            existing_type=sa.Text(),
            nullable=False,
        )
