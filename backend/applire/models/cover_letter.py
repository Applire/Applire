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

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from applire.constants import GENERATED_DOCUMENTS_TTL_DAYS
from applire.db.session import Base

_JSON = JSONB().with_variant(JSON(), "sqlite")


class CoverLetterStatus(str, Enum):
    pending = "pending"
    generating = "generating"
    ready = "ready"
    failed = "failed"
    expired = "expired"


def _cl_expires_at() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=GENERATED_DOCUMENTS_TTL_DAYS)


class GeneratedCoverLetter(Base):
    __tablename__ = "generated_cover_letters"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_analyses.id"), nullable=False, index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("master_profiles.id"), nullable=False
    )
    template: Mapped[str] = mapped_column(String(40), nullable=False, default="classic_german")
    letter_data: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    pre_gen_inputs: Mapped[dict] = mapped_column(_JSON, nullable=False, default=dict)
    section_overrides: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    # ADR-039: persisted ATS audit report; NULL = not audited (engine error or pre-Chocolate row)
    ats_report: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    # ADR-052 (E043/US246): persisted truthfulness self-audit; NULL = not audited
    # (oracle error or pre-Tiramisu row). Written in the same commit as the
    # artifact/report so re-generation replaces it atomically. Never gates delivery.
    truthfulness_report: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    color_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cv_color_profiles.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CoverLetterStatus.pending.value
    )
    # E044/US250 (ADR-054): 'pipeline' = built-in LLM writer, 'agent' = content
    # authored by the caller via render_document — never presented as Applire-authored.
    origin: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pipeline", server_default="pipeline"
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_cl_expires_at,
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
