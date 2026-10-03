# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ownership, part 1 — owner columns on the vault and the per-user jobs (ADR-092
cl. 2–5, S-9, RD-9; Strawberry package 3a).

Every per-user row gets an owner and no ownerless row survives (ADR-092 cl. 4).
Before Strawberry an install had exactly one person — the stub user the lifespan
inserts (``00000000-…-0001``), which first-run setup later converts into the admin
in place (S-3, ADR-091 cl. 14). So every existing row is that person's, and the
backfill target is the stub:

0. **The stub user is inserted first if missing** (a DB that never booted the
   app since 0001 has rows but no ``users`` row). Only the columns that exist on
   every image since 0010 are written; the identity columns of 0071 carry
   defaults (``role`` defaults to ``user`` — the stub gets no role here, ADR-091
   cl. 14).
1. ``master_profiles.user_id`` — added, backfilled to the stub, then **live
   duplicates retired (RD-9)**: per owner the row the app shows today stays live —
   ``ORDER BY created_at DESC, id DESC``, exactly ``_get_latest``'s order — and the
   others get ``deleted_at = now()``. Their ids go to the migration log and to
   ``instance_state['upgrade.retired_profiles']`` (``{"profile_ids": [...],
   "retired_at": iso}``), which the post-claim upgrade notice names. Then NOT NULL,
   FK ``users.id`` and ``uq_master_profiles_user_live UNIQUE(user_id) WHERE
   deleted_at IS NULL``.
2. ``uploads``, ``cv_import_jobs``, ``gap_analysis_jobs`` — ``user_id`` NULL **or
   naming no existing user** (the two job tables had no FK) → the stub; NOT NULL;
   FK where missing. ``gap_analysis_jobs.job_analysis_id`` gains its FK: gap jobs
   whose posting no longer exists are deleted first (ephemeral, TTL 24 h; count
   logged). ``uq_gap_jobs_live_kickoff`` is re-keyed ``(user_id, job_analysis_id)``.
3. ``job_analyses.raw_text_origin`` (``scraped`` | ``supplied``, cl. 5d / MD-10):
   until now ``source_url`` was set exactly when the text was fetched from it
   (``routers/job.py``, ``mcp/server.py`` ``analyze_jd``), so ``source_url IS NOT
   NULL`` → ``scraped``, else ``supplied``.

Portable SQL only (PostgreSQL in production, SQLite in the unit migration tests);
column changes go through ``batch_alter_table`` (plain ALTER on PostgreSQL, a
table copy on SQLite). Partial unique indexes are dropped before and created
after the batch steps so a SQLite table copy can never lose their ``WHERE``.

**Downgrade** removes the columns, FKs and indexes and restores the old
job-keyed kickoff index. Not reversed: retired duplicate profiles stay
soft-deleted (their ids are in the log; restore from the pre-upgrade backup),
deleted orphan gap jobs stay deleted, re-owned rows keep their content.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0074"
down_revision: Union[str, None] = "0073"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

#: The pre-Strawberry single user (auth/no_auth.py, main.py) — frozen here on
#: purpose: a migration must not import application constants that can move.
STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
STUB_EMAIL = "local@applire.community"

KEY_RETIRED_PROFILES = "upgrade.retired_profiles"

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")
_LIVE_KICKOFF_WHERE = "status IN ('pending','processing') AND deleted_at IS NULL"

