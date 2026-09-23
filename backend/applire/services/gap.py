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

import copy
import hashlib
import json
import logging
import math
import uuid
from collections import Counter
from typing import Any

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
from applire.schemas.gap_cluster import LLM_CLUSTER_KEYS, GapClusterSchema
from applire.services.ats_audit import _norm as _ats_norm
from applire.services.ats_audit import surface_present
from applire.services.gap_coverage import (
    AnswerScope,
    all_members,
    initialise_cluster_record,
    refresh_cluster_from_ledger,
    resplit_cluster,
)
from applire.services.gap_inference import pre_classify
from applire.services.keyword_ledger import (
    _annotate_narrative_backed,
    _enforce_denial_stance,
    _matches,
    annotate_evidence_owners,
    assert_claimable_backed,
    build_keyword_ledger,
    downgrade_ledger_for_concepts,
    is_scope_entry,
    keyword_liabilities,
    keyword_only_honest_gaps,
)
from applire.services.profile.reconcile.stance import denial_release_corpus
from applire.services.match_score import compute_match_score_from_ledger
from applire.services.scope_requirements import (
    build_scope_ledger_entries,
    build_scope_prompt_block,
)

logger = logging.getLogger(__name__)


def _unwrap_clusters(raw: Any) -> list:
    """Normalise the clustering LLM payload to a list of cluster dicts (#166).

    Every real provider forces JSON-*object* mode (mistral/requesty/openrouter/
    openai response_format, anthropic `{`-prefill), so a compliant model can NEVER
    return a bare top-level array — the shape the prompt used to demand. Accept
    every shape a provider realistically emits, mirroring the reconcile engine's
    tolerant unwrap (services/profile/reconcile/engine.py):
      - dict with a list-valued "clusters" key (the envelope the prompt now asks for);
      - a dict that itself validates as a single GapClusterSchema (observed: a bare
        single-cluster object from Requesty 2026-07-15);
      - dict with exactly one list-valued key under any other name;
      - a bare list (lenient providers / the legacy shape);
      - anything else → [] (the caller then warns on empty-out/non-empty-in).
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        clusters = raw.get("clusters")
        if isinstance(clusters, list):
            return clusters
        try:
            GapClusterSchema.model_validate(raw)
            return [raw]
        except Exception:
            pass
        list_values = [v for v in raw.values() if isinstance(v, list)]
        if len(list_values) == 1:
            return list_values[0]
    return []


def _norm_gap(s: str) -> str:
    return (s or "").strip().casefold()


def _strengths_the_ledger_supports(
    strengths: list[str] | None, keyword_ledger: list[dict[str, Any]] | None
) -> list[str]:
    """The model's ``strengths`` list, minus every entry THIS SAME RESPONSE'S
    ledger marks unclaimable (E-4 / SF-GAP.10, 2026-09-11).

    ``critical_gaps`` / ``category_*`` are re-sourced from the ledger
    (:func:`compute_match_score_from_ledger`), i.e. from AFTER the ADR-040
    denial floor, the ADR-069 scope judgement and #318's claimable-backing
    heal have run. ``strengths`` was the one list still taken verbatim from the
    model, so every deterministic downgrade split the response in two: the
    2026-09-10 delivery run published ``Produktion`` under ``strengths`` and
    under ``critical_gaps`` at once, and ``Durchsetzungsstärke`` with it. The
    gaps screen renders both lists side by side.

    ADR-062 clause 1 classification: **FACT**. Nothing here re-judges whether
    something IS a strength — that judgement is the model's and stays the
    model's. The only question asked is whether the ledger row the same call
    produced for that exact concept says the candidate may claim it, and the
    answer is a status-enum read. A strength naming no ledger row at all is
    kept (no fact contradicts it), and a strength matching several rows is kept
    when ANY of them is claimable. Clause 5's direction: the pass can only ever
    remove an overclaim.
    """
    if not strengths:
        return list(strengths or [])
    claimable_by_name: dict[str, bool] = {}
    for row in keyword_ledger or []:
        if not isinstance(row, dict):
            continue
        names = [row.get("concept", ""), *(row.get("surface_forms") or [])]
        claim = bool(row.get("claimable"))
        for name in names:
            key = _norm_gap(name)
            if not key:
                continue
            claimable_by_name[key] = claimable_by_name.get(key, False) or claim
    kept: list[str] = []
    for item in strengths:
        if not isinstance(item, str):
            continue
        if claimable_by_name.get(_norm_gap(item)) is False:
            logger.info(
                "gap analysis: dropped %r from strengths — this run's ledger "
                "marks it unclaimable (E-4: one response may not publish a "
                "concept as a strength and a critical gap at once)",
                item,
            )
            continue
        kept.append(item)
    return kept


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
        # ADR-069 — quantified scope bars are score-bearing inputs too: they
        # produce fit-weighted ledger entries, so they must be in the
        # idempotency fingerprint (a JD re-analysis that gains a bar forces a
        # recompute). None (legacy rows) hashes identically to [] on purpose.
        "scope_requirements": job.scope_requirements or [],
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
    answer_scope: AnswerScope | None = None,
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

    ``answer_scope`` (ADR-089 clause 5) marks the ANSWER-DRIVEN path —
    ``AnswerScope()`` from ``POST /gaps/refresh``, ``AnswerScope(cluster_ids=…,
    answers=…)`` from an interview's completion. When a recompute happens on
    that path, every fresh ledger row is merged with the previous row for the
    SAME requirement (:func:`merge_ledger_per_requirement`): outside the touched
    set a downward move is replaced by the previous row unless the fresh row is
    a denial, and the ADR-059 denial floor plus ``assert_claimable_backed`` then
    run on the merged ledger. Headline, breakdown, categories and the persisted
    ledger are all computed ONCE from that merged ledger (the whole-slice clamp
    of ruling B-1 is retired). ``None`` is the non-answer path (first analysis,
    a JD change, a profile edit): fresh, no merge.

    Clusters (ADR-089 clause 4) are CARRIED FORWARD — ids, labels, members,
    ``outcome`` — whenever a previous row exists for the same job and the JD's
    requirement lists are unchanged, on either path; only askable concepts in
    no carried cluster go to the clustering LLM. A first analysis or a JD
    change clusters from scratch.

    Stores the result in gap_analyses and returns a GapAnalysisResponse.
    """
    job = await _resolve_job(job_id, db)
    profile = await _resolve_profile(db)
    return await _run_analysis(job, profile, db, provider, answer_scope=answer_scope)


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


