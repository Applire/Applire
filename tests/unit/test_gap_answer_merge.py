# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-089 clauses 4 + 5 — the answer-driven score merge and stable gap identity.

Founder UAT 2026-09-23: re-entering the gaps view after one answer showed a
LOWER score (50 → 42), the answered gap red again and more cards than before.
Every completed answer re-classified every requirement and re-clustered from
scratch; the whole-slice clamp (ruling B-1) was disabled by any stochastic
regression anywhere.

The pure half pins each rule of the merge with the smallest ledger only that
rule decides; the integration half drives the REAL ``analyze_gaps`` with a
scripted classifier (the two LLM calls are the only thing stubbed) and reads
the persisted ``gap_analyses`` row.

Mutation map (each rule → the tests that kill its removal):

* outside-touched carry      — ``test_outside_touched_downward_move_is_replaced``,
  ``test_refresh_keeps_a_stochastic_flip_from_lowering_the_score``
* denial exception           — ``test_a_fresh_denial_always_stands``,
  ``test_refresh_after_a_denial_lowers_the_score``
* vault floor on the merge   — ``test_vault_floor_heals_a_carried_claim_with_no_backing``,
  ``test_refresh_heals_a_carried_claim_whose_vault_backing_is_gone``
* carry-forward orphan drop  — ``test_carry_forward_drops_an_orphaned_member``
* carried category re-derived — ``test_a_carried_cluster_category_is_re_derived_from_its_members``
"""

import copy
import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.user import User
from applire.prompts.gap_analysis import SYSTEM_PROMPT as GAP_SYSTEM_PROMPT
from applire.prompts.gap_clustering import CLUSTERING_SYSTEM_PROMPT
from applire.providers.llm.mock import MockLLMProvider
from applire.services.gap import (
    _apply_floors_to_merged,
    _jd_lists_unchanged,
    analyze_gaps,
    merge_ledger_per_requirement,
    pair_rows_by_requirement,
)
from applire.services.gap_coverage import AnswerScope, all_members

from tests.support.profile_factory import make_master_profile, set_profile_json

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000089")


# ===========================================================================
# Pure half — the merge rules
# ===========================================================================


def row(concept, status, *, forms=None, sources=("required",), evidence=None):
    claimable = status in ("direct", "partial")
    return {
        "concept": concept,
        "surface_forms": list(forms) if forms is not None else [concept],
        "sources": list(sources),
        "fit_weight": 1.0 if "required" in sources else (0.5 if sources else 0.0),
        "status": status,
        "evidence": (evidence or f"{concept} in the vault") if claimable else "",
        "claimable": claimable,
        "narrative_backed": True,
    }


JD = ["python", "docker", "kubernetes"]


def _merge(fresh, previous, *, members=(), jd=JD):
    return merge_ledger_per_requirement(
        fresh, previous, jd_terms=list(jd), touched_members=list(members)
    )


def test_pairs_by_the_owned_jd_term_across_a_reworded_concept():
    fresh = [row("Kubernetes (K8s)", "gap", forms=["Kubernetes", "K8s"])]
    previous = [row("Kubernetes", "direct")]
    assert pair_rows_by_requirement(fresh, previous, ["kubernetes"]) == [0]


def test_pairs_a_narrow_and_a_broad_requirement_each_with_its_own():
    jd = ["python", "5+ years python experience"]
    fresh = [row("5+ years Python experience", "gap"), row("Python", "direct")]
    previous = [row("Python", "direct"), row("5+ years Python experience", "partial")]
    assert pair_rows_by_requirement(fresh, previous, jd) == [1, 0]


def test_a_row_owning_no_jd_term_pairs_by_its_normalised_concept():
    fresh = [row("Team size ≥ 10", "gap", sources=())]
    previous = [row("team size ≥ 10", "partial", sources=())]
    assert pair_rows_by_requirement(fresh, previous, JD) == [0]


def test_a_new_requirement_has_no_counterpart():
    assert pair_rows_by_requirement([row("Rust", "gap")], [row("Python", "direct")], ["rust", "python"]) == [None]


def test_outside_touched_downward_move_is_replaced():
    fresh = [row("Python", "direct"), row("Docker", "partial")]
    previous = [row("Python", "direct"), row("Docker", "direct", evidence="ran the pipeline")]
    merged, carried = _merge(fresh, previous)
    assert carried == [1]
    assert merged[1]["status"] == "direct"
    assert merged[1]["evidence"] == "ran the pipeline", "the previous CLAIM stands in"


def test_direct_to_gap_outside_touched_is_replaced():
    merged, carried = _merge([row("Docker", "gap")], [row("Docker", "direct")])
    assert carried == [0] and merged[0]["status"] == "direct"


def test_the_carried_row_keeps_the_fresh_score_slot_and_unions_forms():
    fresh = [row("Docker", "gap", forms=["Docker", "Docker Compose"], sources=("nice_to_have",))]
    previous = [row("Docker", "direct", forms=["Docker", "Containers"])]
    merged, _ = _merge(fresh, previous)
    assert merged[0]["sources"] == ["nice_to_have"] and merged[0]["fit_weight"] == 0.5
    assert merged[0]["surface_forms"] == ["Docker", "Docker Compose", "Containers"]


def test_a_carried_row_keeps_the_fresh_concept_so_no_name_is_published_twice():
    """The classifier split one previous requirement into two fresh rows; both
    carry the previous claim, and each keeps its own name in this ledger."""
    jd = ["docker", "docker compose"]
    fresh = [row("Docker", "gap"), row("Docker Compose", "gap")]
    previous = [row("Docker", "direct", forms=["Docker", "Docker Compose"])]
    merged, carried = _merge(fresh, previous, jd=jd)
    assert carried == [0, 1]
    assert [r["concept"] for r in merged] == ["Docker", "Docker Compose"]
    assert [r["status"] for r in merged] == ["direct", "direct"]


def test_an_upward_move_stands():
    merged, carried = _merge([row("Docker", "direct")], [row("Docker", "partial")])
    assert carried == [] and merged[0]["status"] == "direct"


def test_a_fresh_denial_always_stands():
    """Ruling B-1's purpose, kept: a denial must be able to lower the score."""
    merged, carried = _merge([row("Docker", "denied")], [row("Docker", "direct")])
    assert carried == [] and merged[0]["status"] == "denied"


