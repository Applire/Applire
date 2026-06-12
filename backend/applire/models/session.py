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

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

_JSON = JSONB().with_variant(JSON(), "sqlite")

from applire.db.session import Base


class InterviewSession(Base):
    __tablename__ = "interview_sessions"
    __table_args__ = (
        # At most one active session per job — closes the check-then-create
        # race in create_session (concurrent requests, React StrictMode).
        Index(
            "uq_interview_sessions_active_per_job",
            "job_analysis_id",
            unique=True,
            postgresql_where=text("status = 'active' AND deleted_at IS NULL"),
            sqlite_where=text("status = 'active' AND deleted_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_analyses.id"), nullable=True, index=True
    )
    gap_analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gap_analyses.id"), nullable=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("master_profiles.id"), nullable=False
    )
    # "targeted" (MODE A) | "guided" (MODE B)
    mode: Mapped[str] = mapped_column(String(20), nullable=False, default="targeted")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active"
    )  # "active" | "complete"
    state: Mapped[dict] = mapped_column(_JSON, nullable=False)
    questions_asked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hard_ceiling: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
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
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
