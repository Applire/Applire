# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""audit_events + the no-UPDATE trigger — ADR-091 cl. 26 (S-11, RD-11).

Append-only log of every admin-relevant action: who (``actor_user_id``), what
(``action``), when (``at``), whom (``target_user_id``) and a ``detail`` JSON of
ids/roles/reasons. **No IP column** (RD-11). A row can never be changed: a
``BEFORE UPDATE`` trigger raises on PostgreSQL (the ORM refuses it as well,
``models/audit.py``). DELETE stays possible for the retention age rule
``AUDIT_LOG_RETENTION_DAYS`` — the one stated exception.

The users FKs are ``ON DELETE SET NULL``: user rows are tombstoned, never hard
deleted (ADR-091 cl. 25), so this only matters for a manual DB cleanup.

Downgrade drops the trigger, its function and the table (the log is lost —
rollback is restore-only after 0071+ anyway, ADR-091 Consequences).

Chain: W0 stub revision kept (0072 <- 0073 <- 0074).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0073"
down_revision: Union[str, None] = "0072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

# Kept textually identical to applire.models.audit (not imported: a migration must
# not depend on the current model module).
_PG_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_events_reject_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only (ADR-091 cl. 26)';
END;
$$ LANGUAGE plpgsql
"""
_PG_TRIGGER = """
CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_events_reject_update()
"""
_SQLITE_TRIGGER = """
CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only (ADR-091 cl. 26)');
END
"""


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column(
            "target_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("detail", _JSON, nullable=False),
    )
    op.create_index("ix_audit_events_at", "audit_events", ["at"])
    op.create_index("ix_audit_events_target_user_id", "audit_events", ["target_user_id"])

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(_PG_FUNCTION)
        op.execute(_PG_TRIGGER)
    elif dialect == "sqlite":
        op.execute(_SQLITE_TRIGGER)


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect in ("postgresql", "sqlite"):
        op.execute("DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events"
                   if dialect == "postgresql"
                   else "DROP TRIGGER IF EXISTS audit_events_no_update")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS audit_events_reject_update()")
    op.drop_index("ix_audit_events_target_user_id", table_name="audit_events")
    op.drop_index("ix_audit_events_at", table_name="audit_events")
    op.drop_table("audit_events")