def test_a_worked_cluster_member_is_touched():
    merged, carried = _merge(
        [row("Kubernetes", "gap")], [row("Kubernetes", "partial")], members=["Kubernetes"]
    )
    assert carried == [] and merged[0]["status"] == "gap"


def test_a_mention_outside_the_worked_clusters_does_not_touch():
    """Ruling M-2 (adversarial pass E2, 2026-09-23): an observability answer
    that mentioned "the weekly design review" unlocked the unrelated, fully
    backed "Technical communication" (surface form "Design reviews") to
    classifier noise. Only a worked cluster's members are touched now; the
    requirement keeps its previous claim."""
    merged, carried = _merge(
        [row("Technical communication", "partial", forms=["Design reviews"])],
        [row("Technical communication", "direct", forms=["Design reviews"])],
        members=("Prometheus", "Grafana"),
    )
    assert carried == [0] and merged[0]["status"] == "direct"


def test_the_refresh_scope_touches_nothing():
    fresh = [row("Python", "gap"), row("Docker", "partial"), row("Kubernetes", "gap")]
    previous = [row("Python", "direct"), row("Docker", "direct"), row("Kubernetes", "partial")]
    merged, carried = _merge(fresh, previous)
    assert carried == [0, 1, 2]
    assert [r["status"] for r in merged] == ["direct", "direct", "partial"]


def test_a_malformed_previous_status_never_raises():
    merged, carried = _merge([row("Docker", "gap")], [{**row("Docker", "direct"), "status": ["direct"]}])
    assert carried == [] and merged[0]["status"] == "gap"


def test_no_previous_ledger_publishes_the_fresh_one():
    fresh = [row("Docker", "gap")]
    merged, carried = _merge(fresh, None)
    assert merged == fresh and carried == []


