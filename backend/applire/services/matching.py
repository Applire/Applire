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

"""
Job matching service — vector similarity scoring and ranked job list.

Functions:
  compute_similarity(job_id, profile_id, db, embedding_provider) -> float
      Cosine similarity between stored job and profile embeddings.

  rank_jobs(profile_id, db, top_n, berufsbild_code) -> list[JobMatchResult]
      All non-deleted jobs ranked by combined score:
        combined = w_emb × embedding_similarity + w_llm × llm_match_score
      Score weights come from app settings (configurable, not hardcoded).
"""

import math
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.config import settings
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.providers.embedding.base import EmbeddingProvider


@dataclass
class JobMatchResult:
    job_id: uuid.UUID
    role_title: str
    company_name: str | None
    berufsbild_code: str | None
    berufsbild_label: str | None
    llm_match_score: float | None
    embedding_similarity: float | None
    combined_score: float
    gap_analysis_id: uuid.UUID | None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def compute_similarity(
    job_id: uuid.UUID,
    profile_id: uuid.UUID,
    db: AsyncSession,
    embedding_provider: EmbeddingProvider,
    *,
    user_id: uuid.UUID | None = None,
) -> float:
    """Return cosine similarity between stored job and profile embeddings.

    Falls back to 0.0 if either embedding is absent (e.g., noop provider).
    This function re-uses stored embeddings; it does NOT re-embed on the fly.
    """
    job_result = await db.execute(
        select(JobAnalysis).where(
            JobAnalysis.id == job_id,
            JobAnalysis.deleted_at.is_(None),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise LookupError(f"Job analysis {job_id} not found")

    profile_result = await db.execute(
        select(MasterProfile).where(
            MasterProfile.id == profile_id,
            MasterProfile.deleted_at.is_(None),
        )
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise LookupError(f"Profile {profile_id} not found")

    if job.embedding is None or profile.embedding is None:
        return 0.0

    return _cosine_similarity(list(job.embedding), list(profile.embedding))


async def rank_jobs(
    profile_id: uuid.UUID,
    db: AsyncSession,
    top_n: int = 10,
    berufsbild_code: str | None = None,
    *,
    user_id: uuid.UUID | None = None,
) -> list[JobMatchResult]:
    """Rank all non-deleted jobs for the given profile by combined score.

    Combined score = w_emb × embedding_similarity + w_llm × llm_match_score
    Weights are read from settings.matching_score_embedding_weight / matching_score_llm_weight.

    Args:
        profile_id: The master profile to match against.
        db: Async database session.
        top_n: Maximum number of results to return.
        berufsbild_code: Optional KldB 2020 code filter (prefix match on 1-5 digits).

    Returns:
        Ranked list of JobMatchResult, highest combined_score first.
    """
    w_emb = settings.matching_score_embedding_weight
    w_llm = settings.matching_score_llm_weight

    # Resolve profile embedding
    profile_result = await db.execute(
        select(MasterProfile).where(
            MasterProfile.id == profile_id,
            MasterProfile.deleted_at.is_(None),
        )
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise LookupError(f"Profile {profile_id} not found")

    profile_embedding = list(profile.embedding) if profile.embedding is not None else None

    # Fetch all non-deleted jobs
    job_query = select(JobAnalysis).where(JobAnalysis.deleted_at.is_(None))
    if berufsbild_code:
        # Prefix match: e.g. "43" matches "4311", "4321", etc.
        job_query = job_query.where(
            JobAnalysis.berufsbild_code.like(f"{berufsbild_code}%")
        )
    jobs_result = await db.execute(job_query)
    jobs = list(jobs_result.scalars().all())

    if not jobs:
        return []

    # Fetch latest gap analysis for each job+profile pair
    gap_query = select(GapAnalysis).where(
        GapAnalysis.profile_id == profile_id,
        GapAnalysis.deleted_at.is_(None),
    )
    gaps_result = await db.execute(gap_query)
    all_gaps = list(gaps_result.scalars().all())

    # Build a map: job_id -> latest GapAnalysis (by created_at)
    latest_gap: dict[uuid.UUID, GapAnalysis] = {}
    for gap in all_gaps:
        existing = latest_gap.get(gap.job_analysis_id)
        if existing is None or gap.created_at > existing.created_at:
            latest_gap[gap.job_analysis_id] = gap

    results: list[JobMatchResult] = []
    for job in jobs:
        job_embedding = list(job.embedding) if job.embedding is not None else None

        # Compute embedding similarity
        if profile_embedding is not None and job_embedding is not None:
            emb_sim = _cosine_similarity(profile_embedding, job_embedding)
        else:
            emb_sim = None

        # Get LLM match score from latest gap analysis
        gap = latest_gap.get(job.id)
        llm_score = gap.match_score if gap is not None else None

        # Combined score: use 0.0 for missing components
        effective_emb = emb_sim if emb_sim is not None else 0.0
        effective_llm = llm_score if llm_score is not None else 0.0
        combined = w_emb * effective_emb + w_llm * effective_llm

        results.append(JobMatchResult(
            job_id=job.id,
            role_title=job.role_title,
            company_name=job.company_name,
            berufsbild_code=job.berufsbild_code,
            berufsbild_label=job.berufsbild_label,
            llm_match_score=llm_score,
            embedding_similarity=emb_sim,
            combined_score=combined,
            gap_analysis_id=gap.id if gap is not None else None,
        ))

    results.sort(key=lambda r: r.combined_score, reverse=True)
    return results[:top_n]
