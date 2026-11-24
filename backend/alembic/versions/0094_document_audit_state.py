# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet MD-G9: the audit state of a generated document

Revision ID: 0094
Revises: 0093
Create Date: 2026-11-23

One additive nullable JSON column ``audit_state`` on ``generated_cvs`` and
``generated_cover_letters`` (services/audit_state.py): which content the persisted
reports describe (a content fingerprint), whether the last audit ran, is pending or
did not run (and why), the hold that stood before an audit that did not run, and the
terminal-review row when the ATS engine failed. The download doors hold on it
(MD-G9: incomplete, refused, failed or pending never releases).

NULL = not recorded (a row from before this migration, an agent render before its
first audit): no audit-state hold, exactly the behaviour before.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0094"
down_revision: Union[str, None] = "0093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.add_column("generated_cvs", sa.Column("audit_state", _JSON, nullable=True))
    op.add_column("generated_cover_letters", sa.Column("audit_state", _JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("generated_cover_letters", "audit_state")
    op.drop_column("generated_cvs", "audit_state")