def test_merge_never_mutates_its_inputs():
    fresh = [row("Docker", "gap")]
    previous = [row("Docker", "direct")]
    f0, p0 = copy.deepcopy(fresh), copy.deepcopy(previous)
    _merge(fresh, previous)
    assert fresh == f0 and previous == p0


# The floors on the merged ledger -------------------------------------------


def _vault(*skills, bullets=()):
    return {
        "personal_info": {"first_name": "Max", "last_name": "Muster", "email": "max@example.org"},
        "work_experience": [
            {
                "id": "we-1",
                "company": "Acme",
                "role": "Engineer",
                "start_date": "2018-01",
                "responsibilities": list(bullets),
            }
        ],
        "skills": [{"name": s, "category": "technical", "proficiency": "advanced"} for s in skills],
        "education": [],
        "languages": [],
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
    }


def test_vault_floor_heals_a_carried_claim_with_no_backing():
    """A carried `direct` whose vault evidence is gone is healed to `gap` —
    the previous row can never reinstate an unbacked claim (#318)."""
    merged, carried = _merge([row("Docker", "gap")], [row("Docker", "direct")])
    healed = _apply_floors_to_merged(
        merged, carried, denied_concepts=[], profile_json=_vault("Python")
    )
    assert healed[0]["status"] == "gap" and healed[0]["claimable"] is False


def test_vault_floor_keeps_a_backed_carried_claim():
    merged, carried = _merge([row("Docker", "gap")], [row("Docker", "direct")])
    healed = _apply_floors_to_merged(
        merged, carried, denied_concepts=[],
        profile_json=_vault("Docker", bullets=["Ran the Docker build pipeline"]),
    )
    assert healed[0]["status"] == "direct"
    assert healed[0]["narrative_backed"] is True, "re-annotated against today's vault"


def test_denial_floor_reaches_a_carried_row():
    """The fresh row was floored by containment (gap), the previous row was
    `direct`: carrying it back must not undo the floor."""
    merged, carried = _merge(
        [row("CSS", "gap")], [row("CSS", "direct")], jd=["css"]
    )
    healed = _apply_floors_to_merged(
        merged, carried,
        denied_concepts=[{"concept": "Tailwind CSS", "denial_level": "direct"}],
        profile_json=_vault("CSS", bullets=["Styled pages with CSS"]),
    )
    # Released: the vault attests CSS outside the denied compound (#207) —
    # the carried claim is legitimate and stays.
    assert healed[0]["status"] == "direct"
    healed_unattested = _apply_floors_to_merged(
        merged, carried,
        denied_concepts=[{"concept": "Tailwind CSS", "denial_level": "direct"}],
        profile_json=_vault("Python"),
    )
    assert healed_unattested[0]["claimable"] is False


def test_declared_denial_floors_a_carried_row_to_denied():
    merged, carried = _merge([row("Docker", "gap")], [row("Docker", "direct")])
    healed = _apply_floors_to_merged(
        merged, carried,
        denied_concepts=[{"concept": "Docker", "denial_level": "direct"}],
        profile_json=_vault("Docker", bullets=["Ran the Docker build pipeline"]),
    )
    assert healed[0]["status"] == "denied"


# ===========================================================================
# Integration half — the real analyze_gaps, a scripted classifier
# ===========================================================================


class _Scripted(MockLLMProvider):
    """Classification and clustering answers from queues; everything else is
    the ordinary mock. Records every clustering prompt so a test can read
    WHICH concepts were sent to the clustering model."""

    def __init__(self) -> None:
        self.classifications: list[list[dict]] = []
        self.clusters: list[list[dict]] = []
        self.cluster_prompts: list[str] = []
        self.gap_calls = 0

    async def aparse_json(self, prompt, *, system=None, **kwargs):  # type: ignore[override]
        if system == GAP_SYSTEM_PROMPT:
            self.gap_calls += 1
            return {"classifications": self.classifications.pop(0), "strengths": []}
        if system == CLUSTERING_SYSTEM_PROMPT:
            self.cluster_prompts.append(prompt)
            return {"clusters": self.clusters.pop(0)}
        return await super().aparse_json(prompt, system=system, **kwargs)