_users = sa.table(
    "users",
    sa.column("id", sa.Uuid()),
    sa.column("email", sa.String()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("photo_consent", sa.Boolean()),
)
_instance_state = sa.table(
    "instance_state",
    sa.column("key", sa.String()),
    sa.column("value", _JSON),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _uuid(value) -> uuid.UUID:  # noqa: ANN001 — str (SQLite hex) or UUID (PostgreSQL)
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _ensure_stub_user(bind) -> None:  # noqa: ANN001
    present = bind.execute(
        sa.select(_users.c.id).where(_users.c.id == STUB_USER_ID)
    ).first()
    if present is None:
        bind.execute(
            _users.insert().values(
                id=STUB_USER_ID,
                email=STUB_EMAIL,
                created_at=datetime.now(timezone.utc),
                photo_consent=False,
            )
        )
        log.info("0074: inserted the stub user %s (owner of every existing row)", STUB_USER_ID)


def _reown_to_stub(bind, table: str) -> int:  # noqa: ANN001
    """``user_id`` NULL or naming no user → the stub. Returns the row count."""
    t = sa.table(table, sa.column("user_id", sa.Uuid()))
    result = bind.execute(
        t.update()
        .where(
            sa.or_(
                t.c.user_id.is_(None),
                t.c.user_id.not_in(sa.select(_users.c.id).scalar_subquery()),
            )
        )
        .values(user_id=STUB_USER_ID)
    )
    if result.rowcount:
        log.info("0074: %s — %d ownerless row(s) given to the stub user", table, result.rowcount)
    return result.rowcount


def _retire_duplicate_profiles(bind) -> list[str]:  # noqa: ANN001
    """RD-9: per owner keep the row ``_get_latest`` shows; soft-delete the rest."""
    mp = sa.table(
        "master_profiles",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("deleted_at", sa.DateTime(timezone=True)),
    )
    rows = bind.execute(
        sa.select(mp.c.id, mp.c.user_id)
        .where(mp.c.deleted_at.is_(None))
        .order_by(mp.c.user_id, mp.c.created_at.desc(), mp.c.id.desc())
    ).all()
    kept: set = set()
    retired: list[uuid.UUID] = []
    for pid, uid in rows:
        if uid in kept:
            retired.append(_uuid(pid))
        else:
            kept.add(uid)
    if not retired:
        return []
    now = datetime.now(timezone.utc)
    bind.execute(mp.update().where(mp.c.id.in_(retired)).values(deleted_at=now))
    ids = [str(r) for r in retired]
    log.warning(
        "0074: %d older duplicate live master profile(s) retired (soft-deleted; "
        "recoverable from the pre-upgrade backup) — kept the newest per owner "
        "(created_at DESC, id DESC): %s",
        len(ids),
        ", ".join(ids),
    )
    bind.execute(_instance_state.delete().where(_instance_state.c.key == KEY_RETIRED_PROFILES))
    bind.execute(
        _instance_state.insert().values(
            key=KEY_RETIRED_PROFILES,
            value={"profile_ids": ids, "retired_at": now.isoformat()},
            updated_at=now,
        )
    )
    return ids


def upgrade() -> None:
    bind = op.get_bind()

    # 0. The owner every existing row gets.
    _ensure_stub_user(bind)

    # 1. master_profiles.user_id + RD-9 dedupe + live-unique.
    op.add_column("master_profiles", sa.Column("user_id", sa.Uuid(), nullable=True))
    _reown_to_stub(bind, "master_profiles")
    _retire_duplicate_profiles(bind)
    with op.batch_alter_table("master_profiles") as batch:
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            "fk_master_profiles_user_id_users", "users", ["user_id"], ["id"]
        )
    op.create_index("ix_master_profiles_user_id", "master_profiles", ["user_id"])
    op.create_index(
        "uq_master_profiles_user_live",
        "master_profiles",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
        sqlite_where=sa.text("deleted_at IS NULL"),
    )

    # 2. The per-user tables that already had a (nullable) user_id.
    _reown_to_stub(bind, "uploads")
    with op.batch_alter_table("uploads") as batch:
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=False)
    # The model has always declared it; no migration created it (drift found by
    # comparing the migrated PostgreSQL schema with the models, 2026-10-03).
    op.create_index("ix_uploads_user_id", "uploads", ["user_id"], if_not_exists=True)

    _reown_to_stub(bind, "cv_import_jobs")
    with op.batch_alter_table("cv_import_jobs") as batch:
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            "fk_cv_import_jobs_user_id_users", "users", ["user_id"], ["id"]
        )

    op.drop_index("uq_gap_jobs_live_kickoff", table_name="gap_analysis_jobs")
    gj = sa.table("gap_analysis_jobs", sa.column("job_analysis_id", sa.Uuid()))
    ja = sa.table("job_analyses", sa.column("id", sa.Uuid()))
    orphans = bind.execute(
        gj.delete().where(gj.c.job_analysis_id.not_in(sa.select(ja.c.id).scalar_subquery()))
    ).rowcount
    if orphans:
        log.info("0074: gap_analysis_jobs — %d job(s) whose posting no longer exists deleted", orphans)
    _reown_to_stub(bind, "gap_analysis_jobs")
    with op.batch_alter_table("gap_analysis_jobs") as batch:
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=False)
        batch.create_foreign_key(
            "fk_gap_analysis_jobs_user_id_users", "users", ["user_id"], ["id"]
        )
        batch.create_foreign_key(
            "fk_gap_analysis_jobs_job_analysis_id_job_analyses",
            "job_analyses",
            ["job_analysis_id"],
            ["id"],
        )
    op.create_index(
        "uq_gap_jobs_live_kickoff",
        "gap_analysis_jobs",
        ["user_id", "job_analysis_id"],
        unique=True,
        postgresql_where=sa.text(_LIVE_KICKOFF_WHERE),
        sqlite_where=sa.text(_LIVE_KICKOFF_WHERE),
    )

    # 3. The shared posting cache learns where its text came from.
    op.add_column(
        "job_analyses",
        sa.Column(
            "raw_text_origin",
            sa.String(16),
            nullable=False,
            server_default="supplied",
        ),
    )
    jt = sa.table(
        "job_analyses",
        sa.column("source_url", sa.Text()),
        sa.column("raw_text_origin", sa.String()),
    )
    bind.execute(jt.update().where(jt.c.source_url.is_not(None)).values(raw_text_origin="scraped"))
    with op.batch_alter_table("job_analyses") as batch:
        batch.create_check_constraint(
            "ck_job_analyses_raw_text_origin",
            "raw_text_origin IN ('scraped','supplied')",
        )


