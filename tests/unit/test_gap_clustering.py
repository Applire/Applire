"""Unit tests for gap clustering schema and service."""
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


import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest


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
    with patch("applire.services.session.get_ui_language", new=AsyncMock(return_value="de")):
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


# ---------------------------------------------------------------------------
# #166: clustering payload must survive JSON-object mode (every real provider
# forces a top-level object; a compliant model can NEVER emit a bare array).
# Before the fix, gap.py demanded a bare list and silently produced [] for the
# object envelope, which downstream turned into a false "strong match".
# ---------------------------------------------------------------------------

import json as _json
from pathlib import Path as _Path

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


import uuid as _uuid
from unittest.mock import AsyncMock as _AsyncMock, MagicMock as _MagicMock, patch as _patch


def _cluster_gaps_provider(return_value):
    provider = _MagicMock()
    provider.aparse_json = _AsyncMock(return_value=return_value)
    return provider


def _cluster_gaps_ga(category_c=None, category_b=None):
    from applire.models.gap import GapAnalysis
    ga = _MagicMock(spec=GapAnalysis)
    ga.category_b = category_b if category_b is not None else []
    ga.category_c = category_c if category_c is not None else ["LLMs", "Agentic Systems"]
    ga.keyword_ledger = None
    return ga


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
    with _patch("applire.services.session.get_ui_language", new=_AsyncMock(return_value="en")):
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
