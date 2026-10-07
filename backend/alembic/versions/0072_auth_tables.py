# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Identity tables: auth_sessions, personal_tokens, auth_links, reauth_grants (ADR-091 cl. 11, 17, 22, 23; Strawberry W1 package 1a).

Every ``token_hash`` is the hex sha256 of a 256-bit random secret the person
holds; no raw secret is stored. ``personal_tokens.prefix`` (8 chars) is unique —
the lookup key before ``compare_digest``. ``reauth_grants`` is created here and
filled by package 1d (fresh-OIDC re-auth for destructive actions).

The four tables are identity tables, not owned tables (ADR-092 cl. 3): they are
read before a user context exists. All FKs to ``users.id`` cascade — users are
tombstoned, not deleted, so the cascade only matters for a hard delete.

Downgrade drops the four tables (all sessions, tokens and links are lost; the
instance stays on 0071's columns).
Chain: 0071 <- 0072.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: Union[str, None] = "0071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TS = sa.DateTime(timezone=True)


def _user_fk() -> sa.Column:
    return sa.Column(
        "user_id",
        sa.UUID(),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.UUID(), primary_key=True),
        _user_fk(),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("last_seen_at", _TS, nullable=False),
        sa.Column("revoked_at", _TS, nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_auth_sessions_token_hash"),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])

    op.create_table(
        "personal_tokens",
        sa.Column("id", sa.UUID(), primary_key=True),
        _user_fk(),
        sa.Column("scope", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("last_used_at", _TS, nullable=True),
        sa.Column("revoked_at", _TS, nullable=True),
        sa.UniqueConstraint("prefix", name="uq_personal_tokens_prefix"),
        sa.CheckConstraint(
            "scope IN ('agent', 'api', 'probe')", name="ck_personal_tokens_scope"
        ),
    )
    op.create_index("ix_personal_tokens_user_id", "personal_tokens", ["user_id"])

    op.create_table(
        "auth_links",
        sa.Column("id", sa.UUID(), primary_key=True),
        _user_fk(),
        sa.Column("purpose", sa.String(length=8), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("used_at", _TS, nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_auth_links_token_hash"),
        sa.CheckConstraint("purpose IN ('invite', 'reset')", name="ck_auth_links_purpose"),
    )
    op.create_index("ix_auth_links_user_id", "auth_links", ["user_id"])

    op.create_table(
        "reauth_grants",
        sa.Column("id", sa.UUID(), primary_key=True),
        _user_fk(),
        sa.Column(
            "session_id",
            sa.UUID(),
            sa.ForeignKey("auth_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.UUID(), nullable=False),
        sa.Column("started_at", _TS, nullable=False),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("verified_at", _TS, nullable=True),
        sa.Column("used_at", _TS, nullable=True),
        sa.CheckConstraint(
            "action IN ('account.delete', 'oidc.unlink')", name="ck_reauth_grants_action"
        ),
    )
    op.create_index("ix_reauth_grants_user_id", "reauth_grants", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_reauth_grants_user_id", table_name="reauth_grants")
    op.drop_table("reauth_grants")
    op.drop_index("ix_auth_links_user_id", table_name="auth_links")
    op.drop_table("auth_links")
    op.drop_index("ix_personal_tokens_user_id", table_name="personal_tokens")
    op.drop_table("personal_tokens")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
