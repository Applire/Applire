# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#675 line 77 / founder-UAT F-3 → ADR-089 clause 5 — the answer-driven
recompute's two call sites.

`services/gap.py` is the single seam, but it is reached from TWO call sites and a
fix that only holds on one of them is the shape this project has been burned by
before (a shared helper needs one named seam test per call site,
`using-applire` §Verification hierarchy):

  1. `POST /api/job/{id}/gaps/refresh`  — `routers/job.py::refresh_gap_analysis`
  2. interview completion               — `services/session.py::_complete_session`

Both pass an `AnswerScope` (ADR-089 clause 5 replaced ruling B-1's whole-slice
clamp with a per-requirement merge). Each seam test drives the REAL call site
with a denial persisted the way the reconciler persists one
(`profile_json.metadata.denied_concepts`) and reads the DELIVERED gap_analyses
row: the denial must lower the score on both. The completion call sits inside a
best-effort try/except, so a wrong call there fails SILENTLY (the recompute is
skipped, nothing raises) — `test_completion_reaches_analyze_gaps_with_an_answer_scope`
asserts the call itself happened, with the session's answers in its scope.

The per-rule merge tests live in `test_gap_answer_merge.py`.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.session import InterviewSession
from applire.models.user import User
from applire.providers.llm.mock import MockLLMProvider
from applire.services.gap import analyze_gaps
from applire.services.gap_coverage import AnswerScope

from tests.support.profile_factory import make_master_profile, set_profile_json

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000077")

_DENIAL = {
    "concept": "Docker",
    "denial_level": "direct",
    "statement": "I have never run Docker myself — that was a colleague's work.",
}


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


def _profile_json() -> dict:
    """Both JD requirements held AND backed by the vault — #318's
    `assert_claimable_backed` heals an unbacked claimable row to `gap`, which
    would score 0 and could never show a denial-driven DROP."""
    return {
        "personal_info": {
            "first_name": "Max",
            "last_name": "Muster",
            "email": "max@test.de",
        },
        "professional_summary": {"de": "Entwickler", "en": "Developer"},
        "work_experience": [
            {
                "company": "Acme",
                "role": "Dev",
                "start_date": "2018-01",
                "responsibilities": [
                    "Built Python services",
                    "Ran the Docker build pipeline",
                ],
            }
        ],
        "education": [{"institution": "TU Berlin", "degree": "BSc", "field": "CS"}],
        "skills": [
            {"name": "Python", "category": "technical", "proficiency": "expert"},
            {"name": "Docker", "category": "technical", "proficiency": "advanced"},
        ],
        "languages": [{"language": "German", "level": "native"}],
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
    }


def _with_denial(base: dict) -> dict:
    out = json.loads(json.dumps(base))
    out.setdefault("metadata", {})["denied_concepts"] = [dict(_DENIAL)]
    return out


def _headline_from_breakdown(breakdown) -> float | None:
    slots = 0.0
    earned = 0.0
    for item in breakdown or []:
        if isinstance(item, dict):
            slots += float(item.get("slot") or 0.0)
            earned += float(item.get("earned") or 0.0)
        else:
            slots += float(item.slot or 0.0)
            earned += float(item.earned or 0.0)
    return None if slots == 0.0 else earned / slots


async def _seed(db):
    user = User(id=_STUB_USER_ID, email="local@applire.community")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Senior Python Engineer, container platform.",
        role_title="Senior Python Engineer",
        required_skills=["Python", "Docker"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="English",
    )
    profile = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    db.add_all([user, job, profile])
    await db.commit()
    return user, job, profile


async def _latest(db, job_id) -> GapAnalysis:
    return (
        await db.execute(
            select(GapAnalysis)
            .where(GapAnalysis.job_analysis_id == job_id)
            .order_by(desc(GapAnalysis.created_at))
            .limit(1)
        )
    ).scalar_one()


# ===========================================================================
# Call site 1 — POST /api/job/{id}/gaps/refresh
# ===========================================================================


@pytest.mark.asyncio
async def test_seam_gaps_refresh_router_publishes_the_denial_lowered_score(db):
    from applire.routers.job import refresh_gap_analysis

    user, job, profile = await _seed(db)
    provider = MockLLMProvider()

    before = await analyze_gaps(job.id, db, provider)
    assert before.match_score == pytest.approx(1.0)

    set_profile_json(profile, _with_denial(_profile_json()))
    await db.commit()

    # The real router function, called the way FastAPI calls it (explicit
    # arguments — a Depends default leaks into a direct call otherwise).
    after = await refresh_gap_analysis(job.id, db=db, provider=provider, _auth=None)

    assert after.match_score == pytest.approx(0.5), (
        "/gaps/refresh must publish the denial-lowered score (a fresh denial "
        "always stands in the merge)"
    )
    assert after.match_score == pytest.approx(
        _headline_from_breakdown(after.requirement_breakdown)
    )
    assert {b.requirement: b.status for b in after.requirement_breakdown} == {
        "Python": "direct",
        "Docker": "denied",
    }
    # …and the row the next screen reads is the same one.
    assert (await _latest(db, job.id)).match_score == pytest.approx(0.5)


# ===========================================================================
# Call site 2 — interview completion (services/session.py, #240)
# ===========================================================================


