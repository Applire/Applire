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

"""ADR-080 / #646 — the question budget must suffice for the gap plan.

The property under test is NOT "the ceiling equals some formula" — a test
asserting the formula against itself would pass with the off-by-one that
motivated ADR-080 clause 2. It is the behavioural one:

    a session that works through every gap must COMPLETE with reason
    `gaps_resolved`, never `max_questions_reached`

driven through the real `create_session` / `send_message` loop, with the
reconciler doubled but the budget arithmetic, the per-gap counter, the advance
decision and the ceiling check all live. That is what the flat constant failed
and what `+1` also fails — see `test_terminal_headroom_of_one_is_not_enough`,
which pins the measurement that chose `+2`.

The worst answer pattern is the one where NO answer addresses its gap, so every
cluster costs its full `INTERVIEW_MAX_QUESTIONS_PER_GAP` allowance.
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile  # noqa: E402

from applire.constants import INTERVIEW_MAX_QUESTIONS_PER_GAP  # noqa: E402
from applire.services.interview.budget import derive_hard_ceiling  # noqa: E402

# ADR-029 targets 5-12 clusters per analysis; 3 covers the small end and 13 the
# case just past the documented maximum, so the property is pinned across and
# beyond the whole range the clustering step is instructed to produce.
CLUSTER_COUNTS = [1, 3, 5, 6, 8, 12, 13]


@pytest_asyncio.fixture
async def sqlite_session():
    from applire.db.session import Base  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _mock_provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="Tell me more about that.")
    provider.aparse_json = AsyncMock(
        return_value={"question": "Tell me more.", "choices": None, "approved": True}
    )
    provider.__class__.__name__ = "MockProvider"
    return provider


def _unaddressed_turn(profile_dict):
    """The worst realistic shape: an honest answer the reconciler finds nothing
    new to write for, so the gap consumes its full per-gap allowance (#274/#284)."""
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    return InterviewTurnResult(profile_dict=profile_dict, changes=[], addressed=False)


async def _seed(db, n_clusters):
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis

    profile = make_master_profile(
        profile_json={
            "personal_info": {"name": "Anna Bauer", "email": "anna@example.de"},
            "skills": [
                {"name": "Python", "category": "technical", "proficiency": "advanced"}
            ],
            "work_experience": [
                {"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}
            ],
        }
    )
    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Senior Python Engineer requiring GCP, FastAPI, Kubernetes.",
        role_title="Senior Python Engineer",
        required_skills=["Python", "GCP", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="English",
    )
    db.add_all([profile, job])
    await db.commit()
    await db.refresh(profile)
    await db.refresh(job)

    labels = [f"Gap {i + 1}" for i in range(n_clusters)]
    db.add(
        GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            match_score=0.6,
            critical_gaps=list(labels),
            minor_gaps=[],
            strengths=["Python"],
            keyword_gaps=[],
            category_a=[],
            category_b=[],
            category_c=list(labels),
            gap_clusters=[
                {
                    "id": f"cluster-{i}",
                    "label": label,
                    "category": "C",
                    "gaps": [label],
                    "jd_skills": [],
                    "jd_context": "",
                }
                for i, label in enumerate(labels)
            ],
            keyword_ledger=[],
        )
    )
    await db.commit()
    return job, profile


async def _walk_to_completion(db, job, profile, max_turns=200):
    """Drive a real targeted session to completion, answering every question with
    a turn that addresses nothing. Returns the terminal SessionMessageResponse."""
    from applire.models.profile import authorized_profile_write
    from applire.schemas.session import SessionCreateRequest
    from applire.services import session as S

    provider = _mock_provider()

    async def _reconcile(db_, *, profile_record, **_kw):
        turn = _unaddressed_turn(profile_record.profile_json)
        with authorized_profile_write():
            profile_record.profile_json = turn.profile_dict
        await db_.flush()
        return turn

    with patch(
        "applire.services.session.reconcile_interview_turn",
        new=AsyncMock(side_effect=_reconcile),
    ):
        created = await S.create_session(
            SessionCreateRequest(job_id=job.id), db, provider
        )
        for _ in range(max_turns):
            resp = await S.send_message(
                created.session_id, "I worked on that at Acme for two years.", db, provider
            )
            if resp.complete:
                return created, resp
    raise AssertionError("session never completed")


# ---------------------------------------------------------------------------
# The property — one case per plan size across ADR-029's whole target range
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("n_clusters", CLUSTER_COUNTS)
async def test_every_gap_is_asked_within_the_derived_budget(sqlite_session, n_clusters):
    """#646: a targeted interview must work through the plan it was given.

    Reverting `derive_hard_ceiling` to the pre-ADR-080 flat 12 reddens this at
    n >= 6 with `reason == "max_questions_reached"` and a non-empty
    `gaps_unresolved` — the founder-reported failure, reproduced.
    """
    job, profile = await _seed(sqlite_session, n_clusters)
    created, resp = await _walk_to_completion(sqlite_session, job, profile)

    assert created.gaps_total == n_clusters
    assert resp.reason == "gaps_resolved", (
        f"{n_clusters} clusters terminated on {resp.reason} with "
        f"{resp.gaps_unresolved} never asked (budget {created.hard_ceiling}, "
        f"{resp.questions_asked} questions spent)"
    )
    assert not resp.gaps_unresolved
    assert resp.gaps_resolved == n_clusters


@pytest.mark.asyncio
@pytest.mark.parametrize("n_clusters", [5, 8, 12])
async def test_terminal_headroom_of_one_is_not_enough(sqlite_session, n_clusters):
    """ADR-080 clause 2 — why the derivation ends in `+2` and not `+1`.

    This is the measurement that chose the constant, pinned so nobody 'tidies'
    it back. With one unit less, every gap still closes — and the session still
    reports that a limit stopped it, because `send_message` tests the ceiling
    BEFORE the turn's advance/sufficiency decision.
    """
    job, profile = await _seed(sqlite_session, n_clusters)

    one_short = INTERVIEW_MAX_QUESTIONS_PER_GAP * n_clusters + 1
    with patch(
        "applire.services.session.derive_hard_ceiling", return_value=one_short
    ):
        _created, resp = await _walk_to_completion(sqlite_session, job, profile)

    assert resp.gaps_resolved == n_clusters, "every gap should still have closed"
    assert not resp.gaps_unresolved
    assert resp.reason == "max_questions_reached", (
        "with only +1 of headroom the cost guard is expected to pre-empt the "
        "sufficiency verdict on the terminal turn — that is the whole reason "
        "the derivation adds 2"
    )


# ---------------------------------------------------------------------------
# The derivation itself — one implementation, every mode plan (ADR-080 cl. 3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", CLUSTER_COUNTS)
@pytest.mark.parametrize("per_gap", [1, 2, 3])
def test_budget_strictly_exceeds_the_worst_case_question_count(n, per_gap):
    """The arithmetic invariant behind the behavioural test above.

    A fully-worked interview lands on `questions_asked == 1 + per_gap * n`
    (the opening question, then each gap's full allowance), and the ceiling is
    tested with `>=`. So the budget must be STRICTLY greater than that.
    """
    worst_case_final_count = 1 + per_gap * n
    assert derive_hard_ceiling(n, per_gap=per_gap) > worst_case_final_count


def test_mode_b_derivation_reproduces_the_historical_guided_ceiling():
    """ADR-080 Context — the evidence that only the targeted plan was mis-set.

    `gap_detector_mode_b` returns 7 core sections plus up to 2 JD-signalled
    ones, and 20 (the pre-ADR-080 `INTERVIEW_HARD_CEILING_GUIDED`) is exactly
    the derivation for its maximum. If this ever stops holding, either the
    section list or the derivation moved and ADR-080's Context needs revisiting.
    """
    from applire.services.interview_graph import (
        _MODE_B_CORE_SECTIONS,
        _MODE_B_EXTENDED_SECTIONS,
    )

    max_sections = len(_MODE_B_CORE_SECTIONS) + len(_MODE_B_EXTENDED_SECTIONS)
    assert max_sections == 9
    assert derive_hard_ceiling(max_sections, per_gap=2) == 20


def test_operator_setting_is_a_cap_not_the_value():
    """ADR-080 clause 4. Below the derived budget it truncates (the operator's
    deliberate cost decision); above it, it never inflates the budget."""
    uncapped = derive_hard_ceiling(10, per_gap=2)
    assert uncapped == 22
    assert derive_hard_ceiling(10, per_gap=2, cap=15) == 15
    assert derive_hard_ceiling(10, per_gap=2, cap=99) == 22


def test_a_pathological_cap_still_leaves_room_for_one_question():
    """An operator setting of 1 (the #627 trap value) must not make a session
    complete on its own opening question before the candidate can answer it."""
    assert derive_hard_ceiling(5, cap=1) == 2
    assert derive_hard_ceiling(0) >= 2
