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
