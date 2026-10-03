# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""users identity columns + case-insensitive email uniqueness (ADR-091 cl. 5–9; Strawberry W1 package 1a).

Adds ``password_hash``, ``role`` (default ``user``, CHECK admin|user),
``oidc_issuer``/``oidc_subject`` (UNIQUE together — identity is the pair, MD-7),
``email_verified_at``, ``disabled_at``, ``last_login_at``, ``last_active_at`` and
``link_epoch`` (int, default 0), plus ``UNIQUE(lower(email))``.

**No role is set on the stub user** (ADR-091 cl. 14, D-11): the stub becomes the
admin through the setup claim (or ``python -m applire.admin create-admin``),
never through a migration, so an upgraded instance cannot be claimed by whoever
arrives first.

The expression index fails loudly if two emails differ only in case — impossible
on a single-user install (one stub row), stated in the ADR.

Downgrade drops everything added; the stub row and every other row are untouched.
Chain: 0070 <- 0071.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0071"
down_revision: Union[str, None] = "0070"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))
        batch.add_column(
            sa.Column("role", sa.String(length=16), nullable=False, server_default="user")
        )
        batch.add_column(sa.Column("oidc_issuer", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("oidc_subject", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("email_verified_at", _TS, nullable=True))
        batch.add_column(sa.Column("disabled_at", _TS, nullable=True))
        batch.add_column(sa.Column("last_login_at", _TS, nullable=True))
        batch.add_column(sa.Column("last_active_at", _TS, nullable=True))
        batch.add_column(
            sa.Column("link_epoch", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_unique_constraint(
            "uq_users_oidc_identity", ["oidc_issuer", "oidc_subject"]
        )
        batch.create_check_constraint("ck_users_role", "role IN ('admin', 'user')")
    op.create_index(
        "uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_users_email_lower", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_users_role", type_="check")
        batch.drop_constraint("uq_users_oidc_identity", type_="unique")
        for col in (
            "link_epoch",
            "last_active_at",
            "last_login_at",
            "disabled_at",
            "email_verified_at",
            "oidc_subject",
            "oidc_issuer",
            "role",
            "password_hash",
        ):
            batch.drop_column(col)
