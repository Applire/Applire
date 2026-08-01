"""Unit tests for gap clustering schema and service."""
import json as _json
from pathlib import Path as _Path
from unittest.mock import AsyncMock, MagicMock
from unittest.mock import AsyncMock as _AsyncMock, MagicMock as _MagicMock, patch as _patch

import pytest

from applire.schemas.gap_cluster import GapClusterSchema


def test_gap_cluster_schema_validates():
    raw = {
        "id": "cluster-agentic",
        "label": "Agentic AI Systems",
        "category": "C",
        "gaps": ["Agentic Systems", "AI Systems"],
        "jd_skills": ["LLM-based Agent Design"],
        "jd_context": "Die Stelle sucht jemanden, der autonome KI-Agenten designt.",
    }
    cluster = GapClusterSchema.model_validate(raw)
    assert cluster.id == "cluster-agentic"
    assert cluster.category == "C"
    assert len(cluster.gaps) == 2


def test_build_clustering_prompt_includes_gaps():
    from applire.prompts.gap_clustering import build_clustering_prompt
    prompt = build_clustering_prompt(
        category_b=["Python basics", "Git"],
        category_c=["LLMs", "Agentic Systems", "AI Systems"],
        required_skills=["LLM-based Agent Design", "Python"],
        nice_to_have_skills=["Multi-Agent Orchestration"],
    )
    assert "LLMs" in prompt
    assert "Agentic Systems" in prompt
    assert "LLM-based Agent Design" in prompt
    assert "Python basics" in prompt


def test_clustering_system_prompt_exists():
    from applire.prompts.gap_clustering import CLUSTERING_SYSTEM_PROMPT
    assert "cluster" in CLUSTERING_SYSTEM_PROMPT.lower()
    assert "JSON" in CLUSTERING_SYSTEM_PROMPT


def test_build_clustering_prompt_localizes_jd_context_to_ui_language():
    """#3 (ADR-038): the gaps page is conversational — cluster descriptions follow the
    UI language, not the JD language. The prompt must carry an explicit output-language
    directive."""
    from applire.prompts.gap_clustering import build_clustering_prompt
    common = dict(category_b=[], category_c=["X"], required_skills=[], nice_to_have_skills=[])
    assert "ENGLISH" in build_clustering_prompt(**common, lang="en")
    assert "GERMAN" in build_clustering_prompt(**common, lang="de")


@pytest.mark.asyncio
async def test_cluster_gaps_persists_clusters():
    """cluster_gaps() calls LLM and saves result to gap_analysis.gap_clusters."""
    from applire.services.gap import cluster_gaps
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis

    gap_analysis = MagicMock(spec=GapAnalysis)
    gap_analysis.category_b = ["Python basics"]
    gap_analysis.category_c = ["LLMs", "Agentic Systems"]

    job = MagicMock(spec=JobAnalysis)
    job.required_skills = ["LLM-based Agent Design"]
    job.nice_to_have_skills = ["Multi-Agent Orchestration"]

    clusters_raw = [
        {
            "id": "cluster-agentic",
            "label": "Agentic AI Systems",
            "category": "C",
            "gaps": ["LLMs", "Agentic Systems"],
            "jd_skills": ["LLM-based Agent Design"],
            "jd_context": "The role requires designing autonomous AI agents.",
        },
        {
            "id": "cluster-python",
            "label": "Python Fundamentals",
            "category": "B",
            "gaps": ["Python basics"],
            "jd_skills": [],
            "jd_context": "Python is used throughout the stack.",
        },
    ]

    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value=clusters_raw)

    db = MagicMock()
    db.commit = AsyncMock()
    # Standalone re-cluster path: the record is already session-managed, so
    # cluster_gaps persists. (In _run_analysis the record is NOT yet in the
    # session — clusters land in the record's single publishing commit.)
    db.__contains__ = MagicMock(return_value=True)

    # cluster_gaps now resolves the UI language for jd_context (#3, ADR-038).
    from unittest.mock import patch
    with patch("applire.services.session.get_conversation_language", new=AsyncMock(return_value="de")):
        await cluster_gaps(gap_analysis, job, provider, db)

    assert gap_analysis.gap_clusters == clusters_raw
    db.commit.assert_awaited_once()


