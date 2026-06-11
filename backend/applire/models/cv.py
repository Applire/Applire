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

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

_JSON = JSONB().with_variant(JSON(), "sqlite")

from applire.db.session import Base
from applire.constants import GENERATED_DOCUMENTS_TTL_DAYS as _TTL_DAYS


class CVGenerationStatus(str, Enum):
    """Lifecycle of an async CV generation job."""
    pending = "pending"       # Record created, background task not yet started
    generating = "generating" # Background task picked it up
    ready = "ready"           # PDF rendered, html/pdf endpoints are live
    failed = "failed"         # Rendering error; see error_message
    expired = "expired"       # Past expires_at; Retention Worker will clean up


def _expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=_TTL_DAYS)


class GeneratedCV(Base):
    __tablename__ = "generated_cvs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_analyses.id"), nullable=False, index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("master_profiles.id"), nullable=False
    )
    tailored_data: Mapped[dict] = mapped_column(_JSON, nullable=False)
    template: Mapped[str] = mapped_column(default="classic_german", nullable=False)
    # Async generation lifecycle (default 'ready' for pre-iter17 rows)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CVGenerationStatus.ready.value
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_snapshot: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    section_overrides: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    # ADR-039: persisted ATS audit report; NULL = not audited (engine error or pre-Chocolate row)
    ats_report: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    color_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cv_color_profiles.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_expires_at,
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