async def downgrade_keyword_liability(
    job_id: uuid.UUID,
    concept: str,
    db: AsyncSession,
) -> GapAnalysisResponse:
    """Exit (b) of the #260 pre-generation liability check: the candidate's
    own choice to DROP a keyword-liability concept (a JD hard requirement,
    claimable, but with no narrative anywhere in the vault) rather than tell
    its story via ``resolve_gap`` (exit a).

    Deterministic, no LLM: flips the matching CLAIMABLE ledger entry to an
    honest gap (:func:`keyword_ledger.downgrade_ledger_for_concepts`) and
    re-derives the match score from the resulting ledger — the SAME formula
    ``analyze_gaps`` uses, so the displayed score honestly reflects that the
    concept is no longer being claimed (never a silent inflation). A no-op
    (unchanged row returned) when the concept doesn't match any claimable
    entry — never invents or removes a ledger row.
    """
    gap_analysis = await _latest_gap_analysis(job_id, db)
    if gap_analysis is None:
        raise LookupError(f"No gap analysis found for job {job_id}")

    new_ledger, changed = downgrade_ledger_for_concepts(gap_analysis.keyword_ledger, [concept])
    # #318 / ADR-061 — this seam only ever REMOVES claimable, so it cannot
    # create a violation of its own; it is still a persist seam, and a row that
    # was already corrupt must not ride through it unhealed (`violations` alone
    # is enough to make the write worth doing).
    # The row's OWN profile (never "the latest") — that is the vault this
    # ledger was classified against.
    profile = await db.get(MasterProfile, gap_analysis.profile_id)
    new_ledger, violations = assert_claimable_backed(
        new_ledger,
        profile.profile_json if profile else None,
        seam="keyword-liability downgrade",
    )
    if changed or violations:
        # JSONB tracking gotcha (mirrors session.py's upgrade path): keyword_ledger
        # is a plain _JSON column, not a MutableList — reassign the WHOLE list.
        gap_analysis.keyword_ledger = new_ledger
        # ADR-089 clause 3 — the per-gap record is derived from this ledger, so a
        # ledger write on this row re-splits every cluster against it (members
        # kept, `outcome.asked`/`session_ids` kept, `coverage` re-derived). Else
        # the dropped liability's cluster keeps the coverage of a claim the
        # candidate just withdrew until the next recompute (SF-GAP.17).
        denied = [
            d
            for d in (((profile.profile_json or {}).get("metadata") or {}).get("denied_concepts") or [])
            if isinstance(d, dict) and d.get("concept")
        ] if profile else []
        gap_analysis.gap_clusters = [
            resplit_cluster(c, new_ledger, denied) if isinstance(c, dict) else c
            for c in (gap_analysis.gap_clusters or [])
        ]
        scored = compute_match_score_from_ledger(new_ledger)
        gap_analysis.match_score = scored["match_score"]
        gap_analysis.category_a = scored["category_a"]
        gap_analysis.category_b = scored["category_b"]
        gap_analysis.category_c = scored["category_c"]
        gap_analysis.critical_gaps = scored["critical_gaps"]
        gap_analysis.minor_gaps = scored["minor_gaps"]
        gap_analysis.requirement_breakdown = scored["requirement_breakdown"]
        await db.commit()
        await db.refresh(gap_analysis)
    return GapAnalysisResponse.model_validate(gap_analysis)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def askable_gap_inputs(gap_analysis: GapAnalysis) -> list:
    """The augmented category_c list cluster_gaps() actually clusters on (#166
    Important-1).

    Persisted ``gap_analysis.category_c`` PLUS keyword-only honest gaps (US204,
    ADR-048 §10): concepts that carry no fit weight and so never reach category_c
    on their own, deduped against the category_c entries already present. This is
    the SINGLE place that augmentation happens — any other caller that needs to
    know "what will cluster_gaps() see as input" must go through this helper
    rather than re-deriving it, or it will silently diverge (the exact bug this
    fixes: a session-side guard that keyed on raw category_c alone).
    """
    category_c = list(gap_analysis.category_c or [])
    seen_c = {_norm_gap(g) for g in category_c}
    for concept in keyword_only_honest_gaps(getattr(gap_analysis, "keyword_ledger", None)):
        if _norm_gap(concept) not in seen_c:
            category_c.append(concept)
            seen_c.add(_norm_gap(concept))
    # #260: keyword-LIABILITY concepts (required + claimable + no narrative
    # anywhere) live in category_a — a plain "strength" — and so never reach
    # category_c on their own either, exactly like the keyword-only honest
    # gaps above. Fold them in through the SAME seam so exit (a) of the
    # pre-generation liability check ("elicit the story via resolve_gap")
    # is real: the concept becomes clusterable, and therefore reachable via
    # a resolve_gap `gap_id`, without a second clustering call or any prompt
    # change (the existing clustering LLM already accepts arbitrary concept
    # strings — see #204's identical precedent above).
    for entry in keyword_liabilities(getattr(gap_analysis, "keyword_ledger", None)):
        concept = entry.get("concept", "")
        if concept and _norm_gap(concept) not in seen_c:
            category_c.append(concept)
            seen_c.add(_norm_gap(concept))
    return category_c


