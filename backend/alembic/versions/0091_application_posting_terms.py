# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q10: the person's answer to the posting-terms tick list

Revision ID: 0091
Revises: 0090
Create Date: 2026-11-17

ADR-046 amended 2026-11-17 (ruling Q10-4). One additive nullable JSON column
(the 0058 shape):

- applications.posting_terms — {"declined": [term, ...], "answered_at": iso,
  "via": "person"|"agent"}; the terms the person left unticked for THIS
  application, recorded so the card does not ask them again. A ticked term is
  a profile skill, not stored here. NULL = never answered.

No back-fill.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

revision: str = "0091"
down_revision: Union[str, None] = "0090"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = JSONB().with_variant(JSON(), "sqlite")


def upgrade() -> None:
    op.add_column("applications", sa.Column("posting_terms", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "posting_terms")
