# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-023 amended 2026-10-11 (US357, #770) — each person picks an app colour scheme.

* ``system_color_schemes.offered_to_users`` — the admin's offer list (CS-1). The
  built-in schemes and the current default start offered, so nobody's picker is
  empty after the upgrade; a scheme the admin created before stays unoffered
  until the admin says otherwise.
* ``user_settings.ui_color_scheme_id`` — the person's choice; NULL = follow the
  instance default. ``ON DELETE SET NULL`` is the CS-2 fallback for a deleted
  scheme (an un-offered one falls back at read time, in the resolver).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0082"
down_revision: Union[str, None] = "0081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "system_color_schemes",
        sa.Column(
            "offered_to_users",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        "UPDATE system_color_schemes SET offered_to_users = true "
        "WHERE is_builtin = true OR is_active = true"
    )
    with op.batch_alter_table("user_settings") as batch:
        batch.add_column(sa.Column("ui_color_scheme_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_user_settings_ui_color_scheme",
            "system_color_schemes",
            ["ui_color_scheme_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("user_settings") as batch:
        batch.drop_constraint("fk_user_settings_ui_color_scheme", type_="foreignkey")
        batch.drop_column("ui_color_scheme_id")
    op.drop_column("system_color_schemes", "offered_to_users")