def downgrade() -> None:
    with op.batch_alter_table("job_analyses") as batch:
        batch.drop_constraint("ck_job_analyses_raw_text_origin", type_="check")
        batch.drop_column("raw_text_origin")

    op.drop_index("uq_gap_jobs_live_kickoff", table_name="gap_analysis_jobs")
    with op.batch_alter_table("gap_analysis_jobs") as batch:
        batch.drop_constraint(
            "fk_gap_analysis_jobs_job_analysis_id_job_analyses", type_="foreignkey"
        )
        batch.drop_constraint("fk_gap_analysis_jobs_user_id_users", type_="foreignkey")
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=True)
    op.create_index(
        "uq_gap_jobs_live_kickoff",
        "gap_analysis_jobs",
        ["job_analysis_id"],
        unique=True,
        postgresql_where=sa.text(_LIVE_KICKOFF_WHERE),
        sqlite_where=sa.text(_LIVE_KICKOFF_WHERE),
    )

    with op.batch_alter_table("cv_import_jobs") as batch:
        batch.drop_constraint("fk_cv_import_jobs_user_id_users", type_="foreignkey")
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=True)

    op.drop_index("ix_uploads_user_id", table_name="uploads", if_exists=True)
    with op.batch_alter_table("uploads") as batch:
        batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=True)

    op.drop_index("uq_master_profiles_user_live", table_name="master_profiles")
    op.drop_index("ix_master_profiles_user_id", table_name="master_profiles")
    with op.batch_alter_table("master_profiles") as batch:
        batch.drop_constraint("fk_master_profiles_user_id_users", type_="foreignkey")
        batch.drop_column("user_id")
    op.execute(
        _instance_state.delete().where(_instance_state.c.key == KEY_RETIRED_PROFILES)
    )
