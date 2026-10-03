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
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from applire.db.session import Base


class FlowSession(Base):
    """End-to-end user journey from JD intake to CV download.

    Parent context for interview_sessions (1:0..1).
    One flow per (user_id, job_id) — enforced by uq_flow_session_user_job.
    user_type is resolved once at creation and is immutable.
    Child artifact FKs (gap_analysis_id, interview_session_id, generated_cv_id)
    are populated atomically by advance_flow() as the user progresses.
    """

    __tablename__ = "flow_sessions"

    __owned__ = True  # ADR-092 cl. 3 — the statement guard's owned set

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_analyses.id"), nullable=True, index=True
    )
    # Linear step: jd_analysis | cv_import | gap_analysis | interview | cv_generation | complete
    current_step: Mapped[str] = mapped_column(
        String(30), nullable=False, default="jd_analysis"
    )
    # "new" | "returning" — resolved at creation, immutable
    user_type: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    # {"next": "gap_analysis", "skip": "cv_generation"} — varies per (step, user_type)
    available_actions: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )

    # Child artifact references — None until that step is reached
    gap_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gap_analyses.id"), nullable=True
    )
    interview_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interview_sessions.id"), nullable=True
    )
    generated_cv_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_cvs.id"), nullable=True
    )
    generated_cover_letter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_cover_letters.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Back-link to the Application that owns this flow (set by ApplicationService)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applications.id"), nullable=True
    )
    # Soft-delete — set by DELETE /api/applications/{id} cascade
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_flow_session_user_job"),
    )
