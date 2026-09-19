# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
#675 line 60 — a gap cluster's B/C category is derived from its members.

The clustering prompt used to state the rule the model was expected to follow:
``category = "C" if any constituent gap is Category C, else "B"``. It is not a
judgement — which input list a member came from is a fact the caller holds — and
the model broke it. Measured over every captured clustering record (63 records,
198 clusters, ``logs/llm/`` + ``backend/logs/llm/``): 53 clusters carried a
category contradicting their own members, all 53 understating severity. The edge
run of 2026-09-18 showed the other direction, 3 of 9 clusters declared ``C`` with
no Category C member, one of them admitting a ``category_a`` strength as a gap.

Prompt v2 stops soliciting the field; ``_reconcile_cluster_categories`` derives it
and refuses a member the same analysis publishes as a strength.

Mutation kills (verified 2026-09-18 on a scratchpad copy of ``services/gap.py``):
  * ``derived = cluster.get("category")`` instead of deriving  -> 7 tests, led by
        test_a_cluster_with_a_category_c_member_is_C and
        test_a_cluster_of_category_b_members_only_is_B
  * drop the ``strengths`` filter  -> 3 tests, led by
        test_a_category_a_strength_is_not_admitted_as_a_gap
  * ``strengths = {...}`` without ``- submitted``  -> 1 test:
        test_a_keyword_liability_folded_into_the_input_survives
  * drop the empty-cluster disposition  -> 2 tests, led by
        test_a_cluster_of_nothing_but_strengths_is_dropped
  * call the reconciler but discard its result  -> 1 test:
        test_cluster_gaps_persists_the_derived_category (the seam)