def has_clustering_input(gap_analysis: GapAnalysis) -> bool:
    """True when cluster_gaps() has non-empty input to work with.

    Mirrors cluster_gaps()'s own "was there something to cluster" test — the
    augmented category_c (askable_gap_inputs) OR category_b. Callers outside
    cluster_gaps (e.g. the session honest-fallback guard) MUST use this instead
    of inspecting gap_analysis.category_c directly, so the two can never diverge
    again (#166 Important-1).
    """
    return bool(askable_gap_inputs(gap_analysis) or list(gap_analysis.category_b or []))


def _category_c_members(category_c: list[str], liabilities: list[str] | None) -> set[str]:
    """The normalised Category C input MINUS the #260 keyword liabilities
    :func:`askable_gap_inputs` folds in — a liability is a strength to narrate,
    not an absence (see :func:`_reconcile_cluster_categories`)."""
    return {_norm_gap(g) for g in category_c} - {_norm_gap(g) for g in liabilities or []}


def _derived_category(members: list[str], c_members: set[str]) -> str:
    """THE #675 line-60 fact: "C" when any member came from the Category C
    input, else "B". One implementation for freshly clustered AND carried
    clusters (ADR-089 clause 4 re-derives a carried cluster's category on
    every recompute)."""
    return "C" if any(_norm_gap(g) in c_members for g in members) else "B"


def _reconcile_cluster_categories(
    clusters: list[dict],
    *,
    category_c: list[str],
    category_b: list[str],
    category_a: list[str],
    liabilities: list[str] | None = None,
) -> list[dict]:
    """Set each cluster's ``category`` from its members, and refuse a member
    the analysis calls a strength (#675 line 60).

    **ADR-062 clause 1 classification: FACT.** Nothing here re-judges whether a
    concept is a gap, how severe it is, or which cluster it belongs in — those
    are the model's and stay the model's. The only questions asked are which of
    the caller's own input lists a member string came from, and whether the same
    analysis already published it under ``category_a``. Both are list lookups on
    data this function is handed.

    Two rules, in this order:

    1. **A member the analysis calls a strength is dropped.** ``category_a`` is
       "already demonstrated"; a cluster naming one positions a strength as an
       absence, and the agent guide tells an agent a C cluster is a true gap to
       position, never to claim. A concept that is in ``category_a`` *and* was
       nevertheless submitted for clustering is kept — that is the deliberate
       keyword-liability fold of :func:`askable_gap_inputs` (#260), not a model
       error. A cluster left with no members at all is dropped whole, the same
       disposition :func:`~applire.services.interview_graph.filter_answered_concepts`
       already gives a cluster whose concepts have all gone "direct".
    2. **The category is derived**: "C" when any surviving member came from the
       Category C input, else "B" — where "the Category C input" EXCLUDES the
       #260 keyword LIABILITIES :func:`askable_gap_inputs` folds in (``liabilities``):
       a liability is a claimable hard requirement that lacks a story, i.e. a
       strength to narrate, not an absence — the 2026-09-19 delivery run derived
       every cluster as "C" (10 of 10, two of them carrying only liabilities and
       Category B members) before this exclusion. The clustering prompt used to state exactly
       this rule and the model could only restate it; measured over every
       captured clustering record (63 records / 198 clusters across
       ``logs/llm/`` and ``backend/logs/llm/``), 53 clusters carried a category
       contradicting their own members — all 53 understating severity, which
       silently demotes a C-cluster question in the interview plan's C-before-B
       ordering and tells the agent door the gap is softer than it is.

    A member that matches NO input list (a paraphrase — "microservices
    architecture" for an input "microservices", 49 captured occurrences) is
    neither dropped nor counted: it is not a fact against the member, and
    dropping it would delete a real gap the model merely reworded. Prompt v2
    asks for verbatim copies instead.
    """
    submitted = {_norm_gap(g) for g in category_c} | {_norm_gap(g) for g in category_b}
    c_members = _category_c_members(category_c, liabilities)
    strengths = {_norm_gap(g) for g in category_a or []} - submitted

    kept: list[dict] = []
    for cluster in clusters:
        members = [g for g in (cluster.get("gaps") or []) if isinstance(g, str)]
        surviving = [g for g in members if _norm_gap(g) not in strengths]
        for dropped in [g for g in members if _norm_gap(g) in strengths]:
            logger.info(
                "cluster_gaps: dropped %r from cluster %r — this analysis "
                "publishes it under category_a (a strength is not a gap)",
                dropped,
                cluster.get("id"),
            )
        if not surviving:
            logger.info(
                "cluster_gaps: dropped cluster %r — every member is a "
                "category_a strength",
                cluster.get("id"),
            )
            continue
        derived = _derived_category(surviving, c_members)
        if cluster.get("category") != derived:
            logger.info(
                "cluster_gaps: cluster %r category %r → %r (derived from its "
                "members, #675 line 60)",
                cluster.get("id"),
                cluster.get("category"),
                derived,
            )
        kept.append({**cluster, "gaps": surviving, "category": derived})
    return kept


