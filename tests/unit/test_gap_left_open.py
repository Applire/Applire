# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ruling K-1 (ADR-089 amended 2026-09-27) — a gap the candidate leaves open
by hand.

``outcome.left_open`` is one new fact on the per-gap record. Two things can
lose it silently (SF-GAP.19): the whitelisting normaliser every carry path
rebuilds ``outcome`` from (``gap_coverage._outcome_of``) and the pydantic
``GapClusterOutcome``; and a door that checks budget/coverage without
``is_askable``. Each test below is built on a shape only the K-1 rule decides:
a cluster that is otherwise open with budget left.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.services.gap_coverage import (
    LeftOpenRefused,
    apply_turn_outcome,
    is_askable,
    is_left_open,
    refresh_cluster_from_ledger,
    remaining_budget,
    resplit_cluster,
    with_left_open,
)

# ADR-092: the rows below are owner-keyed to the ambient (harness) owner; the HTTP
# caller must be that same user to reach them.
from tests.support.owners import HARNESS_USER_ID  # noqa: E402
from tests.support.posting_links import link_posting  # noqa: E402

TEST_USER_ID = HARNESS_USER_ID


def _row(concept, status):
    return {
        "concept": concept, "surface_forms": [concept], "sources": ["required"],
        "fit_weight": 1.0, "status": status,
        "evidence": "vault says so" if status in ("direct", "partial") else "",
        "claimable": status in ("direct", "partial"), "narrative_backed": True,
    }


_LEDGER = [_row("Terraform", "gap"), _row("Helm", "gap"), _row("Docker", "direct")]


def _cluster(cid="c1", gaps=("Terraform", "Helm"), *, asked=0, left_open=None, covered=(),
             coverage="open"):
    outcome = {"asked": asked, "covered": list(covered), "declined": [], "session_ids": []}
    if left_open is not None:
        outcome["left_open"] = left_open
    return {
        "id": cid, "label": f"Label {cid}", "category": "C", "gaps": list(gaps),
        "jd_skills": [], "jd_context": "ctx", "outcome": outcome, "coverage": coverage,
    }


# ---------------------------------------------------------------------------
# The fact and the one predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [(True, True), (False, False), ("true", False),
                                            (1, False), (None, False)])
def test_only_a_literal_true_reads_as_left_open(value, expected):
    assert is_left_open(_cluster(left_open=value)) is expected


def test_a_legacy_record_is_not_left_open():
    c = _cluster()
    del c["outcome"]
    assert is_left_open(c) is False
    assert is_askable(c)


def test_an_open_cluster_with_budget_left_open_is_not_askable():
    c = _cluster(left_open=True)
    assert remaining_budget(c, 2) == 2, "leaving a gap open spends no budget"
    assert not is_askable(c, 2)


def test_leaving_open_sets_the_fact_and_nothing_else():
    c = _cluster(asked=1)
    out = with_left_open(c, True)
    assert out["outcome"]["left_open"] is True
    assert out["gaps"] == c["gaps"]
    assert out["coverage"] == "open"
    assert out["outcome"]["asked"] == 1
    assert "left_open" not in c["outcome"], "the input is never mutated"


def test_picking_it_up_again_makes_it_askable_again():
    back = with_left_open(_cluster(left_open=True), False)
    assert back["outcome"]["left_open"] is False
    assert is_askable(back, 2)


@pytest.mark.parametrize(
    "closed",
    [
        _cluster(asked=2),  # budget spent
        _cluster(gaps=(), covered=("Terraform", "Helm"), coverage="covered"),
        _cluster(gaps=(), coverage="declined"),
    ],
    ids=["spent", "covered", "declined"],
)
def test_a_closed_cluster_cannot_be_left_open(closed):
    with pytest.raises(LeftOpenRefused):
        with_left_open(closed, True)


# ---------------------------------------------------------------------------
# Every carry path keeps it (the normaliser trap)
# ---------------------------------------------------------------------------


def test_a_recompute_carries_the_fact():
    out = refresh_cluster_from_ledger(_cluster(left_open=True), _LEDGER, None)
    assert out is not None and out["outcome"]["left_open"] is True
    assert not is_askable(out, 2)


def test_an_in_place_resplit_carries_the_fact():
    out = resplit_cluster(_cluster(left_open=True), _LEDGER, None)
    assert out["outcome"]["left_open"] is True


def test_a_recorded_turn_carries_the_fact():
    out = apply_turn_outcome(_cluster(left_open=True), {"Terraform": "open"}, session_id="s1",
                             keyword_ledger=_LEDGER, charge=False)
    assert out["outcome"]["left_open"] is True


def test_the_response_schema_carries_the_fact():
    from applire.schemas.gap_cluster import GapClusterSchema

    assert GapClusterSchema.model_validate(_cluster(left_open=True)).outcome.left_open is True
    legacy = _cluster()
    del legacy["outcome"]
    assert GapClusterSchema.model_validate(legacy).outcome.left_open is False


# ---------------------------------------------------------------------------
# The session door's refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lang,expected", [("en", "left open on the gaps page"), ("de", "offen gelassen")])
def test_the_refusal_names_the_gap_and_the_way_back(lang, expected):
    from applire.services.session import gap_not_askable

    refusal = gap_not_askable(_cluster(left_open=True), lang)
    assert refusal is not None
    assert refusal.error_code == "gap_left_open"
    assert "Label c1" in refusal.message and expected in refusal.message


