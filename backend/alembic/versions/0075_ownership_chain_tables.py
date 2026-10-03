# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ownership, part 2 — settings, the four chain tables, usage attribution, and the
end-of-migration assertion (ADR-092 cl. 1, 3, 4; D-10, S-8; Strawberry package 3a).

1. ``user_settings`` — **dedupe, then ``UNIQUE(user_id)``** (D-10). The table has
   no ``updated_at``; per user the newest row (``created_at DESC, id DESC``) stays,
   the others are deleted and their ids logged. (Two rows for one user made every
   ``scalar_one_or_none()`` settings read raise, so no install with duplicates had
   a working settings page — the choice of survivor changes nothing it showed.)
2. ``generated_cvs``, ``generated_cover_letters``, ``gap_analyses``,
   ``interview_sessions`` gain ``user_id``, **backfilled from their profile's owner
   before NOT NULL** (0074 made ``master_profiles.user_id`` NOT NULL; ``profile_id``
   is NOT NULL with an FK on all four, so every row has an owner to copy). FK
   ``users.id`` + index.
3. Job-keyed uniques re-keyed with the owner (SF-OWN.7):
   ``uq_gap_analyses_live_fingerprint`` → ``(user_id, job_analysis_id,
   input_fingerprint)``; ``uq_interview_sessions_active_per_job`` →
   ``(user_id, job_analysis_id)``. Both new keys are supersets of the old ones, so
   no existing row can collide. The **legacy** ``uq_active_session_per_job``
   (migration 0011: ``UNIQUE(job_analysis_id) WHERE status = 'active'``, never in
   the models, superseded by 0048's index but never dropped — found by the
   PostgreSQL proof of this migration, 2026-10-03) is dropped: left in place it
   would keep the second user's interview on a shared posting failing.
4. ``llm_usage.user_id`` — nullable, FK ``ON DELETE SET NULL`` (S-8). History is
   **not** backfilled: past rows include ops probes, which belong to no person;
   NULL means "not attributed".
5. **Assertion (cl. 3/4):** ``COUNT(*) WHERE user_id IS NULL`` over every owned
   table with a ``user_id`` column must be 0, else the upgrade fails.
   (``profile_snapshots`` is chain-owned by CASCADE, MD-4 — no column.)

Portable SQL; partial unique indexes are dropped before and re-created after the
batch steps (a SQLite table copy must not lose their ``WHERE``).

**Downgrade** drops the columns and constraints and restores the job-keyed
uniques. If two users meanwhile hold live rows on one posting, re-creating the
old global unique fails — downgrade is a development tool; production rolls back
by restoring the pre-upgrade backup (ADR-091 Consequences). Deleted duplicate
settings rows are not restored.
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0075"
down_revision: Union[str, None] = "0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

CHAIN_TABLES = (
    "generated_cvs",
    "generated_cover_letters",
    "gap_analyses",
    "interview_sessions",
)

#: Owned tables with a direct ``user_id`` after this revision (ADR-092 cl. 3) —
#: frozen here: a migration asserts the schema of its own point in history.
OWNED_WITH_USER_ID = (
    "master_profiles",
    "user_settings",
    "applications",
    "flow_sessions",
    "uploads",
    "cv_import_jobs",
    "gap_analysis_jobs",
) + CHAIN_TABLES

_FP_WHERE = "deleted_at IS NULL AND input_fingerprint IS NOT NULL"
_ACTIVE_WHERE = "status = 'active' AND deleted_at IS NULL"


