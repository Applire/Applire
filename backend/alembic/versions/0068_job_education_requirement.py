# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""job_analyses.education_requirement — the posting's own wording of a formal
education bar (founder ruling B-2, 2026-09-20; Interview collector #675 line 78 /
founder-UAT F-6).

Until now a stated degree requirement had nowhere to go. The JD extractor put it
in ``required_skills`` — "Master's degree", "Computer science", "Data science",
"Engineering", four entries out of ONE posting sentence — where every entry is a
concept term matched LITERALLY against the candidate's documents (ADR-048's
keyword ledger). A degree is not a capability a CV bullet can evidence, so those
entries could never move off ``gap``: they inflated the ADR-035 match denominator,
the gap chips and the interview agenda (one measured run spent a paid LLM round
eliciting "Presentation skills" from the same class of entry).

Prompt v10 stops emitting them there. Without this column the bar would simply
be lost from every derived surface, so the founder ruled it gets a home of its
own: ONE nullable text field carrying the posting's own words, never a list of
skills, never a judgement about the candidate.

Shape follows ``seniority_level`` after migration 0067 and ``leadership_emphasis``
(ADR-069/#271): NULL means two things that are deliberately not distinguished at
the column — the posting states no education bar, or the row predates this
migration. Both are resolved the same way by every reader (hide the field), and
nothing scores or gates on it, so no backfill is attempted and no default is
invented. Downgrade drops the column; the information it holds is still in
``job_analyses.raw_text``, so the downgrade loses no posting content.

Chain: 0066 (signature_document_override) -> 0067 (seniority_level nullable) -> 0068.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0068"
down_revision: Union[str, None] = "0067"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "job_analyses",
        sa.Column("education_requirement", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("job_analyses") as batch_op:
        batch_op.drop_column("education_requirement")
