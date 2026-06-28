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
Gap analysis service — two-pass: rule-based pre-classification + LLM refinement.

Entry points:
  analyze_gaps(job_id, db, provider)              — canonical, job-scoped
  analyze_gaps_for_session(session_id, db, provider) — session-scoped convenience wrapper

Both call the same internal _run_analysis() function.
"""

import math
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.constants import GAP_ANALYSIS_MAX_TOKENS, GAP_CLUSTERING_MAX_TOKENS
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.models.session import InterviewSession
from applire.prompts.gap_analysis import SYSTEM_PROMPT, build_user_prompt
from applire.prompts.gap_clustering import CLUSTERING_SYSTEM_PROMPT, build_clustering_prompt
from applire.providers.llm.base import LLMProvider
from applire.schemas.gap import GapAnalysisResponse
from applire.schemas.gap_cluster import GapClusterSchema
from applire.services.gap_inference import pre_classify
from applire.services.match_score import compute_match_score


# ---------------------------------------------------------------------------
# Public: job-scoped (canonical)
# ---------------------------------------------------------------------------


async def analyze_gaps(
    job_id: uuid.UUID,
    db: AsyncSession,
    provider: LLMProvider,
) -> GapAnalysisResponse:
    """
    Canonical gap analysis entry point.

    Resolves the latest MasterProfile and runs a two-pass analysis:
      1. Rule-based pre-classification (pure Python, no LLM)
      2. LLM refinement — confirms/rejects B candidates, classifies unresolved as B or C

    Stores the result in gap_analyses and returns a GapAnalysisResponse.
    """
    job = await _resolve_job(job_id, db)
    profile = await _resolve_profile(db)
    return await _run_analysis(job, profile, db, provider)


# ---------------------------------------------------------------------------
# Public: session-scoped (convenience wrapper)
# ---------------------------------------------------------------------------


async def analyze_gaps_for_session(
    session_id: uuid.UUID,
    db: AsyncSession,
    provider: LLMProvider,
) -> GapAnalysisResponse:
    """
    Session-scoped convenience wrapper.

    Extracts the job_id from the session and delegates to analyze_gaps().
    If a GapAnalysis already exists for this job+profile it creates a new one
    (re-analysis reflects any profile changes since the last run).
    """
    session_result = await db.execute(
        select(InterviewSession).where(
            InterviewSession.id == session_id,
            InterviewSession.deleted_at.is_(None),
        )
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise LookupError(f"Session {session_id} not found")

    return await analyze_gaps(session.job_analysis_id, db, provider)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


async def cluster_gaps(
    gap_analysis: GapAnalysis,
    job: JobAnalysis,
    provider: LLMProvider,
    db: AsyncSession,
) -> None:
    """Run clustering LLM call and persist result to gap_analysis.gap_clusters."""
    # #3 (ADR-038): cluster descriptions (jd_context) render on the conversational gaps
    # page, so they follow the candidate's UI language. Local import avoids the
    # session<->gap circular dependency.
    from applire.services.session import get_ui_language
    lang = await get_ui_language(db)
    raw_clusters: list = await provider.aparse_json(
        build_clustering_prompt(
            category_b=list(gap_analysis.category_b or []),
            category_c=list(gap_analysis.category_c or []),
            required_skills=list(job.required_skills or []),
            nice_to_have_skills=list(job.nice_to_have_skills or []),
            lang=lang,
        ),
        system=CLUSTERING_SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=GAP_CLUSTERING_MAX_TOKENS,
    )
    validated = []
    for item in (raw_clusters if isinstance(raw_clusters, list) else []):
        try:
            validated.append(GapClusterSchema.model_validate(item).model_dump())
        except Exception:
            pass
    gap_analysis.gap_clusters = validated
    await db.commit()


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


def _compute_embedding_similarity(
    job_embedding: list[float] | None,
    profile_embedding: list[float] | None,
) -> float | None:
    """Return cosine similarity or None if either embedding is absent."""
    if job_embedding is None or profile_embedding is None:
        return None
    return _cosine_similarity(job_embedding, profile_embedding)


async def _run_analysis(
    job: JobAnalysis,
    profile: MasterProfile,
    db: AsyncSession,
    provider: LLMProvider,
) -> GapAnalysisResponse:
    job_dict = {
        "role_title": job.role_title,
        "required_skills": job.required_skills,
        "nice_to_have_skills": job.nice_to_have_skills,
        "keywords": job.keywords,
        "seniority_level": job.seniority_level,
        "company_culture_signals": job.company_culture_signals,
        "language_requirement": job.language_requirement,
    }

    # Pass 1: rule-based pre-classification
    pre = pre_classify(job_dict, profile.profile_json)

    # Pass 2: LLM refinement
    data: dict = await provider.aparse_json(
        build_user_prompt(job_dict, profile.profile_json, pre),
        system=SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=GAP_ANALYSIS_MAX_TOKENS,
    )

    scored = compute_match_score(
        data.get("classifications", []),
        list(job.required_skills or []),
        list(job.nice_to_have_skills or []),
    )

    # Compute embedding similarity score (None when noop provider or embeddings absent)
    embedding_similarity_score = _compute_embedding_similarity(
        job.embedding,
        profile.embedding,
    )

    record = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        match_score=scored["match_score"],
        embedding_similarity_score=embedding_similarity_score,
        critical_gaps=scored["critical_gaps"],
        minor_gaps=scored["minor_gaps"],
        strengths=data.get("strengths", []),
        keyword_gaps=data.get("keyword_gaps", []),
        category_a=scored["category_a"],
        category_b=scored["category_b"],
        category_c=scored["category_c"],
        requirement_breakdown=scored["requirement_breakdown"],
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    # Phase 2: semantic clustering
    await cluster_gaps(record, job, provider, db)
    await db.refresh(record)

    # Keep the owning flow's gap_analysis_id FK pointed at the newest analysis so
    # the CV/flow read path reports the post-interview score, not the stale
    # pre-interview one. Single seam: every recompute path (/gaps/refresh,
    # interview completion, gap-click) routes through here. Lazy import avoids the
    # gap<->flow.orchestrator import cycle (orchestrator transitively imports gap
    # via the session/application services).
    from applire.services.flow.orchestrator import repoint_flow_gap_analysis

    await repoint_flow_gap_analysis(job.id, record.id, db)

    return GapAnalysisResponse.model_validate(record)


async def _resolve_job(job_id: uuid.UUID, db: AsyncSession) -> JobAnalysis:
    result = await db.execute(
        select(JobAnalysis).where(
            JobAnalysis.id == job_id,
            JobAnalysis.deleted_at.is_(None),
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise LookupError(f"Job analysis {job_id} not found")
    return job


async def _resolve_profile(db: AsyncSession) -> MasterProfile:
    result = await db.execute(
        select(MasterProfile)
        .where(MasterProfile.deleted_at.is_(None))
        .order_by(MasterProfile.created_at.desc())
        .limit(1)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        raise LookupError("No profile found — import a CV first")
    return profile
