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

import hashlib
import json
import math
import uuid

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
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
from applire.services.keyword_ledger import build_keyword_ledger, keyword_only_honest_gaps
from applire.services.match_score import compute_match_score_from_ledger


def _norm_gap(s: str) -> str:
    return (s or "").strip().casefold()


def _job_inputs(job: JobAnalysis) -> dict:
    """The JD fields that feed the analysis — also the score-bearing inputs."""
    return {
        "role_title": job.role_title,
        "required_skills": job.required_skills,
        "nice_to_have_skills": job.nice_to_have_skills,
        "keywords": job.keywords,
        "seniority_level": job.seniority_level,
        "company_culture_signals": job.company_culture_signals,
        "language_requirement": job.language_requirement,
    }


def _input_fingerprint(job: JobAnalysis, profile: MasterProfile) -> str:
    """Stable sha256 of the analysis inputs (JD fields + master-profile content).

    Same inputs → same fingerprint → reuse the existing row instead of re-running
    the LLM (E037 PQ #3). Profile content (not just a version stamp) is hashed, so
    a genuine interview enrichment changes the fingerprint and forces a recompute;
    an idempotent re-POST from a screen load does not.
    """
    payload = json.dumps(
        {"job": _job_inputs(job), "profile": profile.profile_json},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def _latest_gap_analysis(job_id: uuid.UUID, db: AsyncSession) -> GapAnalysis | None:
    """The most recent non-deleted gap analysis for a job (the read-path row)."""
    result = await db.execute(
        select(GapAnalysis)
        .where(
            GapAnalysis.job_analysis_id == job_id,
            GapAnalysis.deleted_at.is_(None),
        )
        .order_by(desc(GapAnalysis.created_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Public: job-scoped (canonical)
# ---------------------------------------------------------------------------


async def analyze_gaps(
    job_id: uuid.UUID,
    db: AsyncSession,
    provider: LLMProvider,
    *,
    clamp_to_previous: bool = False,
) -> GapAnalysisResponse:
    """
    Canonical gap analysis entry point.

    Resolves the latest MasterProfile and runs a two-pass analysis:
      1. Rule-based pre-classification (pure Python, no LLM)
      2. LLM refinement — confirms/rejects B candidates, classifies unresolved as B or C

    Idempotent per (job, profile-fingerprint): when the inputs are unchanged it
    REUSES the latest stored gap_analyses row instead of re-running the LLM and
    inserting a duplicate (E037 PQ #3 — match-score stability). Only a genuine
    profile or JD change recomputes.

    ``clamp_to_previous`` (the /gaps/refresh, post-interview-answer path): when a
    recompute does happen, the headline ``match_score`` is clamped to
    ``max(old, new)`` — added evidence is monotonic-up since fit weights are fixed,
    so answering a gap can never lower the displayed score.

    Stores the result in gap_analyses and returns a GapAnalysisResponse.
    """
    job = await _resolve_job(job_id, db)
    profile = await _resolve_profile(db)
    return await _run_analysis(
        job, profile, db, provider, clamp_to_previous=clamp_to_previous
    )


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
    # US204 (ADR-048 §10): keyword-only honest gaps carry no fit weight, so they
    # never reach category_c — route them into the interview here, deduped against
    # the category_c gaps already present. The clustering LLM merges by domain and
    # writes an estimate-honest jd_context, so they surface as askable clusters.
    category_c = list(gap_analysis.category_c or [])
    seen_c = {_norm_gap(g) for g in category_c}
    for concept in keyword_only_honest_gaps(getattr(gap_analysis, "keyword_ledger", None)):
        if _norm_gap(concept) not in seen_c:
            category_c.append(concept)
            seen_c.add(_norm_gap(concept))
    raw_clusters: list = await provider.aparse_json(
        build_clustering_prompt(
            category_b=list(gap_analysis.category_b or []),
            category_c=category_c,
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
    # Persist only when the record is already in the session (the standalone
    # re-cluster path). _run_analysis now clusters BEFORE adding the record so
    # classification + clusters publish in ONE commit — a committed row must
    # never be readable without its clusters (Spaghettieis UAT 2026-07-13).
    if gap_analysis in db:
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
    *,
    clamp_to_previous: bool = False,
) -> GapAnalysisResponse:
    job_dict = _job_inputs(job)

    # E037 PQ #3 — idempotency: same (job, profile) → same score, computed once.
    # Reuse the latest stored row when its fingerprint still matches; only re-run
    # the LLM (and insert a new row) when the profile or JD genuinely changed.
    # repoint_flow_gap_analysis on the reuse path keeps the flow FK and the
    # latest-by-created_at read path converged on the SAME row, so every screen
    # shows one score.
    # Lazy import avoids the gap<->flow.orchestrator import cycle (orchestrator
    # transitively imports gap via the session/application services).
    from applire.services.flow.orchestrator import repoint_flow_gap_analysis

    fingerprint = _input_fingerprint(job, profile)
    previous = await _latest_gap_analysis(job.id, db)
    if previous is not None and previous.input_fingerprint == fingerprint:
        await repoint_flow_gap_analysis(job.id, previous.id, db)
        return GapAnalysisResponse.model_validate(previous)

    # Pass 1: rule-based pre-classification
    pre = pre_classify(job_dict, profile.profile_json)

    # Pass 2: LLM refinement
    data: dict = await provider.aparse_json(
        build_user_prompt(job_dict, profile.profile_json, pre),
        system=SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=GAP_ANALYSIS_MAX_TOKENS,
    )

    classifications = data.get("classifications", [])

    # ADR-048: the single source of truth for every JD expectation. `reason` from
    # the classification serves as the grounding evidence for the ledger entry.
    keyword_ledger = build_keyword_ledger(
        [
            {
                "concept": c.get("requirement", ""),
                "status": c.get("status", "gap"),
                "evidence": c.get("reason", ""),
                "surface_forms": c.get("surface_forms"),
            }
            for c in classifications
        ],
        list(job.required_skills or []),
        list(job.nice_to_have_skills or []),
        list(job.keywords or []),
    )

    # ADR-048 §5 (amends ADR-035): re-source the match score from the ledger's
    # fit-weighted slice — the single source of truth — not a parallel
    # classification list. The formula and weights are unchanged.
    scored = compute_match_score_from_ledger(keyword_ledger)

    # Compute embedding similarity score (None when noop provider or embeddings absent)
    embedding_similarity_score = _compute_embedding_similarity(
        job.embedding,
        profile.embedding,
    )

    # E037 PQ #3 — monotonic-up clamp on the post-interview-answer (/gaps/refresh)
    # path: fit weights are fixed, so adding evidence can only raise the score.
    # Never let a re-evaluation lower the headline number ("adding evidence
    # lowered my score"). max(old, new) is the required floor.
    match_score = scored["match_score"]
    if (
        clamp_to_previous
        and previous is not None
        and previous.match_score is not None
        and (match_score is None or match_score < previous.match_score)
    ):
        match_score = previous.match_score

    record = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        match_score=match_score,
        input_fingerprint=fingerprint,
        embedding_similarity_score=embedding_similarity_score,
        critical_gaps=scored["critical_gaps"],
        minor_gaps=scored["minor_gaps"],
        strengths=data.get("strengths", []),
        keyword_gaps=data.get("keyword_gaps", []),
        category_a=scored["category_a"],
        category_b=scored["category_b"],
        category_c=scored["category_c"],
        keyword_ledger=keyword_ledger,
        requirement_breakdown=scored["requirement_breakdown"],
    )

    # Phase 2: semantic clustering — BEFORE the record is published. Committing
    # the row first opened a window where GET /gaps served it with empty
    # gap_clusters and the gaps screen hung on "Analyzing your profile…"
    # forever (Spaghettieis UAT 2026-07-13). A committed analysis now always
    # carries its clusters; if clustering dies, nothing is published and the
    # async gap job fails cleanly (retry recomputes from scratch).
    await cluster_gaps(record, job, provider, db)

    db.add(record)
    # Captured before the commit: rollback expires ORM objects, so the recovery
    # path must not lazy-load `job.id` (sync IO inside the async session).
    job_id = job.id
    try:
        await db.commit()
    except IntegrityError:
        # uq_gap_analyses_live_fingerprint: a concurrent run committed the same
        # (job, fingerprint) between our idempotency pre-check and this commit.
        # Adopt the winner's row — one row, one score (E037 PQ #3).
        await db.rollback()
        winner_result = await db.execute(
            select(GapAnalysis)
            .where(
                GapAnalysis.job_analysis_id == job_id,
                GapAnalysis.input_fingerprint == fingerprint,
                GapAnalysis.deleted_at.is_(None),
            )
            .order_by(desc(GapAnalysis.created_at))
            .limit(1)
        )
        winner = winner_result.scalar_one_or_none()
        if winner is None:
            raise
        await repoint_flow_gap_analysis(job_id, winner.id, db)
        return GapAnalysisResponse.model_validate(winner)
    await db.refresh(record)

    # Keep the owning flow's gap_analysis_id FK pointed at the newest analysis so
    # the CV/flow read path reports the post-interview score, not the stale
    # pre-interview one. Single seam: every recompute path (/gaps/refresh,
    # interview completion, gap-click) routes through here. repoint_flow_gap_analysis
    # is imported at the top of this function.
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