def test_gap_detector_empty_clusters():
    from applire.services.interview_graph import gap_detector
    from unittest.mock import MagicMock
    from applire.models.gap import GapAnalysis

    ga = MagicMock(spec=GapAnalysis)
    ga.gap_clusters = []
    ids, cats, by_id = gap_detector(ga)
    assert ids == []
    assert cats == {}
    assert by_id == {}


def test_gap_detector_c_before_b():
    from applire.services.interview_graph import gap_detector
    from unittest.mock import MagicMock
    from applire.models.gap import GapAnalysis

    ga = MagicMock(spec=GapAnalysis)
    ga.gap_clusters = [
        {"id": "cluster-b", "label": "B Cluster", "category": "B", "gaps": ["b1"], "jd_skills": [], "jd_context": ""},
        {"id": "cluster-c", "label": "C Cluster", "category": "C", "gaps": ["c1"], "jd_skills": [], "jd_context": ""},
    ]
    ids, cats, by_id = gap_detector(ga)
    assert ids[0] == "cluster-c"
    assert ids[1] == "cluster-b"


def test_gap_detector_without_profile_preserves_legacy_order():
    """Backward compatibility: no `profile` arg → no reordering at all (every
    pre-#259 caller/test keeps its exact existing behaviour)."""
    from applire.services.interview_graph import gap_detector
    from unittest.mock import MagicMock
    from applire.models.gap import GapAnalysis

    ga = MagicMock(spec=GapAnalysis)
    ga.gap_clusters = [
        {"id": "cluster-breadth", "label": "Breadth", "category": "C", "gaps": ["Rust"], "jd_skills": [], "jd_context": ""},
        {"id": "cluster-required", "label": "Required", "category": "C", "gaps": ["CI/CD"], "jd_skills": [], "jd_context": ""},
    ]
    ga.keyword_ledger = [
        {"concept": "CI/CD", "sources": ["required"], "status": "gap"},
        {"concept": "Rust", "sources": ["nice_to_have"], "status": "gap"},
    ]
    ids, _, _ = gap_detector(ga)
    assert ids == ["cluster-breadth", "cluster-required"]


def test_gap_detector_promotes_jd_required_keyword_only_concept_within_category():
    """#259 ordering guardrail: a JD-hard-requirement concept that is
    keyword-only in the vault is asked BEFORE a nice-to-have cluster question
    — even though both clusters sit in the same category (breadth is listed
    FIRST in the raw clustering output, so this proves reordering, not luck)."""
    from applire.services.interview_graph import gap_detector
    from unittest.mock import MagicMock
    from applire.models.gap import GapAnalysis

    ga = MagicMock(spec=GapAnalysis)
    ga.gap_clusters = [
        {"id": "cluster-breadth", "label": "Nice-to-have breadth", "category": "C", "gaps": ["Rust"], "jd_skills": [], "jd_context": ""},
        {"id": "cluster-required", "label": "CI/CD (required)", "category": "C", "gaps": ["CI/CD"], "jd_skills": [], "jd_context": ""},
    ]
    ga.keyword_ledger = [
        {"concept": "CI/CD", "sources": ["required"], "status": "gap"},
        {"concept": "Rust", "sources": ["nice_to_have"], "status": "gap"},
    ]
    profile = {"work_experience": []}
    ids, _, _ = gap_detector(ga, profile=profile)
    assert ids[0] == "cluster-required"
    assert ids[1] == "cluster-breadth"