async def _cluster_concepts(
    gap_analysis: GapAnalysis,
    job: JobAnalysis,
    provider: LLMProvider,
    db: AsyncSession,
    *,
    category_b: list[str],
    category_c: list[str],
) -> list[dict]:
    """The clustering LLM call over the given askable concepts, validated and
    reconciled (#675 line 60). Returns the clusters; persists nothing.

    ``category_c`` is the AUGMENTED Category C input (:func:`askable_gap_inputs`
    or a subset of it). The call is the same whether it clusters a whole
    analysis (:func:`cluster_gaps`) or only the concepts no carried cluster
    holds yet (:func:`_carry_forward_clusters`, ADR-089 clause 4).
    """
    # #3 (ADR-038): cluster descriptions (jd_context) render on the conversational gaps
    # page, so they follow the candidate's conversation language — explicit UI choice,
    # else the JD's language (amendment 2026-08-01, #400: job-scoped surface). Local
    # import avoids the session<->gap circular dependency.
    from applire.services.session import get_conversation_language
    lang = await get_conversation_language(db, job_id=job.id)
    raw = await provider.aparse_json(
        build_clustering_prompt(
            category_b=list(category_b),
            category_c=list(category_c),
            required_skills=list(job.required_skills or []),
            nice_to_have_skills=list(job.nice_to_have_skills or []),
            lang=lang,
        ),
        system=CLUSTERING_SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=GAP_CLUSTERING_MAX_TOKENS,
    )
    raw_clusters = _unwrap_clusters(raw)
    validated = []
    for item in raw_clusters:
        try:
            # Only the clustering contract's own keys: the per-gap record
            # (`outcome`, `coverage`) is Applire's to write, never the model's,
            # and the derived `budget_remaining` is never persisted (ruling C-2).
            validated.append(
                GapClusterSchema.model_validate(item).model_dump(include=LLM_CLUSTER_KEYS)
            )
        except Exception:
            logger.debug("cluster_gaps: dropped malformed cluster %r", item)
    # Empty clusters out of non-empty gaps in is almost always a parse failure
    # (JSON-mode envelope not unwrapped, truncation, …) — NOT a genuine "no gaps"
    # outcome. Downstream a false-empty here made the interview tell candidates with
    # critical gaps that they were a "strong match" (#166). Surface it loudly.
    if not validated and (category_c or category_b):
        logger.warning(
            "cluster_gaps: produced 0 clusters from non-empty gaps "
            "(category_c=%d, category_b=%d) — likely a clustering parse failure; "
            "raw payload type=%s",
            len(category_c),
            len(category_b),
            type(raw).__name__,
        )
    # #675 line 60: the B/C category is a fact about the members, not the
    # model's to write, and a member the analysis calls a strength is not a gap.
    return _reconcile_cluster_categories(
        validated,
        category_c=list(category_c),
        category_b=list(category_b),
        category_a=list(getattr(gap_analysis, "category_a", None) or []),
        liabilities=[
            e.get("concept", "")
            for e in keyword_liabilities(getattr(gap_analysis, "keyword_ledger", None))
        ],
    )


async def cluster_gaps(
    gap_analysis: GapAnalysis,
    job: JobAnalysis,
    provider: LLMProvider,
    db: AsyncSession,
) -> None:
    """Run clustering LLM call and persist result to gap_analysis.gap_clusters."""
    # US204 (ADR-048 §10): keyword-only honest gaps carry no fit weight, so they
    # never reach category_c — route them into the interview here, deduped against
    # the category_c gaps already present. The clustering LLM merges by domain and
    # writes an estimate-honest jd_context, so they surface as askable clusters.
    gap_analysis.gap_clusters = await _cluster_concepts(
        gap_analysis,
        job,
        provider,
        db,
        category_b=list(gap_analysis.category_b or []),
        category_c=askable_gap_inputs(gap_analysis),
    )
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


#: Classification keys that may become a keyword-ledger entry. Anything the
#: classifier returns outside this set is INTERNAL and never reaches the ledger
#: — which is what makes ADR-061's 2026-08-02 amendment structural rather than
#: rule-dependent (see :func:`ledger_input_from_classification`).
_LEDGER_PUBLISHABLE_KEYS = frozenset(
    {"concept", "status", "evidence", "surface_forms", "adjacent_evidence"}
)


def ledger_input_from_classification(c: dict[str, Any]) -> dict[str, Any]:
    """One classifier result → one keyword-ledger input row.

    ADR-048: the ledger is the single source of truth for every JD expectation,
    and the classification's ``reason`` is its grounding evidence — the text
    both document writers are given as what the candidate can claim.

    **ADR-061 amended 2026-08-02 (#427): `reason` carries ONLY that.** The
    classifier also applies a declared-proficiency ceiling (clause 5), and the
    prompt used to ask it to name that ceiling inside ``reason``. Because this
    function copies ``reason`` onto ``evidence``, and four generation-facing
    renderers emit ``evidence`` verbatim into both document chains, charter run
    15 published the ceiling as the candidate's own words — "Grundkenntnisse in
    SAP PP/MM" against a vault holding daily use of those modules and a
    Key-User role. A cap is a fact about the *classification*, not about the
    candidate. The ceiling now goes to ``classification_note``, which this
    function does not read: the internal field has no path into the ledger at
    all, so no renderer can reach it and no future renderer can be tempted to.
    Its durable home is the LLM exchange log, which is already the artefact the
    charter-run procedure reads.
    """
    return {
        "concept": c.get("requirement", ""),
        "status": c.get("status", "gap"),
        "evidence": c.get("reason", ""),
        "surface_forms": c.get("surface_forms"),
        # ADR-048 am. 2026-07-27 — on a "partial", the vault item that IS the
        # adjacent capability, so the writers can promote arc42 rather than
        # being told to surface the JD's word "TOGAF".
        "adjacent_evidence": c.get("adjacent_evidence"),
    }