def _sent(prompt: str) -> tuple[list[str], list[str]]:
    """The (Category C, Category B) lists a clustering prompt carried."""
    c_part = prompt.split("Category C gaps (missing evidence):\n", 1)[1].split("\n", 1)[0]
    b_part = prompt.split("Category B gaps (likely but unstated):\n", 1)[1].split("\n", 1)[0]
    return json.loads(c_part), json.loads(b_part)


def cls(requirement, status):
    return {"requirement": requirement, "status": status, "reason": f"{requirement} per vault"}


def clu(cid, gaps, label=None):
    return {
        "id": cid,
        "label": label or cid,
        "gaps": list(gaps),
        "jd_skills": list(gaps),
        "jd_context": f"The role needs {', '.join(gaps)}.",
    }


_BULLETS = [
    "Built Python services",
    "Ran the Docker build pipeline",
    "Operated Kubernetes clusters for staging",
]


def _profile(extra_skill=None, *, skills=("Python", "Docker", "Kubernetes"), bullets=_BULLETS, denied=None):
    p = _vault(*skills, *( [extra_skill] if extra_skill else []), bullets=bullets)
    if denied:
        p["metadata"] = {"denied_concepts": denied}
    return p


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db):
    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Platform engineer: Python, Docker, Kubernetes, Terraform.",
        role_title="Platform Engineer",
        required_skills=["Python", "Docker", "Kubernetes", "Terraform"],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="EN",
    )
    profile = make_master_profile(id=uuid.uuid4(), profile_json=_profile())
    db.add_all([user, job, profile])
    await db.commit()
    return job, profile


BASELINE = [cls("Python", "direct"), cls("Docker", "direct"), cls("Kubernetes", "partial"), cls("Terraform", "gap")]
CLUSTERS = [clu("cluster-iac", ["Terraform"], "Infrastructure as code"), clu("cluster-k8s", ["Kubernetes"], "Orchestration")]


async def _first(db, provider):
    job, profile = await _seed(db)
    provider.classifications.append(BASELINE)
    provider.clusters.append(copy.deepcopy(CLUSTERS))
    r1 = await analyze_gaps(job.id, db, provider)
    return job, profile, r1


async def _change_profile(db, profile, **kwargs):
    set_profile_json(profile, _profile(**kwargs))
    await db.commit()


def _statuses(resp):
    return {b.requirement: b.status for b in resp.requirement_breakdown}


def _arith(resp):
    slots = sum(b.slot for b in resp.requirement_breakdown)
    return sum(b.earned for b in resp.requirement_breakdown) / slots


@pytest.mark.asyncio
async def test_first_analysis_initialises_the_per_gap_record(db):
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    assert r1.match_score == pytest.approx(0.625)
    by_id = {c.id: c for c in r1.gap_clusters}
    assert set(by_id) == {"cluster-iac", "cluster-k8s"}
    assert by_id["cluster-iac"].coverage == "open"
    assert by_id["cluster-k8s"].coverage == "partly_covered", "a Category B cluster starts here"
    assert by_id["cluster-k8s"].category == "B" and by_id["cluster-iac"].category == "C"
    assert all(c.outcome.asked == 0 for c in r1.gap_clusters)
    assert all(c.budget_remaining == 2 for c in r1.gap_clusters)
    # Ruling C-1: the response carries each requirement's chip status,
    # derived from this row's own ledger.
    assert [m.model_dump() for m in by_id["cluster-k8s"].member_statuses] == [
        {"member": "Kubernetes", "status": "partial"}
    ]
    assert [m.model_dump() for m in by_id["cluster-iac"].member_statuses] == [
        {"member": "Terraform", "status": "gap"}
    ]


