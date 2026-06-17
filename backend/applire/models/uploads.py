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

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base
from applire.constants import UPLOAD_TTL_DAYS as _UPLOAD_TTL_DAYS

# JSONB on PostgreSQL (binary, indexed); JSON fallback on SQLite for unit tests.
_StagedJSON = JSONB().with_variant(JSON(), "sqlite")


class UploadRecord(Base):
    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # user_id added in iter17 for GDPR erasure scoping; NULL for pre-iter17 rows
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)  # SHA-256
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc) + timedelta(days=_UPLOAD_TTL_DAYS),
        nullable=False,
    )
    # US167 (E033 / ADR-041 amended) — pre-merge gate parking.
    # NULL for a normal merged upload. When the gate holds the merge, holds the
    # gate verdict ("not_a_cv" | "name_divergence"); the resolve endpoint flips it
    # to "resolved_merged" | "resolved_discarded". The staged extraction is parked
    # here (the already-extracted JSON, not the source PDF) so resolving re-merges
    # without re-running the LLM (ADR-005 source-file independence).
    gate_status: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    staged_extraction: Mapped[dict | None] = mapped_column(_StagedJSON, nullable=True)
