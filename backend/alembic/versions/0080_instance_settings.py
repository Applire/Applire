# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-093 — ``instance_settings``: admin overrides of registry settings.

One table, no data migration. An absent table reads as "no overrides"
(``services/instance_settings.refresh``), so a process on an older schema
keeps working on its environment values.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0080"
down_revision: Union[str, None] = "0079"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    json_type = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")
    op.create_table(
        "instance_settings",
        sa.Column("key", sa.String(length=64), primary_key=True),
        sa.Column("value", json_type, nullable=True),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_by_user_id",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id",
                name="fk_instance_settings_updated_by_users",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("instance_settings")