def test_gap_detector_priority_does_not_cross_category_boundary():
    """A JD-required keyword-only concept in a category-B cluster still asks
    AFTER every category-C cluster — the existing C-before-B ordering (a
    coarser, already-load-bearing value signal) is not inverted; priority
    only breaks ties WITHIN a bucket."""
    from applire.services.interview_graph import gap_detector
    from unittest.mock import MagicMock
    from applire.models.gap import GapAnalysis

    ga = MagicMock(spec=GapAnalysis)
    ga.gap_clusters = [
        {"id": "cluster-b-required", "label": "B required", "category": "B", "gaps": ["CI/CD"], "jd_skills": [], "jd_context": ""},
        {"id": "cluster-c-breadth", "label": "C breadth", "category": "C", "gaps": ["Rust"], "jd_skills": [], "jd_context": ""},
    ]
    ga.keyword_ledger = [
        {"concept": "CI/CD", "sources": ["required"], "status": "gap"},
        {"concept": "Rust", "sources": ["nice_to_have"], "status": "gap"},
    ]
    profile = {"work_experience": []}
    ids, _, _ = gap_detector(ga, profile=profile)
    assert ids[0] == "cluster-c-breadth"
    assert ids[1] == "cluster-b-required"


# ---------------------------------------------------------------------------
# #273/#274/#284 (PO reframing 2026-07-26) — filter_answered_concepts
# ---------------------------------------------------------------------------
# gap_analysis.gap_clusters is a clustering-LLM SNAPSHOT that is never
# recomputed when the ledger is later upgraded. Run-6 ground truth:
# cluster-technical-leadership's five concepts were ALL already status ==
# "direct" in the SAME GapAnalysis row the interview loaded — the snapshot
# alone was stale, and three questions were still burned drilling it.


def _fac_cluster(cid, label, category, gaps):
    return {
        "id": cid, "label": label, "category": category, "gaps": gaps,
        "jd_skills": gaps, "jd_context": f"context for {label}",
    }


def test_filter_answered_concepts_drops_cluster_whose_concepts_are_all_direct():
    """The run-6 shape: every concept in a cluster is already status=='direct'
    in the ledger (evidence arrived via testimony, an earlier session, or the
    reevaluation pass run at session start) — the whole cluster is dropped,
    never re-asked."""
    from applire.services.interview_graph import filter_answered_concepts

    cluster_ids = ["cluster-leadership", "cluster-ai-core"]
    cluster_categories = {"cluster-leadership": "C", "cluster-ai-core": "C"}
    clusters_by_id = {
        "cluster-leadership": _fac_cluster(
            "cluster-leadership", "Technical Leadership", "C",
            ["Team management", "Mentoring"],
        ),
        "cluster-ai-core": _fac_cluster(
            "cluster-ai-core", "AI Core Systems", "C", ["Embeddings"]
        ),
    }
    ledger = [
        {"concept": "Team management", "status": "direct", "claimable": True},
        {"concept": "Mentoring", "status": "direct", "claimable": True},
        {"concept": "Embeddings", "status": "gap", "claimable": False},
    ]

    ids, cats, by_id = filter_answered_concepts(
        cluster_ids, cluster_categories, clusters_by_id, ledger
    )

    assert ids == ["cluster-ai-core"]
    assert "cluster-leadership" not in by_id
    assert "cluster-leadership" not in cats
    assert by_id["cluster-ai-core"]["gaps"] == ["Embeddings"]


