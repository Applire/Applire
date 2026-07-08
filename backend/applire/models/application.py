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

"""Application model — Iteration 17

First-class domain entity for tracking a user's interest in a specific job.
Sits above FlowSession: an Application can exist in 'tracking' state with no
FlowSession attached. When the user commits to the workflow, start_workflow()
creates a FlowSession and links it here.

Status model (two columns, two enums):
  workflow_status — system-managed; synced from FlowSession.current_step
                    by the Flow Orchestrator on each advance_flow() call.
  user_status     — user-managed; set via PATCH /api/applications/{id}.
"""

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base
from applire.constants import PROFILE_INACTIVITY_TTL_DAYS as _APPLICATION_TTL_DAYS


def _default_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=_APPLICATION_TTL_DAYS)


class WorkflowStatus(str, Enum):
    """System-managed. Written exclusively by the Flow Orchestrator."""
    none = "none"               # No FlowSession attached yet
    analyzing = "analyzing"     # jd_analysis / cv_import / gap_analysis steps
    interviewing = "interviewing"
    cv_generating = "cv_generating"
    completed = "completed"


class UserStatus(str, Enum):
    """User-managed. Set via PATCH /api/applications/{id}.

    Pipeline order (E039/US218): tracking → applied → interviewing → offer/rejected → hired.
    """
    tracking = "tracking"   # Default on creation
    applied = "applied"
    interviewing = "interviewing"
    offer = "offer"
    rejected = "rejected"
    hired = "hired"


# Map FlowSession.current_step → WorkflowStatus
STEP_TO_WORKFLOW_STATUS: dict[str, WorkflowStatus] = {
    "jd_analysis":   WorkflowStatus.analyzing,
    "cv_import":     WorkflowStatus.analyzing,
    "gap_analysis":  WorkflowStatus.analyzing,
    "interview":     WorkflowStatus.interviewing,
    "cv_generation": WorkflowStatus.cv_generating,
    "complete":      WorkflowStatus.completed,
}


class Application(Base):
    """One record per (user_id, job_analysis_id) — enforced by unique constraint."""

    __tablename__ = "applications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    job_analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_analyses.id"), nullable=False, index=True
    )

    # --- Status (two layers) ---
    workflow_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=WorkflowStatus.none.value
    )
    user_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=UserStatus.tracking.value
    )

    # --- Denormalized from JobAnalysis for fast list queries ---
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_title: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- User-managed fields ---
    # Where the posting was found (E039/US216): denormalized from
    # JobAnalysis.source_url on create, user-overridable for pasted JDs.
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Submitted pins (E039/US219) ---
    # The exact artifacts sent to the employer. While the application is not
    # tombstoned, pinned documents are exempt from the retention TTL purge —
    # their retention clock follows the application lifecycle (ADR-005
    # amendment 2026-07-06). Erasure (Art. 17) still deletes them.
    submitted_cv_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_cvs.id"), nullable=True
    )
    submitted_cover_letter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("generated_cover_letters.id"), nullable=True
    )

    # --- 1:0..1 link to FlowSession ---
    flow_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("flow_sessions.id"), nullable=True
    )

    # --- Timestamps ---
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
    # GDPR inactivity TTL — resets on every update (ADR 005)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_default_expires_at,
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("user_id", "job_analysis_id", name="uq_application_user_job"),
    )