def test_a_left_open_gap_whose_budget_was_spent_meanwhile_reports_spent():
    """Precedence covered/declined > spent > left open (K1-ADV-2): "pick it up
    again" is impossible once the budget is gone, so the refusal says spent —
    the same order the gaps page and the liability panel use."""
    from applire.services.session import gap_not_askable

    assert gap_not_askable(_cluster(asked=2, left_open=True), "en").error_code == "gap_budget_spent"


def test_a_left_open_gap_a_recompute_found_covered_reports_covered():
    from applire.services.session import gap_not_askable

    c = _cluster(gaps=(), covered=("Terraform", "Helm"), coverage="covered", left_open=True)
    assert gap_not_askable(c, "en").error_code == "gap_already_covered"


# ---------------------------------------------------------------------------
# The service and the endpoint
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    import applire.models.user           # noqa: F401
    import applire.models.job            # noqa: F401
    import applire.models.profile        # noqa: F401
    import applire.models.gap            # noqa: F401
    import applire.models.cv             # noqa: F401
    import applire.models.cover_letter   # noqa: F401
    import applire.models.session        # noqa: F401
    import applire.models.flow           # noqa: F401
    import applire.models.application    # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company        # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads        # noqa: F401
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job = JobAnalysis(
            id=uuid.uuid4(), raw_text_hash="hash-left-open", raw_text="Platform Engineer",
            role_title="Platform Engineer", required_skills=["Terraform", "Helm", "Docker"],
            nice_to_have_skills=[], keywords=[], seniority_level="senior",
            company_culture_signals=[], language_requirement="EN",
        )
        profile = make_master_profile(id=uuid.uuid4(), profile_json={"skills": [{"name": "Docker"}]})
        session.add_all([job, profile])
        await session.flush()
        await link_posting(session, job, TEST_USER_ID)  # ADR-092: the caller's link
        await session.commit()
        older = GapAnalysis(
            job_analysis_id=job.id, profile_id=profile.id, match_score=0.4,
            keyword_ledger=_LEDGER, gap_clusters=[_cluster()],
            created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        latest = GapAnalysis(
            job_analysis_id=job.id, profile_id=profile.id, match_score=0.4,
            keyword_ledger=_LEDGER,
            gap_clusters=[_cluster(), _cluster("c2", gaps=("Helm",), asked=2)],
            created_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        )
        session.add_all([older, latest])
        await session.commit()
        yield session, job.id, older.id, latest.id
    await engine.dispose()


async def _clusters(session, row_id):
    from applire.models.gap import GapAnalysis

    return (
        await session.execute(
            select(GapAnalysis).where(GapAnalysis.id == row_id).execution_options(populate_existing=True)
        )
    ).scalar_one().gap_clusters


@pytest.mark.asyncio
async def test_the_service_writes_the_latest_row_only(db_session):
    from applire.services.gap import set_cluster_left_open

    session, job_id, older_id, latest_id = db_session
    await set_cluster_left_open(job_id, "c1", True, session)
    assert (await _clusters(session, latest_id))[0]["outcome"]["left_open"] is True
    assert "left_open" not in (await _clusters(session, older_id))[0]["outcome"]
    assert (await _clusters(session, latest_id))[1]["outcome"].get("left_open") is None, \
        "a sibling cluster is untouched"


@pytest.mark.asyncio
async def test_a_repeated_click_is_idempotent(db_session):
    from applire.services.gap import set_cluster_left_open

    session, job_id, _older, latest_id = db_session
    await set_cluster_left_open(job_id, "c1", True, session)
    await set_cluster_left_open(job_id, "c1", True, session)  # would raise if it re-checked askable
    await set_cluster_left_open(job_id, "c2", False, session)  # clearing an unset fact: no-op
    clusters = await _clusters(session, latest_id)
    assert clusters[0]["outcome"]["left_open"] is True
    assert "left_open" not in clusters[1]["outcome"]


@pytest_asyncio.fixture
async def client(db_session):
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.job import router

    session, job_id, _older, latest_id = db_session
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=TEST_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, job_id, session, latest_id


@pytest.mark.asyncio
async def test_the_endpoint_leaves_open_and_picks_up_again(client):
    ac, job_id, session, latest_id = client
    resp = await ac.post(f"/api/job/{job_id}/gaps/c1/left-open", json={"left_open": True})
    assert resp.status_code == 200
    body = resp.json()
    c1 = next(c for c in body["gap_clusters"] if c["id"] == "c1")
    assert c1["outcome"]["left_open"] is True
    assert c1["budget_remaining"] == 2, "no budget is charged"
    assert c1["coverage"] == "open", "the gap stays a gap"
    assert body["match_score"] == pytest.approx(0.4), "the score does not move"

    resp = await ac.post(f"/api/job/{job_id}/gaps/c1/left-open", json={"left_open": False})
    assert resp.status_code == 200
    assert (await _clusters(session, latest_id))[0]["outcome"]["left_open"] is False


@pytest.mark.asyncio
async def test_the_endpoint_refuses_a_gap_with_no_questions_left(client):
    ac, job_id, _session, _latest = client
    resp = await ac.post(f"/api/job/{job_id}/gaps/c2/left-open", json={"left_open": True})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "gap_not_askable"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["{job}/gaps/nope/left-open", "{other}/gaps/c1/left-open"])
async def test_the_endpoint_404s_an_unknown_gap_or_job(client, path):
    ac, job_id, _session, _latest = client
    url = "/api/job/" + path.format(job=job_id, other=uuid.uuid4())
    resp = await ac.post(url, json={"left_open": True})
    assert resp.status_code == 404