@pytest.mark.asyncio
async def test_refresh_keeps_a_stochastic_flip_from_lowering_the_score(db):
    """Two answer-driven recomputes of an unchanged JD with evidence added:
    the classifier flips an untouched requirement down each time, and the
    published score, table and ledger stay where they were."""
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)

    await _change_profile(db, profile, extra_skill="Jira")
    provider.classifications.append(
        [cls("Python", "direct"), cls("Docker", "partial"), cls("Kubernetes", "partial"), cls("Terraform", "gap")]
    )
    r2 = await analyze_gaps(job.id, db, provider, answer_scope=AnswerScope())

    await _change_profile(db, profile, extra_skill="Confluence")
    provider.classifications.append(
        [cls("Python", "gap"), cls("Docker", "direct"), cls("Kubernetes", "partial"), cls("Terraform", "gap")]
    )
    r3 = await analyze_gaps(job.id, db, provider, answer_scope=AnswerScope())

    assert r1.id != r2.id != r3.id
    for r in (r2, r3):
        assert r.match_score == pytest.approx(r1.match_score)
        assert _statuses(r) == _statuses(r1)
        assert r.match_score == pytest.approx(_arith(r)), "headline == its own table"
        ledger = {e.concept: e.status for e in r.keyword_ledger}
        assert ledger["Python"] == "direct" and ledger["Docker"] == "direct", (
            "the PERSISTED ledger is the merged one — the writers read what the score says"
        )
    assert provider.cluster_prompts == [provider.cluster_prompts[0]], (
        "no concept was new: the recompute made no clustering call"
    )
    assert [c.id for c in r3.gap_clusters] == [c.id for c in r1.gap_clusters]


@pytest.mark.asyncio
async def test_the_non_answer_path_publishes_the_fresh_ledger(db):
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    await _change_profile(db, profile, extra_skill="Jira")
    provider.classifications.append(
        [cls("Python", "direct"), cls("Docker", "partial"), cls("Kubernetes", "partial"), cls("Terraform", "gap")]
    )
    provider.clusters.append([clu("cluster-containers", ["Docker"])])
    r2 = await analyze_gaps(job.id, db, provider)  # answer_scope=None
    assert _statuses(r2)["Docker"] == "partial"
    assert r2.match_score == pytest.approx(0.5)
    assert [c.id for c in r2.gap_clusters] == [c.id for c in r1.gap_clusters] + ["cluster-containers"], (
        "clusters carry forward on every recompute of the same JD (clause 4); "
        "the newly askable Docker is appended"
    )
    assert _sent(provider.cluster_prompts[-1]) == ([], ["Docker"])


@pytest.mark.asyncio
async def test_refresh_after_a_denial_lowers_the_score(db):
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    await _change_profile(db, profile, denied=[{"concept": "Docker", "denial_level": "direct"}])
    provider.classifications.append(BASELINE)  # the classifier still says direct
    r2 = await analyze_gaps(job.id, db, provider, answer_scope=AnswerScope())
    assert _statuses(r2)["Docker"] == "denied"
    assert r2.match_score == pytest.approx(0.375)
    assert r2.match_score == pytest.approx(_arith(r2))


@pytest.mark.asyncio
async def test_a_worked_cluster_may_move_down(db):
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    await _change_profile(db, profile, extra_skill="Jira")
    provider.classifications.append(
        [cls("Python", "direct"), cls("Docker", "direct"), cls("Kubernetes", "gap"), cls("Terraform", "gap")]
    )
    r2 = await analyze_gaps(
        job.id, db, provider,
        answer_scope=AnswerScope(cluster_ids=("cluster-k8s",)),
    )
    assert _statuses(r2)["Kubernetes"] == "gap"
    assert r2.match_score == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_refresh_heals_a_carried_claim_whose_vault_backing_is_gone(db):
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    await _change_profile(
        db, profile, skills=("Python", "Kubernetes"),
        bullets=["Built Python services", "Operated Kubernetes clusters for staging"],
    )
    provider.classifications.append(
        [cls("Python", "direct"), cls("Docker", "gap"), cls("Kubernetes", "partial"), cls("Terraform", "gap")]
    )
    provider.clusters.append([clu("cluster-containers", ["Docker"])])
    r2 = await analyze_gaps(job.id, db, provider, answer_scope=AnswerScope())
    assert _statuses(r2)["Docker"] == "gap", "the previous `direct` may not outlive its evidence"
    assert r2.match_score == pytest.approx(0.375)