def test_filter_answered_concepts_narrows_a_mixed_cluster():
    """A cluster with SOME concepts already 'direct' keeps only its still-open
    ones — the cluster survives (still askable), label/category/jd_context
    unchanged, just a narrower `gaps` list."""
    from applire.services.interview_graph import filter_answered_concepts

    cluster_ids = ["cluster-leadership"]
    cluster_categories = {"cluster-leadership": "C"}
    clusters_by_id = {
        "cluster-leadership": _fac_cluster(
            "cluster-leadership", "Technical Leadership", "C",
            ["Team management", "Engineering standards"],
        ),
    }
    ledger = [
        {"concept": "Team management", "status": "direct", "claimable": True},
        {"concept": "Engineering standards", "status": "gap", "claimable": False},
    ]

    ids, cats, by_id = filter_answered_concepts(
        cluster_ids, cluster_categories, clusters_by_id, ledger
    )

    assert ids == ["cluster-leadership"]
    assert cats == {"cluster-leadership": "C"}
    assert by_id["cluster-leadership"]["gaps"] == ["Engineering standards"]
    # Everything else on the cluster dict is untouched.
    assert by_id["cluster-leadership"]["label"] == "Technical Leadership"
    assert by_id["cluster-leadership"]["jd_context"] == "context for Technical Leadership"


def test_filter_answered_concepts_leaves_partial_clusters_askable():
    """'partial' concepts stay exactly as askable as today — asking may firm
    a partial into a direct; that is legitimate interview work, not the bug
    being fixed."""
    from applire.services.interview_graph import filter_answered_concepts

    cluster_ids = ["cluster-observability"]
    cluster_categories = {"cluster-observability": "B"}
    clusters_by_id = {
        "cluster-observability": _fac_cluster(
            "cluster-observability", "Observability", "B", ["Observability"]
        ),
    }
    ledger = [{"concept": "Observability", "status": "partial", "claimable": True}]

    ids, cats, by_id = filter_answered_concepts(
        cluster_ids, cluster_categories, clusters_by_id, ledger
    )

    assert ids == ["cluster-observability"]
    assert by_id["cluster-observability"]["gaps"] == ["Observability"]


def test_filter_answered_concepts_concept_absent_from_ledger_fails_open():
    """A concept with NO matching ledger entry is never dropped on absence of
    information — fail-open, not fail-closed."""
    from applire.services.interview_graph import filter_answered_concepts

    cluster_ids = ["cluster-x"]
    cluster_categories = {"cluster-x": "C"}
    clusters_by_id = {
        "cluster-x": _fac_cluster("cluster-x", "X", "C", ["Some Untracked Concept"]),
    }
    ledger = [{"concept": "Unrelated concept", "status": "direct", "claimable": True}]

    ids, _, by_id = filter_answered_concepts(
        cluster_ids, cluster_categories, clusters_by_id, ledger
    )

    assert ids == ["cluster-x"]
    assert by_id["cluster-x"]["gaps"] == ["Some Untracked Concept"]


def test_filter_answered_concepts_never_drops_a_concept_the_ledger_calls_a_gap():
    """A narrower concept that carries its OWN ``status: "gap"`` entry must
    survive, even when a BROADER concept is ``direct``.

    Pinned from mock-stack PQ ground truth (2026-07-26): the ledger held
    ``Python -> direct`` AND ``5+ years Python experience -> gap``. Matching
    the gap string against the direct set with the bidirectional-substring
    ``_matches`` dropped the whole ``cluster-python-experience`` cluster — the
    interview silently skipped a concept the ledger itself still called open.
    Presence of a broader token never satisfies a depth/duration requirement
    (#207 over-fire family). A non-direct match VETOES the drop.
    """
    from applire.services.interview_graph import filter_answered_concepts

    cluster_ids = ["cluster-python-experience"]
    cluster_categories = {"cluster-python-experience": "C"}
    clusters_by_id = {
        "cluster-python-experience": _fac_cluster(
            "cluster-python-experience",
            "Python Experience Depth",
            "C",
            ["5+ years Python experience"],
        ),
    }
    ledger = [
        {"concept": "Python", "status": "direct", "claimable": True},
        {"concept": "5+ years Python experience", "status": "gap", "claimable": False},
    ]

    ids, _, by_id = filter_answered_concepts(
        cluster_ids, cluster_categories, clusters_by_id, ledger
    )

    assert ids == ["cluster-python-experience"]
    assert by_id["cluster-python-experience"]["gaps"] == ["5+ years Python experience"]


