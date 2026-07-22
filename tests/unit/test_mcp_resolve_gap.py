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

"""resolve_gap (ADR-054) — the agent-channel projection of the UI's targeted
Gap-Click micro-session. One stateless call resolves one gap cluster: create a
targeted micro-session, apply the agent's testimony through the guided
reconciler, return the resolution. No session_id, no termination signal."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from mcp.shared.exceptions import McpError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def db():
    import applire.models  # noqa: F401
    import importlib
    import pkgutil

    for _m in pkgutil.iter_modules(applire.models.__path__):
        importlib.import_module(f"applire.models.{_m.name}")
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _patches(session):
    return (
        patch("applire.mcp.server.get_db", return_value=_db_cm(session)),
        patch("applire.mcp.server.get_provider", return_value=MagicMock()),
    )


async def _seed_job_and_analysis(db, cluster_ids):
    from applire.models.job import JobAnalysis
    from applire.models.gap import GapAnalysis
    from applire.models.profile import MasterProfile

    profile = MasterProfile(profile_json={"personal_info": {"name": "Kaile"}})
    db.add(profile)
    job = JobAnalysis(
        raw_text_hash="h", raw_text="jd", role_title="Engineer",
        required_skills=["Kubernetes"], nice_to_have_skills=[], keywords=[],
        seniority_level="senior", company_culture_signals=[], language_requirement="",
        jd_language="en",
    )
    db.add(job)
    await db.flush()
    ga = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        match_score=0.5,
        critical_gaps=[], minor_gaps=[], strengths=[], keyword_gaps=[],
        category_a=[], category_b=[], category_c=[],
        gap_clusters=[
            {"id": cid, "label": cid, "category": "B", "gaps": [cid],
             "jd_skills": [cid], "jd_context": ""}
            for cid in cluster_ids
        ],
    )
    db.add(ga)
    await db.commit()
    return job.id


@pytest.mark.asyncio
async def test_empty_answer_rejected(db):
    from applire.mcp.server import resolve_gap

    job_id = await _seed_job_and_analysis(db, ["cluster-k8s"])
    p1, p2 = _patches(db)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await resolve_gap(job_id=str(job_id), gap_id="cluster-k8s", answer="  ")
    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_bad_job_uuid_rejected(db):
    from applire.mcp.server import resolve_gap

    p1, p2 = _patches(db)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await resolve_gap(job_id="not-a-uuid", gap_id="x", answer="a")
    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_no_analysis_is_not_found(db):
    from applire.mcp.server import resolve_gap

    p1, p2 = _patches(db)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await resolve_gap(job_id=str(uuid.uuid4()), gap_id="x", answer="a")
    assert exc.value.error.code == -32001


@pytest.mark.asyncio
async def test_unknown_gap_id_rejected_with_valid_ids(db):
    from applire.mcp.server import resolve_gap

    job_id = await _seed_job_and_analysis(db, ["cluster-k8s", "cluster-aws"])
    p1, p2 = _patches(db)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await resolve_gap(job_id=str(job_id), gap_id="cluster-bogus", answer="a")
    assert exc.value.error.code == -32602
    # the error lists the valid ids so the agent can self-correct
    assert "cluster-k8s" in exc.value.error.message
    assert "cluster-aws" in exc.value.error.message


@pytest.mark.asyncio
async def test_happy_path_composes_targeted_session(db):
    """resolve_gap = create targeted micro-session + apply the answer; returns
    the scoped question + an addressed status. Underlying guided machinery is
    mocked (already tested); this asserts the composition + response shape."""
    from applire.mcp.server import resolve_gap
    from applire.schemas.session import SessionCreateResponse, SessionMessageResponse

    job_id = await _seed_job_and_analysis(db, ["cluster-k8s"])
    sid = uuid.uuid4()
    created = SessionCreateResponse(
        session_id=sid, mode="targeted",
        first_question="Do you have Kubernetes experience? If so, describe it.",
        estimated_questions=1, question="...", gaps_total=1, gaps_remaining=1,
    )
    completed = SessionMessageResponse(
        complete=True, reason="max_questions_reached", questions_asked=2,
        gaps_resolved=1, gaps_unresolved=[], completeness_score=0.62,
    )

    create_mock = AsyncMock(return_value=created)
    send_mock = AsyncMock(return_value=completed)
    p1, p2 = _patches(db)
    with p1, p2, \
        patch("applire.services.session.create_session", create_mock), \
        patch("applire.services.session.send_message", send_mock):
        result = await resolve_gap(
            job_id=str(job_id), gap_id="cluster-k8s",
            answer="I ran production K8s clusters for 3 years at Acme.",
        )

    # created a TARGETED session scoped to the cluster
    create_req = create_mock.call_args.args[0]
    assert create_req.mode == "targeted"
    assert create_req.target_gap == "cluster-k8s"
    # applied the agent's testimony to THAT session
    assert send_mock.call_args.args[0] == sid
    # response is clean + honest: the scoped question, addressed status, no
    # leaked "max_questions_reached" internal reason
    assert result["gap_id"] == "cluster-k8s"
    assert result["question_asked"].startswith("Do you have Kubernetes")
    assert result["status"] == "addressed"
    assert result["profile_completeness"] == 0.62
    assert "reason" not in result
    assert "pending_confirmations" not in result  # clean resolution → no noise


@pytest.mark.asyncio
async def test_parked_ambiguity_surfaces_as_needs_confirmation(db):
    """A reconciler ambiguity on the one answer must NOT be silently dropped
    (the micro-session ceiling-return skips the confirmation-surfacing branch);
    resolve_gap surfaces it as needs_confirmation with the parked prompt."""
    from applire.mcp.server import resolve_gap
    from applire.schemas.session import (
        ConfirmationPrompt, SessionCreateResponse, SessionMessageResponse,
    )

    job_id = await _seed_job_and_analysis(db, ["cluster-k8s"])
    created = SessionCreateResponse(
        session_id=uuid.uuid4(), mode="targeted",
        first_question="Do you have Kubernetes experience?",
        estimated_questions=1, question="...", gaps_total=1, gaps_remaining=1,
    )
    completed = SessionMessageResponse(
        complete=True, reason="max_questions_reached", questions_asked=2,
        gaps_resolved=1, gaps_unresolved=[], completeness_score=0.5,
        pending_confirmations=[ConfirmationPrompt(
            question="Is this the same role as your 'DevOps Lead' at Acme?",
            options=["Yes, same role", "No, separate"], context={},
        )],
    )
    p1, p2 = _patches(db)
    with p1, p2, \
        patch("applire.services.session.create_session", AsyncMock(return_value=created)), \
        patch("applire.services.session.send_message", AsyncMock(return_value=completed)):
        result = await resolve_gap(
            job_id=str(job_id), gap_id="cluster-k8s", answer="I led K8s at Acme.",
        )

    assert result["status"] == "needs_confirmation"
    assert result["pending_confirmations"][0]["question"].startswith("Is this the same role")