def _dedupe_user_settings(bind) -> list[str]:  # noqa: ANN001
    us = sa.table(
        "user_settings",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(
        sa.select(us.c.id, us.c.user_id).order_by(
            us.c.user_id, us.c.created_at.desc(), us.c.id.desc()
        )
    ).all()
    kept: set = set()
    losers = []
    for sid, uid in rows:
        if uid in kept:
            losers.append(sid)
        else:
            kept.add(uid)
    if losers:
        bind.execute(us.delete().where(us.c.id.in_(losers)))
        log.warning(
            "0075: %d duplicate user_settings row(s) deleted (kept the newest per user): %s",
            len(losers),
            ", ".join(str(x) for x in losers),
        )
    return [str(x) for x in losers]


def _assert_no_ownerless_rows(bind) -> None:  # noqa: ANN001
    offenders = {}
    for table in OWNED_WITH_USER_ID:
        t = sa.table(table, sa.column("user_id", sa.Uuid()))
        n = bind.execute(
            sa.select(sa.func.count()).select_from(t).where(t.c.user_id.is_(None))
        ).scalar_one()
        if n:
            offenders[table] = n
    if offenders:
        raise RuntimeError(
            f"ADR-092 cl. 4: ownerless rows survived the ownership migration: {offenders}"
        )


def upgrade() -> None:
    bind = op.get_bind()

    # 1. One settings row per user.
    _dedupe_user_settings(bind)
    with op.batch_alter_table("user_settings") as batch:
        batch.create_unique_constraint("uq_user_settings_user", ["user_id"])

    # 2. The chain tables copy their profile's owner.
    op.drop_index("uq_gap_analyses_live_fingerprint", table_name="gap_analyses")
    op.drop_index("uq_interview_sessions_active_per_job", table_name="interview_sessions")
    op.execute("DROP INDEX IF EXISTS uq_active_session_per_job")
    mp = sa.table("master_profiles", sa.column("id", sa.Uuid()), sa.column("user_id", sa.Uuid()))
    for table in CHAIN_TABLES:
        op.add_column(table, sa.Column("user_id", sa.Uuid(), nullable=True))
        t = sa.table(table, sa.column("profile_id", sa.Uuid()), sa.column("user_id", sa.Uuid()))
        bind.execute(
            t.update()
            .where(t.c.user_id.is_(None))
            .values(
                user_id=sa.select(mp.c.user_id)
                .where(mp.c.id == t.c.profile_id)
                .scalar_subquery()
            )
        )
        with op.batch_alter_table(table) as batch:
            batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=False)
            batch.create_foreign_key(f"fk_{table}_user_id_users", "users", ["user_id"], ["id"])
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    # 3. Job-keyed uniques re-keyed with the owner.
    op.create_index(
        "uq_gap_analyses_live_fingerprint",
        "gap_analyses",
        ["user_id", "job_analysis_id", "input_fingerprint"],
        unique=True,
        postgresql_where=sa.text(_FP_WHERE),
        sqlite_where=sa.text(_FP_WHERE),
    )
    op.create_index(
        "uq_interview_sessions_active_per_job",
        "interview_sessions",
        ["user_id", "job_analysis_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_WHERE),
        sqlite_where=sa.text(_ACTIVE_WHERE),
    )

    # 4. Usage attribution (S-8).
    op.add_column("llm_usage", sa.Column("user_id", sa.Uuid(), nullable=True))
    with op.batch_alter_table("llm_usage") as batch:
        batch.create_foreign_key(
            "fk_llm_usage_user_id_users", "users", ["user_id"], ["id"], ondelete="SET NULL"
        )
    op.create_index("ix_llm_usage_user_id", "llm_usage", ["user_id"])

    # 5. No ownerless per-user row survives.
    _assert_no_ownerless_rows(bind)


def downgrade() -> None:
    op.drop_index("ix_llm_usage_user_id", table_name="llm_usage")
    with op.batch_alter_table("llm_usage") as batch:
        batch.drop_constraint("fk_llm_usage_user_id_users", type_="foreignkey")
        batch.drop_column("user_id")

    op.drop_index("uq_interview_sessions_active_per_job", table_name="interview_sessions")
    op.drop_index("uq_gap_analyses_live_fingerprint", table_name="gap_analyses")
    for table in reversed(CHAIN_TABLES):
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_user_id_users", type_="foreignkey")
            batch.drop_column("user_id")
    op.create_index(
        "uq_interview_sessions_active_per_job",
        "interview_sessions",
        ["job_analysis_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_WHERE),
        sqlite_where=sa.text(_ACTIVE_WHERE),
    )
    op.create_index(
        "uq_gap_analyses_live_fingerprint",
        "gap_analyses",
        ["job_analysis_id", "input_fingerprint"],
        unique=True,
        postgresql_where=sa.text(_FP_WHERE),
        sqlite_where=sa.text(_FP_WHERE),
    )

    with op.batch_alter_table("user_settings") as batch:
        batch.drop_constraint("uq_user_settings_user", type_="unique")
    if op.get_bind().dialect.name == "postgresql":  # 0011 created it on PostgreSQL only
        op.create_index(
            "uq_active_session_per_job",
            "interview_sessions",
            ["job_analysis_id"],
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        )
