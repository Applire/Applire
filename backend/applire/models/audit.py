# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Append-only audit log (ADR-091 cl. 26, S-11; frozen interface F11).

``audit_events(id, at, actor_user_id NULL, action, target_user_id NULL, detail
JSON)`` — **no IP** (RD-11): who, what, when, target. Not ``__owned__`` (an
instance table referencing users; ``applire.ownership.IDENTITY_TABLES``).

Append-only, three layers deep:

1. **Database trigger** — rejects every ``UPDATE`` of a row. Migration ``0073``
   installs it on PostgreSQL; the ``after_create`` DDL below installs the same rule
   on any database built by ``Base.metadata.create_all`` (unit tests, SQLite), so a
   raw ``text("UPDATE audit_events …")`` is refused there too.
2. **ORM unit of work** — ``before_update`` on the mapper raises before a dirty
   ``AuditEvent`` is flushed.
3. **ORM bulk statements** — ``do_orm_execute`` refuses ``update(AuditEvent)``
   (bulk updates bypass mapper events).

``DELETE`` stays possible: the one stated exception is the retention age rule
``AUDIT_LOG_RETENTION_DAYS`` (ADR-091 cl. 26). Writers go through
``applire.services.audit.record`` — never construct rows elsewhere.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DDL, JSON, DateTime, ForeignKey, String, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from applire.db.session import Base

_JSON = JSONB().with_variant(JSON(), "sqlite")

AUDIT_TABLE = "audit_events"


class AuditLogIsAppendOnly(RuntimeError):
    """An attempt to change a written audit row (ADR-091 cl. 26)."""


class AuditEvent(Base):
    __tablename__ = AUDIT_TABLE

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    #: NULL = the system (CLI, setup, harness boot) or an unauthenticated request.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: ids, roles, reasons — never email, password, token or content
    #: (per-action key sets in ``services/audit.py``).
    detail: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)


# --- layer 1: the database refuses UPDATE --------------------------------------
# PostgreSQL text is shared with migration 0073 (single source).
PG_TRIGGER_FUNCTION = """
CREATE OR REPLACE FUNCTION audit_events_reject_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only (ADR-091 cl. 26)';
END;
$$ LANGUAGE plpgsql
"""
PG_TRIGGER = """
CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_events_reject_update()
"""
SQLITE_TRIGGER = """
CREATE TRIGGER audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit_events is append-only (ADR-091 cl. 26)');
END
"""

event.listen(
    AuditEvent.__table__,
    "after_create",
    DDL(PG_TRIGGER_FUNCTION).execute_if(dialect="postgresql"),
)
event.listen(
    AuditEvent.__table__,
    "after_create",
    DDL(PG_TRIGGER).execute_if(dialect="postgresql"),
)
event.listen(
    AuditEvent.__table__,
    "after_create",
    DDL(SQLITE_TRIGGER).execute_if(dialect="sqlite"),
)


# --- layer 2: the unit of work refuses a dirty row -----------------------------
@event.listens_for(AuditEvent, "before_update")
def _refuse_orm_update(mapper, connection, target) -> None:  # noqa: ARG001
    raise AuditLogIsAppendOnly("audit_events is append-only (ADR-091 cl. 26)")


# --- layer 3: bulk ORM update() is refused -------------------------------------
@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_update(orm_execute_state) -> None:
    if not orm_execute_state.is_update:
        return
    for mapper in orm_execute_state.all_mappers:
        if mapper.class_ is AuditEvent:
            raise AuditLogIsAppendOnly("audit_events is append-only (ADR-091 cl. 26)")