def test_filter_answered_concepts_partial_own_entry_vetoes_broader_direct():
    """Same veto for ``partial`` — asking may still firm it into ``direct``."""
    from applire.services.interview_graph import filter_answered_concepts

    cluster_ids = ["cluster-k8s"]
    cluster_categories = {"cluster-k8s": "B"}
    clusters_by_id = {
        "cluster-k8s": _fac_cluster(
            "cluster-k8s", "Cloud", "B", ["Kubernetes at production scale"]
        ),
    }
    ledger = [
        {"concept": "Kubernetes", "status": "direct", "claimable": True},
        {
            "concept": "Kubernetes at production scale",
            "status": "partial",
            "claimable": False,
        },
    ]

    ids, _, by_id = filter_answered_concepts(
        cluster_ids, cluster_categories, clusters_by_id, ledger
    )

    assert ids == ["cluster-k8s"]
    assert by_id["cluster-k8s"]["gaps"] == ["Kubernetes at production scale"]


def test_filter_answered_concepts_tolerates_none_and_empty_ledger():
    from applire.services.interview_graph import filter_answered_concepts

    cluster_ids = ["cluster-x"]
    cluster_categories = {"cluster-x": "C"}
    clusters_by_id = {"cluster-x": _fac_cluster("cluster-x", "X", "C", ["Concept"])}

    ids, cats, by_id = filter_answered_concepts(
        cluster_ids, cluster_categories, clusters_by_id, None
    )
    assert (ids, cats, by_id) == (cluster_ids, cluster_categories, clusters_by_id)

    ids, cats, by_id = filter_answered_concepts(
        cluster_ids, cluster_categories, clusters_by_id, []
    )
    assert (ids, cats, by_id) == (cluster_ids, cluster_categories, clusters_by_id)


def test_filter_answered_concepts_makes_no_llm_call():
    """Deterministic: no provider/LLM argument on the signature."""
    import inspect

    from applire.services.interview_graph import filter_answered_concepts

    sig = inspect.signature(filter_answered_concepts)
    assert list(sig.parameters) == [
        "cluster_ids", "cluster_categories", "clusters_by_id", "keyword_ledger",
    ]


# ---------------------------------------------------------------------------
# #166: clustering payload must survive JSON-object mode (every real provider
# forces a top-level object; a compliant model can NEVER emit a bare array).
# Before the fix, gap.py demanded a bare list and silently produced [] for the
# object envelope, which downstream turned into a false "strong match".
# ---------------------------------------------------------------------------

_VALID_CLUSTER = {
    "id": "cluster-agentic",
    "label": "Agentic AI Systems",
    "category": "C",
    "gaps": ["LLMs", "Agentic Systems"],
    "jd_skills": ["LLM-based Agent Design"],
    "jd_context": "The role requires designing autonomous AI agents.",
}
_VALID_CLUSTER_B = {
    "id": "cluster-python",
    "label": "Python Fundamentals",
    "category": "B",
    "gaps": ["Python basics"],
    "jd_skills": [],
    "jd_context": "Python is used throughout the stack.",
}


def test_unwrap_clusters_object_envelope():
    """`{"clusters": [...]}` — the shape the fixed prompt now demands."""
    from applire.services.gap import _unwrap_clusters
    out = _unwrap_clusters({"clusters": [_VALID_CLUSTER, _VALID_CLUSTER_B]})
    assert out == [_VALID_CLUSTER, _VALID_CLUSTER_B]


def test_unwrap_clusters_single_list_valued_key():
    """A model that names the envelope key differently still unwraps."""
    from applire.services.gap import _unwrap_clusters
    out = _unwrap_clusters({"result": [_VALID_CLUSTER]})
    assert out == [_VALID_CLUSTER]


