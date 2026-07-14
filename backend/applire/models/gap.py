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

from sqlalchemy import DateTime, Float, ForeignKey, Index, JSON, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

_JSON = JSONB().with_variant(JSON(), "sqlite")

from applire.db.session import Base


class GapAnalysis(Base):
    __tablename__ = "gap_analyses"
    # One LIVE analysis per (job, input fingerprint) — the app-level idempotency
    # check in analyze_gaps is check-then-insert and lost a 7 ms race (two rows,
    # two scores, the "Analyzing your profile…" hang; Spaghettieis UAT 2026-07-13).
    # Legacy NULL fingerprints and soft-deleted rows stay outside the constraint.
    __table_args__ = (
        Index(
            "uq_gap_analyses_live_fingerprint",
            "job_analysis_id",
            "input_fingerprint",
            unique=True,
            sqlite_where=text("deleted_at IS NULL AND input_fingerprint IS NOT NULL"),
            postgresql_where=text("deleted_at IS NULL AND input_fingerprint IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_analysis_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_analyses.id"), nullable=False, index=True
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("master_profiles.id"), nullable=False, index=True
    )
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Cosine similarity of job_analysis.embedding vs master_profile.embedding (migration 0017).
    # NULL if either embedding is absent or noop provider is active.
    embedding_similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_gaps: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    minor_gaps: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    strengths: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    keyword_gaps: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    category_a: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    category_b: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    category_c: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    # ADR-048 Keyword Ledger — single source of truth for every JD expectation
    # (concept + surface_forms + sources + fit_weight + status + evidence + claimable).
    # Nullable: legacy rows created before E037 stay NULL; consumers fall back.
    keyword_ledger: Mapped[list | None] = mapped_column(_JSON, nullable=True, default=list)
    # E037 PQ #3 (match-score stability) — sha256 fingerprint of the analysis
    # inputs (JD fields + master-profile content). Lets analyze_gaps reuse the
    # latest row instead of re-running the LLM when inputs are unchanged, so the
    # deterministic score stays identical across screens. Nullable: legacy rows
    # created before this fix stay NULL and never match a fresh fingerprint, so
    # they recompute once and then become stable.
    input_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    gap_clusters: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    requirement_breakdown: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