# ---------------------------------------------------------------------------
# The answer-driven score merge (ADR-089 clause 5)
# ---------------------------------------------------------------------------
#
# A recompute after an interview answer used to publish a full LLM
# re-classification of EVERY requirement, and the whole-slice clamp of ruling
# B-1 (2026-09-20) was disabled whenever any requirement anywhere regressed —
# one stochastic flip in an unrelated requirement dragged the score down
# (founder UAT 2026-09-23, 50 → 42). The merge below is per requirement: only
# what this session's answers touched may move freely; everything else may go
# UP but not down, unless the fresh row is a denial — and the two deterministic
# floors then run on the merged ledger, so a denial and a vanished vault
# backing still lower the score (ruling B-1's purpose, kept).
#
# ADR-062 clause 1/6 declaration: every function here computes FACTS — status
# enum ranks, list membership, the ledger builder's own string matcher and the
# shared presence predicate. None interprets prose; none calls a model.

#: Claim rank of a ledger status. ``denied`` ranks with ``gap`` (both earn 0.0,
#: neither is claimable); a FRESH ``denied`` is never replaced (clause 5).
_STATUS_RANK = {"direct": 2, "partial": 1, "gap": 0, "denied": 0}


def _row_names(row: dict[str, Any]) -> list[str]:
    """A ledger row's concept + surface forms (raw strings, blanks dropped)."""
    names = [row.get("concept", ""), *(row.get("surface_forms") or [])]
    return [n for n in names if isinstance(n, str) and _norm_gap(n)]


def _jd_terms(job: JobAnalysis) -> list[str]:
    """The job's static requirement vocabulary, normalised, in JD order — the
    ledger builder's union keys (``required_skills`` / ``nice_to_have_skills`` /
    ``keywords``). Stable across runs, unlike a ledger ``concept``."""
    seen: set[str] = set()
    out: list[str] = []
    for items in (job.required_skills, job.nice_to_have_skills, job.keywords):
        for raw in items or []:
            key = _norm_gap(raw) if isinstance(raw, str) else ""
            if key and key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _term_owners(ledger: list[Any], terms: list[str]) -> dict[str, int]:
    """JD term → index of the ledger row that OWNS it, by the ledger builder's
    own rule (``build_keyword_ledger``'s exact owner, #675 line 46): the first
    row whose concept or a surface form norm-EQUALS the term; else the first row
    whose names ``_matches`` it."""
    names = [
        {_norm_gap(n) for n in _row_names(r)} if isinstance(r, dict) else set()
        for r in ledger
    ]
    owners: dict[str, int] = {}
    for term in terms:
        idx = next((i for i, ns in enumerate(names) if term in ns), None)
        if idx is None:
            idx = next(
                (i for i, ns in enumerate(names) if any(_matches(term, n) for n in ns)),
                None,
            )
        if idx is not None:
            owners[term] = idx
    return owners


def pair_rows_by_requirement(
    fresh: list[Any], previous: list[Any], jd_terms: list[str]
) -> list[int | None]:
    """For each fresh ledger row, the index of the previous row that is the
    SAME requirement — or ``None`` (ADR-089 clause 5).

    Rows do not persist which JD term they were credited with, so it is
    re-derived here: both ledgers' rows are matched against the job's static
    lists (:func:`_term_owners`), and two rows owning the same JD term are the
    same requirement. When the terms a fresh row owns were owned by more than
    one previous row, the one with the same normalised concept wins, else the
    one owning the most of those terms (JD order breaks a tie). A fresh row
    that owns no JD term (a scope entry, a row whose keys an exact owner took)
    falls back to the normalised concept.
    """
    f_owners = _term_owners(fresh, jd_terms)
    p_owners = _term_owners(previous, jd_terms)
    prev_by_concept: dict[str, int] = {}
    for j, row in enumerate(previous):
        if isinstance(row, dict):
            key = _norm_gap(row.get("concept", ""))
            if key:
                prev_by_concept.setdefault(key, j)
    pairs: list[int | None] = []
    for i, row in enumerate(fresh):
        if not isinstance(row, dict):
            pairs.append(None)
            continue
        concept = _norm_gap(row.get("concept", ""))
        candidates = [p_owners[t] for t in jd_terms if f_owners.get(t) == i and t in p_owners]
        if not candidates:
            pairs.append(prev_by_concept.get(concept) if concept else None)
            continue
        same_concept = [
            j for j in candidates if _norm_gap(previous[j].get("concept", "")) == concept
        ]
        if same_concept:
            pairs.append(same_concept[0])
            continue
        counts = Counter(candidates)
        pairs.append(max(dict.fromkeys(candidates), key=lambda j: counts[j]))
    return pairs


def _is_touched(
    names: list[str], touched_members: list[str], answers_norm: list[str]
) -> bool:
    """ADR-089 clause 5's TOUCHED test for one requirement (its fresh and its
    previous row's names together): it speaks for a member of a cluster this
    session worked (the ledger builder's ``_matches``), or one of its names is
    present in any answer of the session (THE presence predicate,
    ``ats_audit.surface_present`` — a self-correction outside the answered
    cluster)."""
    norm_names = [_norm_gap(n) for n in names]
    for member in touched_members:
        m = _norm_gap(member)
        if m and any(_matches(m, n) for n in norm_names if n):
            return True
    return any(surface_present(n, a) for a in answers_norm for n in names)