def test_unwrap_clusters_bare_list_tolerated():
    """Lenient providers / legacy mock: a bare top-level array still works."""
    from applire.services.gap import _unwrap_clusters
    out = _unwrap_clusters([_VALID_CLUSTER])
    assert out == [_VALID_CLUSTER]


def test_unwrap_clusters_single_cluster_object_wrapped():
    """A bare single cluster object (a real Requesty shape) wraps to a 1-list."""
    from applire.services.gap import _unwrap_clusters
    out = _unwrap_clusters(dict(_VALID_CLUSTER))
    assert out == [_VALID_CLUSTER]


def test_unwrap_clusters_unknown_shape_empty():
    from applire.services.gap import _unwrap_clusters
    assert _unwrap_clusters({"unexpected": {"nested": 1}}) == []
    assert _unwrap_clusters(None) == []
    assert _unwrap_clusters("nope") == []


def _cluster_gaps_provider(return_value):
    provider = _MagicMock()
    provider.aparse_json = _AsyncMock(return_value=return_value)
    return provider


def _cluster_gaps_ga(category_c=None, category_b=None, keyword_ledger=None):
    from applire.models.gap import GapAnalysis
    ga = _MagicMock(spec=GapAnalysis)
    ga.category_b = category_b if category_b is not None else []
    ga.category_c = category_c if category_c is not None else ["LLMs", "Agentic Systems"]
    ga.keyword_ledger = keyword_ledger
    return ga


def _honest_keyword_entry(concept, claimable=False, fit_weight=0):
    """A keyword-only honest-gap ledger entry (US204, ADR-048 §10): not
    claimable AND no fit weight, so it never reaches category_c on its own."""
    return {"concept": concept, "claimable": claimable, "fit_weight": fit_weight}


def _cluster_gaps_job():
    from applire.models.job import JobAnalysis
    job = _MagicMock(spec=JobAnalysis)
    job.required_skills = ["LLM-based Agent Design"]
    job.nice_to_have_skills = []
    return job


async def _run_cluster_gaps(raw_return, category_c=None, category_b=None):
    from applire.services.gap import cluster_gaps
    ga = _cluster_gaps_ga(category_c=category_c, category_b=category_b)
    job = _cluster_gaps_job()
    provider = _cluster_gaps_provider(raw_return)
    db = _MagicMock()
    db.commit = _AsyncMock()
    db.__contains__ = _MagicMock(return_value=True)
    with _patch("applire.services.session.get_conversation_language", new=_AsyncMock(return_value="en")):
        await cluster_gaps(ga, job, provider, db)
    return ga


@pytest.mark.asyncio
async def test_cluster_gaps_object_envelope_populates_clusters():
    """The regression: an object envelope must NOT collapse to []."""
    ga = await _run_cluster_gaps({"clusters": [_VALID_CLUSTER, _VALID_CLUSTER_B]})
    assert [c["id"] for c in ga.gap_clusters] == ["cluster-agentic", "cluster-python"]