async def _seed_one_answer_session(db, job, profile, pre_row):
    flow = FlowSession(
        user_id=_STUB_USER_ID,
        job_id=job.id,
        current_step="interview",
        user_type="new",
        available_actions={"next": "cv_generation"},
        gap_analysis_id=pre_row.id,
    )
    record = InterviewSession(
        job_analysis_id=job.id,
        gap_analysis_id=pre_row.id,
        profile_id=profile.id,
        mode="targeted",
        status="active",
        state={
            "mode": "targeted",
            "job_id": str(job.id),
            "gap_analysis_id": str(pre_row.id),
            "profile_id": str(profile.id),
            "critical_gaps": ["Docker"],
            "gap_categories": {"Docker": "C"},
            "addressed_gaps": [],
            "current_gap_index": 0,
            "current_question": "Tell me about your Docker work.",
            "messages": [
                {"role": "assistant", "content": "Tell me about your Docker work."}
            ],
            "questions_asked": 1,
            "hard_ceiling": 12,
            "questions_per_gap": {},
            "skipped_gaps": [],
            "full_gaps": [],
        },
        hard_ceiling=12,
        questions_asked=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add_all([flow, record])
    await db.commit()
    await db.refresh(record)
    return flow, record


def _denial_turn():
    from applire.schemas.profile import FieldChange
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    # What the reconciler returns for the candidate's denial: the same
    # `metadata.denied_concepts` write the real interview turn persists.
    return InterviewTurnResult(
        profile_dict=_with_denial(_profile_json()),
        changes=[
            FieldChange(
                section="metadata",
                field="denied_concepts",
                action="added",
                new_value="Docker",
            )
        ],
        addressed=True,
        conflict_summaries=[],
    )


_ANSWER = "I have never run Docker myself — that was a colleague's work."


@pytest.mark.asyncio
async def test_seam_interview_completion_publishes_the_denial_lowered_score(db):
    """The founder-UAT path: the denial is recorded IN the interview, and the
    completion recompute is what the gaps page and the CV workspace then read."""
    from applire.services.session import send_message

    user, job, profile = await _seed(db)
    provider = MockLLMProvider()

    pre = await analyze_gaps(job.id, db, provider)
    assert pre.match_score == pytest.approx(1.0)
    pre_row = await _latest(db, job.id)
    flow, record = await _seed_one_answer_session(db, job, profile, pre_row)
    turn = _denial_turn()

    # A FAITHFUL double: the real `reconcile_interview_turn` PERSISTS the
    # reconciled profile itself (it is handed `db` and the profile record) and
    # then returns the turn. A double that only returns leaves the vault
    # unchanged, the fingerprint identical, and the recompute reuses its row —
    # i.e. it would prove nothing about the merge.
    async def _reconcile_and_persist(*_args, **_kwargs):
        set_profile_json(profile, _with_denial(_profile_json()))
        await db.commit()
        return turn

    with patch(
        "applire.services.session.reconcile_interview_turn",
        new=AsyncMock(side_effect=_reconcile_and_persist),
    ):
        result = await send_message(record.id, _ANSWER, db, provider)

    assert result.complete is True

    delivered = await _latest(db, job.id)
    assert delivered.id != pre_row.id, "the completion recompute inserted a new row"
    assert delivered.match_score == pytest.approx(0.5), (
        "completing an interview in which the candidate denied a requirement must "
        "lower the score the gaps page and the CV workspace read"
    )
    assert delivered.match_score == pytest.approx(
        _headline_from_breakdown(delivered.requirement_breakdown)
    )
    flow_after = (
        await db.execute(select(FlowSession).where(FlowSession.id == flow.id))
    ).scalar_one()
    assert flow_after.gap_analysis_id == delivered.id, (
        "#240 — the flow FK follows the recompute, so the lowered score is what "
        "every screen reads"
    )


@pytest.mark.asyncio
async def test_completion_reaches_analyze_gaps_with_an_answer_scope(db):
    """`_complete_session`'s recompute is best-effort (try/except): a call with
    the wrong signature raises a swallowed TypeError and the recompute silently
    never happens. So assert the CALL — once, answer-driven, carrying this
    session's answer — not merely that nothing raised (ADR-089 clause 5)."""
    import applire.services.session as session_service
    from applire.services.session import send_message

    user, job, profile = await _seed(db)
    provider = MockLLMProvider()
    await analyze_gaps(job.id, db, provider)
    pre_row = await _latest(db, job.id)
    _flow, record = await _seed_one_answer_session(db, job, profile, pre_row)
    turn = _denial_turn()

    async def _reconcile_and_persist(*_args, **_kwargs):
        set_profile_json(profile, _with_denial(_profile_json()))
        await db.commit()
        return turn

    calls: list[dict] = []
    real = session_service.analyze_gaps

    async def _spy(job_id, db_, provider_, **kwargs):
        calls.append(kwargs)
        return await real(job_id, db_, provider_, **kwargs)

    with patch(
        "applire.services.session.reconcile_interview_turn",
        new=AsyncMock(side_effect=_reconcile_and_persist),
    ), patch.object(session_service, "analyze_gaps", new=_spy):
        result = await send_message(record.id, _ANSWER, db, provider)

    assert result.complete is True
    scoped = [k for k in calls if isinstance(k.get("answer_scope"), AnswerScope)]
    assert scoped, (
        f"_complete_session never called analyze_gaps with an AnswerScope (calls: {calls!r})"
    )
    assert any(_ANSWER in k["answer_scope"].answers for k in scoped), (
        "the completion scope must carry this session's answers (a self-correction "
        "outside the answered cluster must be able to lower that requirement)"
    )