def _carried_row(previous_row: dict[str, Any], fresh_row: dict[str, Any]) -> dict[str, Any]:
    """The previous row standing in for a fresh one that moved down outside the
    touched set. Its CLAIM (status, claimable, evidence, adjacency, JD phrase,
    denial level) is the previous row's; its IDENTITY in this ledger is the
    fresh row's — ``concept`` and the SCORE SLOT (``sources`` / ``fit_weight``:
    a JD term is credited to exactly one row of THIS ledger, #675 line 46) — so
    two fresh rows paired with one broader previous row never publish the same
    requirement name twice. Surface forms are the union of both, fresh first
    (the SF-GAP.12 precedent), so the denial floor that runs next sees every
    name."""
    out = copy.deepcopy(previous_row)
    out["concept"] = fresh_row.get("concept", out.get("concept", ""))
    out["sources"] = list(fresh_row.get("sources") or [])
    out["fit_weight"] = fresh_row.get("fit_weight", 0.0)
    forms = [
        f
        for f in [*(fresh_row.get("surface_forms") or []), *(previous_row.get("surface_forms") or [])]
        if isinstance(f, str) and f.strip()
    ]
    out["surface_forms"] = list(dict.fromkeys(forms)) or [out.get("concept", "")]
    return out


def merge_ledger_per_requirement(
    fresh: list[dict[str, Any]],
    previous: list[dict[str, Any]] | None,
    *,
    jd_terms: list[str],
    touched_members: list[str],
    answers: tuple[str, ...] | list[str],
) -> tuple[list[dict[str, Any]], list[int]]:
    """PURE. ADR-089 clause 5's per-requirement merge, BEFORE the floors.

    For every fresh row paired with a previous row of the same requirement
    (:func:`pair_rows_by_requirement`): outside the touched set
    (:func:`_is_touched`), a downward move (``direct → partial → gap``) is
    replaced by the previous row (:func:`_carried_row`) — unless the fresh row
    is ``denied``. Inside the touched set the fresh row stands, and so does
    every fresh row with no previous counterpart.

    Returns ``(merged, carried_indices)`` — the indices of the rows that now
    carry a previous row, which the caller runs the denial floor on and
    re-annotates against the current vault. The floors are NOT applied here.
    """
    prev = list(previous or [])
    merged = [dict(r) if isinstance(r, dict) else r for r in fresh]
    if not prev:
        return merged, []
    answers_norm = [_ats_norm(a) for a in answers or () if isinstance(a, str) and a.strip()]
    pairs = pair_rows_by_requirement(fresh, prev, jd_terms)
    carried: list[int] = []
    for i, j in enumerate(pairs):
        f = fresh[i]
        if j is None or not isinstance(f, dict):
            continue
        p = prev[j]
        f_status = f.get("status")
        if f_status == "denied":
            continue  # a denial always stands (ruling B-1's purpose)
        if _STATUS_RANK.get(p.get("status"), 0) <= _STATUS_RANK.get(f_status, 0):
            continue  # not a downward move
        if _is_touched(_row_names(f) + _row_names(p), touched_members, answers_norm):
            continue  # this session's answers may move it freely
        merged[i] = _carried_row(p, f)
        carried.append(i)
        logger.info(
            "gap analysis merge: kept %r at %r (fresh run said %r) — outside the "
            "requirements this session touched (ADR-089 clause 5)",
            p.get("concept"),
            p.get("status"),
            f_status,
        )
    return merged, carried