@pytest.mark.asyncio
async def test_carry_forward_keeps_the_record_and_clusters_only_new_concepts(db):
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    # A session asked cluster-iac once (the record the turn writes).
    row1 = (await db.execute(select(GapAnalysis).where(GapAnalysis.id == r1.id))).scalar_one()
    clusters = copy.deepcopy(row1.gap_clusters)
    clusters[0]["outcome"] = {"asked": 1, "covered": [], "declined": [], "session_ids": ["s-1"]}
    row1.gap_clusters = clusters
    await db.commit()

    # Docker's vault evidence is gone → the vault floor heals its carried
    # claim to gap → it is askable now and belongs to no carried cluster →
    # the only concept sent to the clustering model.
    await _change_profile(
        db, profile, skills=("Python", "Kubernetes"),
        bullets=["Built Python services", "Operated Kubernetes clusters for staging"],
    )
    provider.classifications.append(
        [cls("Python", "direct"), cls("Docker", "gap"), cls("Kubernetes", "partial"), cls("Terraform", "gap")]
    )
    provider.clusters.append([clu("cluster-iac", ["Docker"], "Containers")])  # id collides
    r2 = await analyze_gaps(job.id, db, provider, answer_scope=AnswerScope())
    ids = [c.id for c in r2.gap_clusters]
    assert ids == ["cluster-iac", "cluster-k8s", "cluster-iac-2"]
    iac = r2.gap_clusters[0]
    assert iac.outcome.asked == 1 and iac.outcome.session_ids == ["s-1"]
    assert iac.label == "Infrastructure as code"
    assert r2.gap_clusters[2].outcome.asked == 0
    assert _sent(provider.cluster_prompts[-1]) == (["Docker"], []), (
        "only the concept no carried cluster holds goes to the clustering model"
    )


@pytest.mark.asyncio
async def test_carry_forward_moves_a_now_claimable_member_to_covered(db):
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    await _change_profile(db, profile, skills=("Python", "Docker", "Kubernetes", "Terraform"),
                          bullets=[*_BULLETS, "Wrote Terraform modules"])
    provider.classifications.append(
        [cls("Python", "direct"), cls("Docker", "direct"), cls("Kubernetes", "partial"), cls("Terraform", "direct")]
    )
    r2 = await analyze_gaps(
        job.id, db, provider, answer_scope=AnswerScope(cluster_ids=("cluster-iac",))
    )
    iac = next(c for c in r2.gap_clusters if c.id == "cluster-iac")
    assert iac.gaps == [] and iac.outcome.covered == ["Terraform"]
    assert iac.coverage == "covered", "a worked cluster stays listed with its coverage"


@pytest.mark.asyncio
async def test_a_carried_cluster_category_is_re_derived_from_its_members(db):
    """#675 line 60 on EVERY recompute (clause 4): Terraform turns from a
    Category C gap into a partial, so its carried cluster is now "B"."""
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    assert next(c for c in r1.gap_clusters if c.id == "cluster-iac").category == "C"
    await _change_profile(db, profile, skills=("Python", "Docker", "Kubernetes", "Terraform"),
                          bullets=[*_BULLETS, "Reviewed Terraform plans"])
    provider.classifications.append(
        [cls("Python", "direct"), cls("Docker", "direct"), cls("Kubernetes", "partial"), cls("Terraform", "partial")]
    )
    r2 = await analyze_gaps(job.id, db, provider, answer_scope=AnswerScope())
    iac = next(c for c in r2.gap_clusters if c.id == "cluster-iac")
    assert iac.category == "B" and iac.coverage == "partly_covered"


