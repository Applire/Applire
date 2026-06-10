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
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

_JSON = JSONB().with_variant(JSON(), "sqlite")
# VECTOR(1024) on PostgreSQL; TEXT (always NULL) on SQLite — see migration 0016.
_VECTOR_1024 = Vector(1024).with_variant(sa.Text(), "sqlite")

from applire.db.session import Base


class JobAnalysis(Base):
    __tablename__ = "job_analyses"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    raw_text_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    role_title: Mapped[str] = mapped_column(Text, nullable=False)
    required_skills: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    nice_to_have_skills: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    keywords: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    seniority_level: Mapped[str] = mapped_column(Text, nullable=False)
    company_culture_signals: Mapped[list] = mapped_column(_JSON, nullable=False, default=list)
    language_requirement: Mapped[str] = mapped_column(Text, nullable=False)
    # Language the JD document is *written in* ('de'/'en'), detected
    # deterministically at analysis time — the routing source for document
    # outputs per ADR-038. NULL for rows that pre-date migration 0032
    # (resolved at use time via utils.language_detection.resolve_jd_language).
    jd_language: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # LLM-extracted best-effort; nullable (recruiter-anonymised JDs omit company)
    company_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    # DACH occupation classification — KldB 2020 (BA-Klassifikation der Berufe 2020).
    # NULL when the LLM cannot confidently assign a code or for pre-migration rows.
    berufsbild_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    berufsbild_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True
    )
    # Embedding vector for job-profile similarity scoring (migration 0016).
    # NULL until first embedding pass; always NULL on SQLite (noop provider).
    embedding: Mapped[list[float] | None] = mapped_column(_VECTOR_1024, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