def _apply_floors_to_merged(
    merged: list[dict[str, Any]],
    carried: list[int],
    *,
    denied_concepts: list[dict[str, Any]],
    profile_json: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """The two deterministic floors on the MERGED ledger (ADR-089 clause 5).

    The fresh rows already passed both inside :func:`build_keyword_ledger` /
    :func:`_run_analysis`; a carried row was floored against an older vault
    and older denials. So each carried non-scope row gets the ADR-059 denial
    floor against TODAY's denials, and the builder's final annotation pass
    (``narrative_backed``, ``evidence_owners``) against today's vault; then
    ``assert_claimable_backed`` runs over the whole merged ledger (idempotent on
    the fresh rows), which heals a carried claim whose vault evidence is gone.
    Scope entries keep their own floor (``scope_requirements``), exactly as on
    the fresh path.
    """
    out = list(merged)
    idx = [i for i in carried if isinstance(out[i], dict) and not is_scope_entry(out[i])]
    if idx:
        rows = [out[i] for i in idx]
        rows = _enforce_denial_stance(
            rows, denied_concepts, denial_release_corpus(profile_json) or None
        )
        rows = annotate_evidence_owners(_annotate_narrative_backed(rows, profile_json), profile_json)
        for i, row in zip(idx, rows):
            out[i] = row
    healed, _violations = assert_claimable_backed(out, profile_json, seam="gap-analysis merge")
    return healed


def _jd_lists_unchanged(job: JobAnalysis, previous: GapAnalysis | None) -> bool:
    """Were the previous row's requirements built from the SAME JD lists?
    (ADR-089 clause 4: carry clusters forward only then.)

    Read off the previous ledger, which carries a row for every JD term of the
    run that built it (a classified row matching it, or the builder's default
    gap row): every current term must match some previous row (no term was
    added), and every previous row that was credited with a JD term (non-empty
    ``sources``) must match some current term (none was removed). A previous row
    without a ledger (pre-E037) cannot answer, and reads as changed. A JD is
    immutable per job id in this codebase (a new posting is a new job), so this
    is a guard, not a hot path.
    """
    if previous is None or not previous.keyword_ledger:
        return False
    terms = _jd_terms(job)
    rows = [r for r in previous.keyword_ledger if isinstance(r, dict) and not is_scope_entry(r)]
    names = [[_norm_gap(n) for n in _row_names(r)] for r in rows]
    for term in terms:
        if not any(_matches(term, n) for ns in names for n in ns):
            return False
    for row, ns in zip(rows, names):
        if row.get("sources") and not any(_matches(t, n) for t in terms for n in ns):
            return False
    return True


def _unique_cluster_ids(clusters: list[dict], taken: set[str]) -> list[dict]:
    """Appended clusters never reuse a carried cluster's id (the id is the
    record's identity, ADR-089 clause 4)."""
    out: list[dict] = []
    used = set(taken)
    for c in clusters:
        cid = str(c.get("id") or "cluster")
        if cid in used:
            n = 2
            while f"{cid}-{n}" in used:
                n += 1
            logger.info("cluster_gaps: appended cluster id %r renamed to %r (taken)", cid, f"{cid}-{n}")
            cid = f"{cid}-{n}"
            c = {**c, "id": cid}
        used.add(cid)
        out.append(c)
    return out


async def _carry_forward_clusters(
    record: GapAnalysis,
    previous: GapAnalysis,
    job: JobAnalysis,
    provider: LLMProvider,
    db: AsyncSession,
    *,
    denied_concepts: list[dict[str, Any]],
) -> list[dict]:
    """ADR-089 clause 4 — the previous row's clusters, re-split against THIS
    row's ledger, plus freshly clustered concepts no carried cluster holds.

    Every carried cluster keeps its id, label, ``jd_context``, members and
    ``outcome``; :func:`gap_coverage.refresh_cluster_from_ledger` drops members
    that match no row of the new ledger (and a cluster left with none) and
    re-derives the open/covered/declined split and ``coverage``; the B/C
    ``category`` is re-derived with the #675 line-60 fact. A carried member that
    became claimable moves to ``covered`` — it is not dropped, and a cluster
    with no open member stays listed with its coverage.

    Only askable concepts (the same augmented input :func:`cluster_gaps` uses)
    that match no carried member go to the clustering LLM — none at all means
    no call. Their clusters are appended with fresh records.
    """
    ledger = record.keyword_ledger or []
    carried: list[dict] = []
    for cluster in previous.gap_clusters or []:
        if not isinstance(cluster, dict) or not cluster.get("id"):
            continue
        refreshed = refresh_cluster_from_ledger(cluster, ledger, denied_concepts)
        if refreshed is not None:
            carried.append(refreshed)

    askable_c = askable_gap_inputs(record)
    c_members = _category_c_members(
        askable_c, [e.get("concept", "") for e in keyword_liabilities(ledger)]
    )
    for cluster in carried:
        cluster["category"] = _derived_category(all_members(cluster), c_members)

    carried_members = [_norm_gap(m) for c in carried for m in all_members(c)]

    def _held(concept: str) -> bool:
        key = _norm_gap(concept)
        return bool(key) and any(_matches(key, m) for m in carried_members if m)

    leftover_c = [g for g in askable_c if not _held(g)]
    leftover_b = [g for g in (record.category_b or []) if not _held(g)]
    appended: list[dict] = []
    if leftover_c or leftover_b:
        fresh = await _cluster_concepts(
            record, job, provider, db, category_b=leftover_b, category_c=leftover_c
        )
        appended = [
            initialise_cluster_record(c, ledger, denied_concepts)
            for c in _unique_cluster_ids(fresh, {c["id"] for c in carried})
        ]
    return carried + appended


async def _run_analysis(
    job: JobAnalysis,
    profile: MasterProfile,
    db: AsyncSession,
    provider: LLMProvider,
    *,
    answer_scope: AnswerScope | None = None,
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

    # ADR-069 clause 2 — assemble the scope confrontation (facts): the JD's
    # stated bars verbatim + the candidate's typed vault values with their
    # field semantics. The sufficiency judgement happens inside the same LLM
    # call; the floor + citation check run on its output below.
    scope_block = build_scope_prompt_block(
        job.scope_requirements, profile.profile_json, job.jd_language
    )

    # Pass 2: LLM refinement
    data: dict = await provider.aparse_json(
        build_user_prompt(job_dict, profile.profile_json, pre, scope_block),
        system=SYSTEM_PROMPT,
        temperature=0.1,
        max_tokens=GAP_ANALYSIS_MAX_TOKENS,
    )

    classifications = data.get("classifications", [])

    # #231 — the candidate's own persisted denials are a deterministic floor
    # the classifier's adjacency inference can never override (F8: a denied
    # concept must never resurface as a claimable "supported by your profile"
    # ledger entry on a later run).
    profile_meta = (profile.profile_json or {}).get("metadata") or {}
    # ADR-064 — pass the raw DeniedConcept dicts through (not just the bare
    # concept string) so denial_level reaches build_keyword_ledger's floor
    # (_enforce_denial_stance) and is mirrored onto the forced ledger entry.
    denied_concepts = [
        d
        for d in (profile_meta.get("denied_concepts") or [])
        if isinstance(d, dict) and d.get("concept")
    ]

    keyword_ledger = build_keyword_ledger(
        [ledger_input_from_classification(c) for c in classifications],
        list(job.required_skills or []),
        list(job.nice_to_have_skills or []),
        list(job.keywords or []),
        denied_concepts=denied_concepts,
        # #249 run-4: the vault's own literal text, so the denial floor can
        # independently affirm a broad term against real evidence instead of
        # always fail-closing on a narrow denial's compound-containment rule.
        profile_json=profile.profile_json,
        # SF-GAP.12 (Nougat build-2 delivery-run 2026-09-11) — this recompute's
        # own classification call is free to name different surface_forms for
        # the same concept than the LAST build did; without the prior row on
        # hand a released containment-only denial can silently re-flip to a
        # gap on a later recompute of identical denied_concepts/vault state.
        # `previous` is already loaded above for the idempotency check — reuse
        # it, never a second query.
        previous_ledger=(previous.keyword_ledger if previous is not None else None),
        # #415 (ruling W-4/W-5): the posting's own text, so each claimable entry can
        # carry the VERBATIM sentence that qualifies its concept. A coarse concept
        # ("HGB") satisfies every coverage instrument while the specific evidence the
        # posting actually asked about is never selected — the ledger is where the
        # writer's input view learns WHICH evidence answers the requirement.
        jd_text=job.raw_text,
    )

    # ADR-069 clause 3 — scope entries join the ledger BEFORE the score and the
    # category split, so they ride the existing rails (partial → category B →
    # cluster → targeted probe). Floor + citation check applied inside; the
    # entry's evidence string is composed from the recorded facts, never from
    # the model's reason alone (SF-GAP.4).
    # ADR-070 clause 1: profile prose is passed so a model-cited attestation can
    # be verified fail-closed and stored on bar.attested.
    keyword_ledger = keyword_ledger + build_scope_ledger_entries(
        scope_block, data.get("scope_classifications"), profile_json=profile.profile_json
    )

    # #318 / ADR-061 — THE affirmative invariant, on the row that is actually
    # persisted: a `claimable` entry with no vault evidence must be impossible.
    # Placed after the scope entries join (so the check sees the whole row) and
    # BEFORE the score, so a healed entry can never leave a silently inflated
    # match_score behind — the same ordering `downgrade_keyword_liability` uses.
    keyword_ledger, _violations = assert_claimable_backed(
        keyword_ledger, profile.profile_json, seam="gap-analysis build"
    )

    # ADR-089 clause 4/5 — is THIS recompute one of the same job's requirement
    # set (a previous row, the same JD lists)? Only then may the answer-driven
    # merge compare rows and the clusters carry forward.
    same_jd = _jd_lists_unchanged(job, previous)

    # ADR-089 clause 5 — the answer-driven path merges per requirement: outside
    # what this session's answers touched, a downward move is replaced by the
    # previous row (unless the fresh row is a denial); the denial floor and
    # #318's claimable-backing invariant then run on the MERGED ledger. The
    # non-answer path (first analysis, JD change, a profile edit) publishes the
    # fresh ledger unchanged.
    if answer_scope is not None and same_jd:
        touched_members = [
            member
            for cluster in (previous.gap_clusters or [])
            if isinstance(cluster, dict) and cluster.get("id") in set(answer_scope.cluster_ids)
            for member in all_members(cluster)
        ]
        merged, carried_idx = merge_ledger_per_requirement(
            keyword_ledger,
            previous.keyword_ledger,
            jd_terms=_jd_terms(job),
            touched_members=touched_members,
            answers=answer_scope.answers,
        )
        keyword_ledger = _apply_floors_to_merged(
            merged,
            carried_idx,
            denied_concepts=denied_concepts,
            profile_json=profile.profile_json,
        )

    # ADR-048 §5 (amends ADR-035): re-source the match score from the ledger's
    # fit-weighted slice — the single source of truth — not a parallel
    # classification list. The formula and weights are unchanged. ADR-089
    # clause 5: headline, breakdown, categories, critical/minor gaps AND the
    # persisted ledger all come from this ONE ledger, so they cannot disagree.
    scored = compute_match_score_from_ledger(keyword_ledger)

    # Compute embedding similarity score (None when noop provider or embeddings absent)
    embedding_similarity_score = _compute_embedding_similarity(
        job.embedding,
        profile.embedding,
    )

    record = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        match_score=scored["match_score"],
        input_fingerprint=fingerprint,
        embedding_similarity_score=embedding_similarity_score,
        critical_gaps=scored["critical_gaps"],
        minor_gaps=scored["minor_gaps"],
        # E-4 / SF-GAP.10 — the ledger has the last word on every published
        # list, `strengths` included (see `_strengths_the_ledger_supports`).
        strengths=_strengths_the_ledger_supports(
            data.get("strengths", []), keyword_ledger
        ),
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
    #
    # ADR-089 clause 4 — stable gap identity: with a previous row of the same
    # JD, its clusters (ids, labels, members, outcome) are carried forward and
    # only concepts no carried cluster holds are clustered; a first analysis or
    # a JD change clusters from scratch. Either way every cluster leaves here
    # with its per-gap record (clause 3).
    if same_jd:
        record.gap_clusters = await _carry_forward_clusters(
            record, previous, job, provider, db, denied_concepts=denied_concepts
        )
    else:
        await cluster_gaps(record, job, provider, db)
        record.gap_clusters = [
            initialise_cluster_record(c, keyword_ledger, denied_concepts)
            for c in record.gap_clusters or []
        ]

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
