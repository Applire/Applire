# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-L: the person's consent to content logs (LOG_LEVEL=DEBUG only)

Revision ID: 0092
Revises: 0091
Create Date: 2026-11-18

ADR-091 amended 2026-11-18. Three additive nullable columns on ``users``:

- log_consent_at — when the person last agreed;
- log_consent_version — which text they agreed to (text version + values hash);
- log_consent_withdrawn_at — when they last withdrew.

No back-fill: everyone starts without a consent and is asked at DEBUG only.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0092"
down_revision: Union[str, None] = "0091"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("log_consent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("log_consent_version", sa.String(32), nullable=True))
    op.add_column(
        "users", sa.Column("log_consent_withdrawn_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "log_consent_withdrawn_at")
    op.drop_column("users", "log_consent_version")
    op.drop_column("users", "log_consent_at")