@pytest.mark.asyncio
async def test_cluster_gaps_malformed_item_dropped_others_survive(caplog):
    import logging
    with caplog.at_level(logging.DEBUG, logger="applire.services.gap"):
        ga = await _run_cluster_gaps({"clusters": [_VALID_CLUSTER, {"garbage": True}]})
    assert [c["id"] for c in ga.gap_clusters] == ["cluster-agentic"]
    assert any("dropped" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_cluster_gaps_zero_clusters_warns_when_input_nonempty(caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger="applire.services.gap"):
        ga = await _run_cluster_gaps({"clusters": []}, category_c=["LLMs"])
    assert ga.gap_clusters == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.asyncio
async def test_cluster_gaps_real_requesty_responses(caplog):
    """Verbatim REAL Requesty payloads captured 2026-07-15 (LLM interaction log):
      - 13:21:20Z  a single bare cluster object
      - 14:10:25Z  a {"clusters": [...5 valid clusters...]} envelope
    Source: scratchpad/clustering-fixtures.json → tests/unit/fixtures/clustering_real_responses.json.
    Both MUST populate gap_clusters; before the #166 fix both collapsed to []."""
    fixtures = _json.loads(
        (_Path(__file__).parent / "fixtures" / "clustering_real_responses.json").read_text()
    )
    bare_single = next(f for f in fixtures if f["ts"].startswith("2026-07-15T13:21:20"))
    envelope = next(f for f in fixtures if f["ts"].startswith("2026-07-15T14:10:25"))

    ga1 = await _run_cluster_gaps(bare_single["response"])
    assert len(ga1.gap_clusters) == 1
    assert ga1.gap_clusters[0]["id"] == "cluster-cloud-devops"

    ga2 = await _run_cluster_gaps(envelope["response"])
    assert len(ga2.gap_clusters) == 8
    assert ga2.gap_clusters[0]["id"] == "cluster-javascript-ecosystem"


# ---------------------------------------------------------------------------
# #166 Important-1: cluster_gaps() clusters on category_c PLUS keyword-only
# honest gaps, but the session-side honest-fallback guard used to key on raw
# category_c alone — a caller could diverge from what was actually clustered
# on. askable_gap_inputs()/has_clustering_input() are the single shared
# predicate that both cluster_gaps() and the session guard now use.
# ---------------------------------------------------------------------------


def test_askable_gap_inputs_augments_with_keyword_only_honest_gaps():
    """Persisted category_c=[] but a keyword-only honest gap exists in the
    ledger → it must still show up as askable input."""
    from applire.services.gap import askable_gap_inputs
    ga = _cluster_gaps_ga(category_c=[], keyword_ledger=[_honest_keyword_entry("Kubernetes")])
    assert askable_gap_inputs(ga) == ["Kubernetes"]


def test_askable_gap_inputs_dedupes_against_category_c():
    from applire.services.gap import askable_gap_inputs
    ga = _cluster_gaps_ga(
        category_c=["Kubernetes"],
        keyword_ledger=[_honest_keyword_entry("kubernetes")],  # different casing
    )
    assert askable_gap_inputs(ga) == ["Kubernetes"]


def test_askable_gap_inputs_empty_when_nothing_present():
    from applire.services.gap import askable_gap_inputs
    ga = _cluster_gaps_ga(category_c=[], keyword_ledger=[])
    assert askable_gap_inputs(ga) == []


def test_has_clustering_input_true_for_keyword_only_honest_gaps_with_empty_category_c():
    """The #166 Important-1 regression: persisted category_c=[] and category_b=[]
    but keyword-only honest gaps are non-empty → has_clustering_input() must be
    True, matching what cluster_gaps() actually clusters on."""
    from applire.services.gap import has_clustering_input
    ga = _cluster_gaps_ga(category_c=[], category_b=[], keyword_ledger=[_honest_keyword_entry("Kubernetes")])
    assert has_clustering_input(ga) is True


def test_has_clustering_input_false_when_everything_empty():
    from applire.services.gap import has_clustering_input
    ga = _cluster_gaps_ga(category_c=[], category_b=[], keyword_ledger=[])
    assert has_clustering_input(ga) is False


def test_has_clustering_input_true_for_category_b_alone():
    from applire.services.gap import has_clustering_input
    ga = _cluster_gaps_ga(category_c=[], category_b=["Git basics"], keyword_ledger=[])
    assert has_clustering_input(ga) is True


@pytest.mark.asyncio
async def test_cluster_gaps_uses_keyword_only_honest_gaps_as_clustering_input():
    """cluster_gaps() must still feed keyword-only honest gaps into the
    clustering prompt even when persisted category_c is empty (regression
    guard for the askable_gap_inputs() extraction)."""
    from applire.services.gap import cluster_gaps
    ga = _cluster_gaps_ga(category_c=[], category_b=[], keyword_ledger=[_honest_keyword_entry("Kubernetes")])
    job = _cluster_gaps_job()
    provider = _cluster_gaps_provider({"clusters": []})
    db = _MagicMock()
    db.commit = _AsyncMock()
    db.__contains__ = _MagicMock(return_value=True)
    with _patch("applire.services.session.get_conversation_language", new=_AsyncMock(return_value="en")):
        await cluster_gaps(ga, job, provider, db)
    prompt_arg = provider.aparse_json.call_args.args[0]
    assert "Kubernetes" in prompt_arg


# ---------------------------------------------------------------------------
# #260 — askable_gap_inputs() ALSO augments with keyword-LIABILITY concepts
# (required + claimable + no narrative anywhere): exit (a) of the
# pre-generation liability check is "elicit the story via the existing
# resolve_gap micro-session machinery" — routing through the SAME
# augmentation seam US204 established for keyword-only honest gaps, so a
# liability concept (which normally lives in category_a, never askable)
# becomes clusterable and therefore resolve_gap-reachable.
# ---------------------------------------------------------------------------


def _liability_keyword_entry(concept):
    """A required, claimable, narrative-less ledger entry (#260) — status
    `direct`/category_a in compute_match_score terms, so it would never reach
    category_c on its own, exactly like the keyword-only honest-gap case."""
    return {
        "concept": concept,
        "claimable": True,
        "fit_weight": 1.0,
        "sources": ["required"],
        "status": "direct",
        "narrative_backed": False,
    }


def test_askable_gap_inputs_augments_with_keyword_liabilities():
    from applire.services.gap import askable_gap_inputs
    ga = _cluster_gaps_ga(category_c=[], keyword_ledger=[_liability_keyword_entry("RAG")])
    assert askable_gap_inputs(ga) == ["RAG"]


def test_askable_gap_inputs_dedupes_liability_against_category_c():
    from applire.services.gap import askable_gap_inputs
    ga = _cluster_gaps_ga(
        category_c=["RAG"],
        keyword_ledger=[_liability_keyword_entry("rag")],  # different casing
    )
    assert askable_gap_inputs(ga) == ["RAG"]


def test_askable_gap_inputs_dedupes_liability_against_keyword_only_honest_gap():
    """A concept cannot double-count if it somehow satisfied both
    augmentation predicates (defensive; the two are normally disjoint since
    a liability concept is claimable and a keyword-only honest gap is not)."""
    from applire.services.gap import askable_gap_inputs
    ga = _cluster_gaps_ga(
        category_c=[],
        keyword_ledger=[_honest_keyword_entry("Kubernetes"), _liability_keyword_entry("RAG")],
    )
    assert askable_gap_inputs(ga) == ["Kubernetes", "RAG"]


def test_askable_gap_inputs_narrative_backed_liability_entry_never_augments():
    """A backed entry (narrative_backed True, or missing status not
    required/claimable) is not a liability and stays out of askable input."""
    from applire.services.gap import askable_gap_inputs
    backed = _liability_keyword_entry("RAG")
    backed["narrative_backed"] = True
    ga = _cluster_gaps_ga(category_c=[], keyword_ledger=[backed])
    assert askable_gap_inputs(ga) == []


@pytest.mark.asyncio
async def test_cluster_gaps_uses_keyword_liabilities_as_clustering_input():
    from applire.services.gap import cluster_gaps
    ga = _cluster_gaps_ga(category_c=[], category_b=[], keyword_ledger=[_liability_keyword_entry("RAG")])
    job = _cluster_gaps_job()
    provider = _cluster_gaps_provider({"clusters": []})
    db = _MagicMock()
    db.commit = _AsyncMock()
    db.__contains__ = _MagicMock(return_value=True)
    with _patch("applire.services.session.get_conversation_language", new=_AsyncMock(return_value="en")):
        await cluster_gaps(ga, job, provider, db)
    prompt_arg = provider.aparse_json.call_args.args[0]
    assert "RAG" in prompt_arg