"""
import pytest

from applire.services.gap import _reconcile_cluster_categories


def _cluster(cid: str, gaps: list[str], category: str = "B") -> dict:
    return {
        "id": cid,
        "label": cid.replace("cluster-", "").replace("-", " ").title(),
        "category": category,
        "gaps": gaps,
        "jd_skills": [],
        "jd_context": "Für diese Rolle brauche ich das.",
    }


# ---------------------------------------------------------------------------
# The derivation
# ---------------------------------------------------------------------------


def test_a_cluster_with_a_category_c_member_is_C():
    """The direction the captured corpus shows 53 times: declared B, member C."""
    out = _reconcile_cluster_categories(
        [_cluster("cluster-datenplattform", ["Kubernetes", "Containerisierung"], category="B")],
        category_c=["Kubernetes"],
        category_b=["Containerisierung"],
        category_a=[],
    )
    assert [c["category"] for c in out] == ["C"]
    assert out[0]["gaps"] == ["Kubernetes", "Containerisierung"]


def test_a_cluster_of_category_b_members_only_is_B():
    """The direction the edge run showed 3 of 9 times: declared C, all members B."""
    out = _reconcile_cluster_categories(
        [_cluster("cluster-ki-strategie", ["KI-Strategie", "Technologiebewertung"], category="C")],
        category_c=["Betriebsrat"],
        category_b=["KI-Strategie", "Technologiebewertung"],
        category_a=[],
    )
    assert [c["category"] for c in out] == ["B"]


def test_the_derivation_ignores_case_and_surrounding_space():
    out = _reconcile_cluster_categories(
        [_cluster("cluster-sap", ["  sap mm  "], category="B")],
        category_c=["SAP MM"],
        category_b=[],
        category_a=[],
    )
    assert out[0]["category"] == "C"


def test_a_paraphrased_member_is_kept_and_does_not_decide_the_category():
    """49 captured clusters named "microservices architecture" for an input
    "microservices". A paraphrase is not a fact against the member: dropping it
    would delete a real gap the model merely reworded, so it is kept — and it
    decides nothing, because it matches no input list."""
    out = _reconcile_cluster_categories(
        [_cluster("cluster-architektur", ["Microservices-Architektur", "GraphQL"], category="B")],
        category_c=["GraphQL"],
        category_b=["Microservices"],
        category_a=[],
    )
    assert out[0]["gaps"] == ["Microservices-Architektur", "GraphQL"]
    assert out[0]["category"] == "C"  # decided by GraphQL, not by the paraphrase


def test_every_other_cluster_field_passes_through_untouched():
    cluster = _cluster("cluster-fuehrung", ["Führungsspanne"], category="C")
    cluster["jd_skills"] = ["Teamleitung"]
    out = _reconcile_cluster_categories(
        [cluster], category_c=[], category_b=["Führungsspanne"], category_a=[]
    )
    assert out[0]["id"] == "cluster-fuehrung"
    assert out[0]["label"] == "Fuehrung"
    assert out[0]["jd_skills"] == ["Teamleitung"]
    assert out[0]["jd_context"] == "Für diese Rolle brauche ich das."


# ---------------------------------------------------------------------------
# Refusing a strength
# ---------------------------------------------------------------------------


def test_a_category_a_strength_is_not_admitted_as_a_gap():
    """The edge run's `cluster-beratung-und-operating-models` carried a
    category_a/direct concept beside two Category B gaps."""
    out = _reconcile_cluster_categories(
        [
            _cluster(
                "cluster-beratung",
                ["Geschäftsführungsberatung", "Operating Models", "Stakeholder-Management"],
                category="C",
            )
        ],
        category_c=[],
        category_b=["Geschäftsführungsberatung", "Operating Models"],
        category_a=["Stakeholder-Management"],
    )
    assert out[0]["gaps"] == ["Geschäftsführungsberatung", "Operating Models"]
    assert out[0]["category"] == "B"


def test_a_cluster_of_nothing_but_strengths_is_dropped():
    out = _reconcile_cluster_categories(
        [
            _cluster("cluster-staerken", ["Stakeholder-Management", "Moderation"], category="C"),
            _cluster("cluster-echt", ["Kubernetes"], category="B"),
        ],
        category_c=["Kubernetes"],
        category_b=[],
        category_a=["Stakeholder-Management", "Moderation"],
    )
    assert [c["id"] for c in out] == ["cluster-echt"]


def test_a_keyword_liability_folded_into_the_input_survives():
    """#260 deliberately folds a keyword LIABILITY — a concept that lives in
    ``category_a`` — into the clustering input so it becomes askable. It was
    submitted, so it is not the model admitting a strength, and it must survive.
    """
    out = _reconcile_cluster_categories(
        [_cluster("cluster-produktion", ["Produktion"], category="B")],
        category_c=["Produktion"],   # askable_gap_inputs folded it in
        category_b=[],
        category_a=["Produktion"],   # and the analysis also calls it a strength
        liabilities=["Produktion"],  # ... because it is the #260 liability slice
    )
    assert out[0]["gaps"] == ["Produktion"]
    # A liability is a claimable hard requirement WITHOUT a story — a strength to
    # narrate, not an absence — so it never makes a cluster "C" (delivery run
    # 2026-09-19: every cluster came out "C", two of them on liabilities alone).
    assert out[0]["category"] == "B"


def test_a_liability_beside_a_true_category_c_gap_still_yields_C():
    out = _reconcile_cluster_categories(
        [_cluster("cluster-sap", ["SAP PP", "SAP"], category="B")],
        category_c=["SAP PP", "SAP"],   # SAP is the folded liability, SAP PP a true gap
        category_b=[],
        category_a=["SAP"],
        liabilities=["SAP"],
    )
    assert out[0]["category"] == "C"


def test_a_cluster_of_liabilities_and_category_b_members_is_B():
    """The 2026-09-19 delivery-run shape: ``cluster-sprachen`` = Deutsch
    (category_a, folded as a liability) + Englisch (category_b, also a
    liability) came out "C" with no true Category C member."""
    out = _reconcile_cluster_categories(
        [_cluster("cluster-sprachen", ["Deutsch", "Englisch"], category="B")],
        category_c=["MES", "Deutsch", "Englisch"],
        category_b=["Englisch"],
        category_a=["Deutsch"],
        liabilities=["Deutsch", "Englisch"],
    )
    assert out[0]["gaps"] == ["Deutsch", "Englisch"]
    assert out[0]["category"] == "B"


# ---------------------------------------------------------------------------
# The schema no longer solicits the field
# ---------------------------------------------------------------------------


def test_the_schema_validates_a_response_without_a_category():
    from applire.schemas.gap_cluster import GapClusterSchema

    cluster = GapClusterSchema.model_validate(
        {
            "id": "cluster-kubernetes",
            "label": "Kubernetes",
            "gaps": ["Kubernetes"],
            "jd_skills": [],
            "jd_context": "Ich brauche Kubernetes für diese Rolle.",
        }
    )
    assert cluster.category == "B"  # placeholder; the derivation sets the real one


def test_the_prompt_no_longer_states_a_rule_the_code_now_owns():
    from applire.prompts.gap_clustering import CLUSTERING_SYSTEM_PROMPT

    assert '"category"' not in CLUSTERING_SYSTEM_PROMPT
    assert "if any constituent gap is Category C" not in CLUSTERING_SYSTEM_PROMPT
    # and it asks for verbatim gap strings instead (the paraphrase residue)
    assert "EXACTLY as it is written" in CLUSTERING_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# The seam: cluster_gaps persists the derived category
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cluster_gaps_persists_the_derived_category():
    """Drive the real service function with a stub provider, and read the
    PERSISTED snapshot — the shape the agent door, the gaps screen and
    ``interview_graph.plan_clusters`` all read."""
    from unittest.mock import AsyncMock, patch

    from applire.services import gap as gap_service
    from applire.services import session as session_service

    class _GapAnalysis:
        category_a = ["Stakeholder-Management"]
        category_b = ["KI-Strategie"]
        category_c = ["Kubernetes"]
        keyword_ledger = []
        gap_clusters = []

    class _Job:
        id = "job-1"
        required_skills = ["Kubernetes"]
        nice_to_have_skills = []

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(
        return_value={
            "clusters": [
                _cluster("cluster-plattform", ["Kubernetes"], category="B"),
                _cluster("cluster-strategie", ["KI-Strategie"], category="C"),
                _cluster("cluster-staerke", ["Stakeholder-Management"], category="C"),
            ]
        }
    )

    class _DB:
        def __contains__(self, _obj):
            return False

    record = _GapAnalysis()
    # `cluster_gaps` imports it locally to dodge the session<->gap import cycle,
    # so the patch has to land on the defining module.
    with patch.object(
        session_service, "get_conversation_language", AsyncMock(return_value="de")
    ):
        await gap_service.cluster_gaps(record, _Job(), provider, _DB())

    by_id = {c["id"]: c for c in record.gap_clusters}
    assert set(by_id) == {"cluster-plattform", "cluster-strategie"}
    assert by_id["cluster-plattform"]["category"] == "C"
    assert by_id["cluster-strategie"]["category"] == "B"
