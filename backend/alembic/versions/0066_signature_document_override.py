# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Per-document signature override — one nullable boolean per document kind (F-4b).

0065 gave the signature two KIND-level defaults on ``user_settings``
(``signature_in_letter`` / ``signature_in_cv``). Founder ruling F-4b
(2026-09-11) adds a per-DOCUMENT override on top: a specific generated CV or
cover letter can say "with signature" / "without", overriding the kind
default for that one document, without touching anyone else's default.

Nullable, no server default — unlike 0065's two toggles, THIS column's three
states are all meaningful and distinct: ``NULL`` = "use the kind default",
``true`` = "with, regardless of the kind default", ``false`` = "without,
regardless of the kind default". Collapsing NULL into a boolean would destroy
the "never touched" state a reset (PATCH with ``signature_override: null``)
needs to return to. Same shape as ``target_pages``/``color_profile_id`` — a
nullable per-document override column, not a second pair of on/off defaults.

Read at BOTH render seams of each document kind (PDF/HTML and DOCX) ahead of
the kind default — see ``services/signature.py::_signature_path_if_enabled``'s
own docstring for the precedence. Additive only; no data migration, no
backfill (NULL is the correct value for every pre-existing row: "no override
was ever set" is exactly what a legacy document's silence means).

Chain: 0064 (llm_usage.reasoning_tokens) -> 0065 (signature) -> 0066.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0066"
down_revision: Union[str, None] = "0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "generated_cvs",
        sa.Column("signature_override", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "generated_cover_letters",
        sa.Column("signature_override", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("generated_cover_letters", "signature_override")
    op.drop_column("generated_cvs", "signature_override")
