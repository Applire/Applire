# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-096 — who pays for a call: per-user provider keys, the admin grant, the ledger.

One migration, as ADR-096's Consequences name it:

* ``user_provider_keys`` — one active key per person, Fernet ciphertext under a
  key derived from the environment-only ``PROVIDER_KEY_SECRET`` (cl. 2/3);
* ``allowance_ledger`` and ``allowance_daily`` (cl. 8);
* ``users.instance_key_granted_at`` (cl. 6) and ``users.allowance_subject`` (cl. 9);
* ``llm_usage.payer`` (cl. 11: the admin's usage view gains the payer).

No data migration. With the defaults (``LLM_KEY_MODE=instance``) nothing reads
the new tables on the request path, so a process on this schema behaves as before.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0090"
down_revision: Union[str, None] = "0089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_provider_keys",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id", name="fk_user_provider_keys_user_id_users", ondelete="CASCADE"
            ),
            primary_key=True,
        ),
        sa.Column("family", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("secret_ciphertext", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "family IN ('mistral', 'openrouter', 'requesty', 'anthropic', 'openai')",
            name="ck_user_provider_keys_family",
        ),
    )

    op.create_table(
        "allowance_ledger",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey(
                "users.id", name="fk_allowance_ledger_user_id_users", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        sa.Column("subject_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("payer", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('grant', 'debit', 'refund')", name="ck_allowance_ledger_kind"),
        sa.CheckConstraint(
            "action IN ('application', 'profile_import', 'job_analysis')",
            name="ck_allowance_ledger_action",
        ),
        sa.CheckConstraint(
            "payer IN ('user', 'instance', 'system')", name="ck_allowance_ledger_payer"
        ),
        sa.CheckConstraint("amount >= 0", name="ck_allowance_ledger_amount"),
    )
    op.create_index("ix_allowance_ledger_user_id", "allowance_ledger", ["user_id"])
    op.create_index(
        "uq_allowance_ledger_subject_kind_action_ref",
        "allowance_ledger",
        ["subject_hash", "kind", "action", "ref"],
        unique=True,
    )

    op.create_table(
        "allowance_daily",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("subject_hash", sa.String(length=64), nullable=True),
        sa.Column("tokens", sa.BigInteger(), nullable=False),
    )
    op.create_index(
        "uq_allowance_daily_day_subject",
        "allowance_daily",
        ["day", "subject_hash"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("instance_key_granted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("allowance_subject", sa.String(length=64), nullable=True))

    with op.batch_alter_table("llm_usage") as batch:
        batch.add_column(
            sa.Column("payer", sa.String(length=16), nullable=False, server_default="")
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_usage") as batch:
        batch.drop_column("payer")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("allowance_subject")
        batch.drop_column("instance_key_granted_at")
    op.drop_index("uq_allowance_daily_day_subject", table_name="allowance_daily")
    op.drop_table("allowance_daily")
    op.drop_index("uq_allowance_ledger_subject_kind_action_ref", table_name="allowance_ledger")
    op.drop_index("ix_allowance_ledger_user_id", table_name="allowance_ledger")
    op.drop_table("allowance_ledger")
    op.drop_table("user_provider_keys")