@pytest.mark.asyncio
async def test_carry_forward_drops_an_orphaned_member(db):
    """A paraphrased member that matches no ledger row would stay open forever
    while the same requirement, reworded, is clustered again (clause 4)."""
    provider = _Scripted()
    job, profile = await _seed(db)
    provider.classifications.append(BASELINE)
    provider.clusters.append(
        [clu("cluster-iac", ["Terraform", "Pulumi stacks"], "IaC"), clu("cluster-k8s", ["Kubernetes"])]
    )
    r1 = await analyze_gaps(job.id, db, provider)
    assert "Pulumi stacks" in all_members(r1.gap_clusters[0].model_dump()), "a fresh cluster keeps it"

    await _change_profile(db, profile, extra_skill="Jira")
    provider.classifications.append(BASELINE)
    r2 = await analyze_gaps(job.id, db, provider, answer_scope=AnswerScope())
    iac = next(c for c in r2.gap_clusters if c.id == "cluster-iac")
    assert all_members(iac.model_dump()) == ["Terraform"]


@pytest.mark.asyncio
async def test_a_jd_change_re_clusters_and_does_not_merge(db):
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    job.required_skills = ["Python", "Docker", "Kubernetes", "Terraform", "Ansible"]
    await db.commit()
    provider.classifications.append(
        [cls("Python", "direct"), cls("Docker", "partial"), cls("Kubernetes", "partial"),
         cls("Terraform", "gap"), cls("Ansible", "gap")]
    )
    provider.clusters.append([clu("cluster-auto", ["Terraform", "Ansible"])])
    r2 = await analyze_gaps(job.id, db, provider, answer_scope=AnswerScope())
    assert _statuses(r2)["Docker"] == "partial", "no merge across a JD change"
    assert [c.id for c in r2.gap_clusters] == ["cluster-auto"]


@pytest.mark.asyncio
async def test_jd_lists_unchanged_reads_the_previous_ledger(db):
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    prev = (await db.execute(select(GapAnalysis).where(GapAnalysis.id == r1.id))).scalar_one()
    assert _jd_lists_unchanged(job, prev)
    job.required_skills = ["Python", "Docker", "Kubernetes"]  # a term removed
    assert not _jd_lists_unchanged(job, prev)
    job.required_skills = ["Python", "Docker", "Kubernetes", "Terraform", "Go"]  # added
    assert not _jd_lists_unchanged(job, prev)
    assert not _jd_lists_unchanged(job, None)


@pytest.mark.asyncio
async def test_unchanged_inputs_still_reuse_the_row(db):
    """E037 PQ #3 — the fingerprint short-circuit is untouched by the merge."""
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    calls = provider.gap_calls
    r2 = await analyze_gaps(job.id, db, provider, answer_scope=AnswerScope())
    assert r2.id == r1.id and provider.gap_calls == calls


@pytest.mark.asyncio
async def test_legacy_clusters_without_a_record_are_carried_and_initialised(db):
    provider = _Scripted()
    job, profile, r1 = await _first(db, provider)
    row1 = (await db.execute(select(GapAnalysis).where(GapAnalysis.id == r1.id))).scalar_one()
    row1.gap_clusters = [
        {k: v for k, v in c.items() if k not in ("outcome", "coverage")} for c in row1.gap_clusters
    ]
    await db.commit()
    # The GET path derives coverage for a legacy row from its own ledger.
    from applire.schemas.gap import GapAnalysisResponse

    legacy = GapAnalysisResponse.model_validate(row1)
    assert {c.id: c.coverage for c in legacy.gap_clusters} == {
        "cluster-iac": "open", "cluster-k8s": "partly_covered"
    }
    await _change_profile(db, profile, extra_skill="Jira")
    provider.classifications.append(BASELINE)
    r2 = await analyze_gaps(job.id, db, provider, answer_scope=AnswerScope())
    persisted = (await db.execute(
        select(GapAnalysis).where(GapAnalysis.job_analysis_id == job.id).order_by(desc(GapAnalysis.created_at)).limit(1)
    )).scalar_one().gap_clusters
    assert all("outcome" in c and "coverage" in c for c in persisted)
    assert all("budget_remaining" not in c for c in persisted), "ruling C-2: derived, never persisted"
    assert json.loads(json.dumps(persisted)) == persisted
