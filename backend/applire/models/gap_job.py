# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Async gap-analysis job (E037 N2 — async gap analysis).

Gap analysis runs heavy real-LLM work (classification + clustering) that, on the first
analysis of a fresh job, blocks the gaps screen ~2 min and 504s fragilely (a timeout
mid-call wedges it until a manual reload). This record lets the kick-off return
immediately and the work run in a background task, polled via
GET /api/job/{job_id}/gap-jobs/{gap_job_id} (mirrors the async CV-import lifecycle in
models/import_job.py). The result is a pointer to the produced gap_analyses row, so the
migration-0040 input_fingerprint idempotency is preserved (the background task reuses a
fingerprint-matching row and skips the LLM).
"""

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base

# Gap jobs are ephemeral — the result is consumed by the polling UI within minutes.
# Keep a short TTL so the table can't grow unbounded; the Retention Worker purges past it.
GAP_JOB_TTL_HOURS = 24


class GapJobStatus(str, Enum):
    """Lifecycle of an async gap-analysis job."""

    pending = "pending"        # Record created, background task not yet started
    processing = "processing"  # Background task picked it up (LLM analysis running)
    ready = "ready"            # Analysis finished; result_gap_analysis_id points at the row
    failed = "failed"          # Analysis error; see error_code (raw text stays internal)
    expired = "expired"        # Past expires_at; Retention Worker will clean up


def _expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=GAP_JOB_TTL_HOURS)


class GapAnalysisJob(Base):
    __tablename__ = "gap_analysis_jobs"
    __owned__ = True  # ADR-092 cl. 3 — the statement guard's owned set
    # At most ONE live (pending/processing) job per (user, job_analysis_id) — re-keyed
    # with user_id by migration 0074 (ADR-092 cl. 3, SF-OWN.7). The
    # create_gap_job SELECT dedup is check-then-insert and lost a 7 ms race
    # (two kickoffs → two full LLM analyses; Spaghettieis UAT 2026-07-13) —
    # this partial unique index makes the DB the arbiter; the service catches
    # the IntegrityError and returns the winner.
    __table_args__ = (
        Index(
            "uq_gap_jobs_live_kickoff",
            "user_id",
            "job_analysis_id",
            unique=True,
            sqlite_where=text(
                "status IN ('pending','processing') AND deleted_at IS NULL"
            ),
            postgresql_where=text(
                "status IN ('pending','processing') AND deleted_at IS NULL"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # The JobAnalysis this gap job analyses; indexed for the concurrent-dedup lookup.
    # FK since migration 0074 (ADR-092 cl. 11: erasure's posting refcount must see it).
    job_analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(),
        ForeignKey("job_analyses.id", name="fk_gap_analysis_jobs_job_analysis_id_job_analyses"),
        nullable=False,
        index=True,
    )
    # The owner (ADR-092 cl. 3): NOT NULL + FK since migration 0074.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(),
        ForeignKey("users.id", name="fk_gap_analysis_jobs_user_id_users"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=GapJobStatus.pending.value
    )
    # Stable machine code for a failed analysis (e.g. 'llm_timeout', 'rate_limited').
    # The raw exception text stays in error_message (internal only).
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pointer to the produced gap_analyses row, populated when status == ready.
    result_gap_analysis_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_expires_at, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
