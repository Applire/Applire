# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-086 clause 5 — the retention worker's JSON report gets a consumer.

Since the worker was built it has printed one JSON report per run to stdout
(``retention/worker.py``, 18 counters) and **nothing has read it**. That is the
literal wording of ``SF-RET.1``'s and ``SF-RET.2``'s current-control cells —
"(D, unread)" — and of the founder's 2026-07-12 ruling that *an unread signal is
not a detection mechanism*.

One row per run, carrying the same dict verbatim. The stdout line is unchanged
and stays: every existing log-reading habit keeps working, and this table is
purely additive.

Read by ``services/ops/probes.py::probe_retention`` for two facts:
  * **last-run age** — WARNING at more than two run intervals (48 h at the
    shipped ``sleep 86400`` cadence);
  * **deletion-count anomaly** — any counter more than
    ``OPS_RETENTION_ANOMALY_FACTOR`` times the median of the previous runs.

No personal data: the report is counters only.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base

# ADR-002 pattern: JSONB on PostgreSQL, plain JSON on SQLite (unit tests).
_JSON = JSONB().with_variant(JSON(), "sqlite")


class RetentionRun(Base):
    __tablename__ = "retention_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Indexed: every read of this table is "the newest N rows".
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    # The worker's report dict verbatim — deliberately not normalised into
    # columns. A new TTL rule adds a counter to the dict and needs no migration,
    # and the anomaly probe iterates whatever keys are present.
    report: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # False when the run raised before finishing; `error` then carries the
    # exception's string form (never a stack trace, never a value).
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
