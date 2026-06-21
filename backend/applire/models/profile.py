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

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base

# JSONB on PostgreSQL (binary, indexed); falls back to JSON on SQLite for unit tests.
_ProfileJSON = JSONB().with_variant(JSON(), "sqlite")
# VECTOR(1024) on PostgreSQL; TEXT (always NULL) on SQLite — see migration 0016.
_VECTOR_1024 = Vector(1024).with_variant(sa.Text(), "sqlite")


class MasterProfile(Base):
    __tablename__ = "master_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_json: Mapped[dict] = mapped_column(_ProfileJSON, nullable=False)
    # Embedding vector for job-profile similarity scoring (migration 0016).
    # Re-computed on every upsert; NULL until first pass; always NULL on SQLite.
    embedding: Mapped[list[float] | None] = mapped_column(_VECTOR_1024, nullable=True)
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
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProfileSnapshot(Base):
    """Pre-merge snapshot of a Master Profile (US168 / ADR-042).

    Captured unconditionally before every merge commit so an accidental bad merge
    is recoverable via ``undo_last_merge``. Keyed to the merge's ``EnrichmentRecord``
    so undo can detect later edits. Profile-derived PII: cascades on profile delete,
    so it is purged with the profile under existing GDPR erasure (ADR-040) — no new
    retention surface. Bounded per profile (``SNAPSHOT_MAX_PER_PROFILE``).
    """

    __tablename__ = "profile_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("master_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The merge enrichment record this snapshot precedes (ADR-042). UUID-as-text:
    # EnrichmentRecords live inside profile_json, not their own table — no FK.
    enrichment_record_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    profile_json: Mapped[dict] = mapped_column(_ProfileJSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
