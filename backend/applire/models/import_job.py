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

"""Async CV-import job (E036 follow-up — async import).

A CV upload runs heavy LLM work (segmented extraction + reconcile + enrichment) that,
on a slow/output-capped model, exceeds the request/proxy timeout — the upload then 504s
and the CV is dropped. This record lets the upload return immediately and the work run in
a background task, polled via GET /api/profile/import-jobs/{id} (mirrors the async CV
generation lifecycle in models/cv.py).
"""

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base

_JSON = JSONB().with_variant(JSON(), "sqlite")

# Import jobs are ephemeral — the result is consumed by the polling UI within minutes.
# Keep a short TTL so the table can't grow unbounded; the Retention Worker purges past it.
IMPORT_JOB_TTL_HOURS = 24


class CVImportStatus(str, Enum):
    """Lifecycle of an async CV-import job."""

    pending = "pending"        # Record created, background task not yet started
    processing = "processing"  # Background task picked it up (extraction/reconcile running)
    ready = "ready"            # Import finished; `result` holds the CVUploadResponse
    failed = "failed"          # Extraction/reconcile error; see error_code (raw text stays internal)
    expired = "expired"        # Past expires_at; Retention Worker will clean up


def _expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=IMPORT_JOB_TTL_HOURS)


class CVImportJob(Base):
    __tablename__ = "cv_import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Scopes the status lookup to its owner (IDOR guard, parity with staged extractions).
    # Nullable for single-user community / agent contexts that don't carry a user.
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False, default="upload")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CVImportStatus.pending.value
    )
    # Stable machine code for a failed import (e.g. 'llm_truncated', 'llm_timeout',
    # 'invalid_document'). The raw exception text stays in error_message (internal only).
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The serialised CVUploadResponse, populated when status == ready.
    result: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
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
