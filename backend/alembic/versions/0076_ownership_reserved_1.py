# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ownership, part 3 — give every owner a link to the postings they already worked on
(ADR-092 cl. 5c, RD-2; founder task W2-1; Strawberry package 4a).

Since Strawberry a posting (``job_analyses``) is reachable for a user only through
their own ``applications`` row (``services.job.get_job_for_user``). Before the
upgrade a person could generate a CV, a letter, a gap analysis, an interview or a
flow for a posting WITHOUT ever adding it to the pipeline — so after 0074/0075 that
work would sit behind a 404 on its own posting (gaps page, CV page job header,
regenerate).

For each owner, this step creates the link — an ``applications`` row with
``user_status='tracking'``, ``workflow_status='none'`` — for every LIVE posting the
owner has LIVE work on (a row in ``generated_cvs``, ``generated_cover_letters``,
``gap_analyses``, ``interview_sessions`` or ``flow_sessions`` with
``deleted_at IS NULL``), when the owner has no ``applications`` row for it yet
(soft-deleted rows count as existing: ``uq_application_user_job`` ignores
``deleted_at``, and a removed card is the user's own decision). Postings without
any owner work get no link. The new row takes the posting's ``role_title`` /
``company_name`` / ``source_url`` (pre-upgrade installs were single-user: the
posting's URL was the owner's own) and a fresh inactivity clock. Flows are NOT
attached (``flow_session_id`` stays NULL): "start" re-links the existing flow for
(user, job) (``services.application._start_workflow``), and attaching here would
need a workflow status this step cannot know.

Ids are generated in Python (portable: no ``gen_random_uuid`` on SQLite). The
created count is logged. Runs after 0075 (chain tables carry ``user_id``).

**Downgrade** is a no-op: a created link is indistinguishable from one analyze
made afterwards; rows are harmless to the older schema. Production rolls back by
restoring the pre-upgrade backup (ADR-091 Consequences).
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: Union[str, None] = "0075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")

#: (table, posting column) — every owner-work table that names a posting.
WORK_TABLES = (
    ("generated_cvs", "job_analysis_id"),
    ("generated_cover_letters", "job_analysis_id"),
    ("gap_analyses", "job_analysis_id"),
    ("interview_sessions", "job_analysis_id"),
    ("flow_sessions", "job_id"),
)

#: Same default as ``constants.PROFILE_INACTIVITY_TTL_DAYS`` (the application TTL);
#: a migration does not import the app (its settings may not load here).
_TTL_DAYS = 730


def _missing_links(conn) -> list[tuple]:
    """(user_id, job_id, role_title, company_name, source_url) needing a link."""
    union = " UNION ".join(
        f"SELECT w.user_id AS user_id, w.{col} AS job_id FROM {table} w "
        f"WHERE w.deleted_at IS NULL AND w.{col} IS NOT NULL"
        for table, col in WORK_TABLES
    )
    sql = sa.text(
        "SELECT DISTINCT o.user_id, j.id, j.role_title, j.company_name, j.source_url "
        f"FROM ({union}) o "
        "JOIN job_analyses j ON j.id = o.job_id AND j.deleted_at IS NULL "
        "WHERE NOT EXISTS (SELECT 1 FROM applications a "
        "                  WHERE a.user_id = o.user_id AND a.job_analysis_id = o.job_id) "
        "ORDER BY 1, 2"
    )
    return list(conn.execute(sql))


def upgrade() -> None:
    conn = op.get_bind()
    missing = _missing_links(conn)
    if not missing:
        log.info("0076: every owner already links every posting they worked on — nothing to create")
        return
    now = datetime.now(timezone.utc)
    apps = sa.table(
        "applications",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("job_analysis_id", sa.Uuid()),
        sa.column("workflow_status", sa.String()),
        sa.column("user_status", sa.String()),
        sa.column("role_title", sa.Text()),
        sa.column("company_name", sa.Text()),
        sa.column("source_url", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("expires_at", sa.DateTime(timezone=True)),
    )
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": uuid.UUID(str(user_id)),
            "job_analysis_id": uuid.UUID(str(job_id)),
            "workflow_status": "none",
            "user_status": "tracking",
            "role_title": role_title,
            "company_name": company_name,
            "source_url": source_url,
            "created_at": now,
            "updated_at": now,
            "expires_at": now + timedelta(days=_TTL_DAYS),
        }
        for user_id, job_id, role_title, company_name, source_url in missing
    ]
    op.bulk_insert(apps, rows)
    log.info(
        "0076: created %d application link(s) for postings owners already worked on: %s",
        len(rows),
        ", ".join(f"{r['user_id']}→{r['job_analysis_id']}" for r in rows),
    )


def downgrade() -> None:
    pass
