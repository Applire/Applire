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

"""E037 PQ #3 — match-score stability.

The deterministic score (fit_weight x status-factor) wobbled 99->98->97 for a
single application because every screen re-POSTed gap analysis and _run_analysis
ALWAYS re-ran the LLM (a fresh per-requirement classification) and inserted a new
gap_analyses row. Different screens then read different rows.

Fix: analyze_gaps is idempotent per (job, profile-fingerprint) — it reuses the
latest row instead of re-running the LLM when inputs are unchanged. The
/gaps/refresh path (``AnswerScope()``) merges every requirement with its
previous row (ADR-089 clause 5): added evidence never lowers a requirement,
a denial always can. The per-rule merge tests live in
``test_gap_answer_merge.py``; this file keeps the idempotency contract and the
denial/JD-change arms at the ``analyze_gaps`` level.
"""

import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.flow import FlowSession
from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.models.user import User
from applire.providers.llm.mock import MockLLMProvider
from applire.services.gap import analyze_gaps
from applire.services.gap_coverage import AnswerScope

from tests.support.profile_factory import make_master_profile, set_profile_json

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000099")


class _SpyProvider(MockLLMProvider):
    """MockLLMProvider that counts aparse_json calls (LLM invocations)."""

    def __init__(self) -> None:
        self.parse_calls = 0

    async def aparse_json(self, prompt, **kwargs):  # type: ignore[override]
        self.parse_calls += 1
        return await super().aparse_json(prompt, **kwargs)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user           # noqa: F401
    import applire.models.job            # noqa: F401
    import applire.models.profile        # noqa: F401
    import applire.models.gap            # noqa: F401
    import applire.models.cv             # noqa: F401
    import applire.models.cover_letter   # noqa: F401
    import applire.models.session        # noqa: F401
    import applire.models.flow           # noqa: F401
    import applire.models.application     # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company        # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.uploads        # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _profile_json() -> dict:
    return {
        "work_experience": [
            {"company": "Acme", "role": "Dev", "start_date": "2018-01"}
        ],
        "education": [
            {"institution": "TU Berlin", "degree": "BSc", "field": "CS"}
        ],
        "skills": [
            {"name": "Python", "category": "technical", "proficiency": "expert"}
        ],
        "languages": [{"language": "German", "level": "native"}],
        "personal_info": {
            "first_name": "Max",
            "last_name": "Muster",
            "email": "max@test.de",
        },
        "professional_summary": {"de": "Entwickler", "en": "Developer"},
        "certifications": [],
        "publications": [],
        "volunteer_activities": [],
    }


@pytest_asyncio.fixture
async def seeded(db):
    """Seed user + job + profile + a flow (no gap analysis yet)."""
    user = User(
        id=_STUB_USER_ID,
        email="local@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="hash-idem",
        raw_text="Senior Python Engineer",
        role_title="Senior Python Engineer",
        required_skills=["Python", "FastAPI"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )
    profile = make_master_profile(id=uuid.uuid4(), profile_json=_profile_json())
    db.add_all([user, job, profile])
    await db.commit()

    flow = FlowSession(
        user_id=user.id,
        job_id=job.id,
        current_step="gap_analysis",
        user_type="new",
        available_actions={"next": "interview", "skip": "cv_generation"},
    )
    db.add(flow)
    await db.commit()
    await db.refresh(flow)
    return job, profile, flow


async def _count_rows(db, job_id) -> int:
    rows = (
        await db.execute(
            select(GapAnalysis).where(GapAnalysis.job_analysis_id == job_id)
        )
    ).scalars().all()
    return len(rows)


@pytest.mark.asyncio
async def test_second_analyze_reuses_row_without_calling_llm(db, seeded):
    job, profile, flow = seeded
    spy = _SpyProvider()

    r1 = await analyze_gaps(job.id, db, spy)
    calls_after_first = spy.parse_calls
    assert calls_after_first > 0, "first run must invoke the LLM"
    assert await _count_rows(db, job.id) == 1

    r2 = await analyze_gaps(job.id, db, spy)

    # Same (job, profile) → SAME row, no new insert, no extra LLM call.
    assert r2.id == r1.id, "unchanged inputs must reuse the existing analysis row"
    assert spy.parse_calls == calls_after_first, "reuse must NOT call the LLM again"
    assert await _count_rows(db, job.id) == 1, "no duplicate gap_analyses row"
    assert r2.match_score == r1.match_score, "same inputs → same score"


@pytest.mark.asyncio
async def test_changed_profile_fingerprint_recomputes(db, seeded):
    job, profile, flow = seeded
    spy = _SpyProvider()

    r1 = await analyze_gaps(job.id, db, spy)
    calls_after_first = spy.parse_calls

    # Genuinely change the profile content (e.g. interview enrichment).
    new_json = _profile_json()
    new_json["skills"].append(
        {"name": "FastAPI", "category": "technical", "proficiency": "advanced"}
    )
    set_profile_json(profile, new_json)
    await db.commit()

    r2 = await analyze_gaps(job.id, db, spy)

    assert r2.id != r1.id, "a changed profile must produce a new analysis row"
    assert spy.parse_calls > calls_after_first, "changed inputs must re-run the LLM"
    assert await _count_rows(db, job.id) == 2


@pytest.mark.asyncio
async def test_flow_fk_and_latest_converge_on_reuse(db, seeded):
    job, profile, flow = seeded
    spy = _SpyProvider()

    r1 = await analyze_gaps(job.id, db, spy)
    r2 = await analyze_gaps(job.id, db, spy)  # idempotent reuse

    latest = (
        await db.execute(
            select(GapAnalysis)
            .where(GapAnalysis.job_analysis_id == job.id)
            .order_by(desc(GapAnalysis.created_at))
            .limit(1)
        )
    ).scalar_one()
    refreshed_flow = (
        await db.execute(select(FlowSession).where(FlowSession.id == flow.id))
    ).scalar_one()

    # The flow-pinned FK and the latest-by-created_at read path must be the SAME
    # row the analyze call returned — every screen reads one score.
    assert r2.id == latest.id
    assert refreshed_flow.gap_analysis_id == latest.id


_RANK = {"direct": 2, "partial": 1, "gap": 0, "denied": 0}


@pytest.mark.asyncio
async def test_refresh_never_lowers_a_requirement_it_did_not_touch(db, seeded):
    """ADR-089 clause 5 replaces the whole-slice clamp: the refresh publishes
    the headline its OWN merged table implies, and no requirement in it sits
    below where the previous row had it (nothing touched, nothing denied)."""
    job, profile, flow = seeded
    spy = _SpyProvider()

    r1 = await analyze_gaps(job.id, db, spy)

    # A genuine profile change so the refresh path recomputes (fingerprint differs).
    new_json = _profile_json()
    new_json["personal_info"]["headline"] = "changed"
    set_profile_json(profile, new_json)
    await db.commit()

    r2 = await analyze_gaps(job.id, db, spy, answer_scope=AnswerScope())

    assert r2.id != r1.id, "refresh with changed inputs creates a new row"
    assert r2.match_score is not None and r2.match_score >= r1.match_score
    before = {b.requirement: b.status for b in r1.requirement_breakdown}
    for b in r2.requirement_breakdown:
        if b.requirement in before:
            assert _RANK[b.status] >= _RANK[before[b.requirement]], b.requirement
    assert r2.match_score == pytest.approx(
        _headline_from_breakdown(r2.requirement_breakdown)
    ), "the headline is its own table's arithmetic — never a republished number"


# ===========================================================================
# #675 line 77 / UAT F-3 — the clamp's denial arm (ruling B-1, 2026-09-20)
# ===========================================================================
#
# The arm above (`test_refresh_clamps_score_monotonically_up`) exercises ADDED
# EVIDENCE and asserts the headline alone. It is the population the E037 PQ #3
# clamp was earned against and it stays green. What it never exercised is the
# population the product exists for: a recorded DENIAL. On the founder UAT of
# 2026-09-20 four requirements flipped to `denied` and the clamp republished the
# pre-interview headline (0.6404) above a freshly written table whose own
# arithmetic said 0.6292 — the headline contradicted its own explanation, and an
# honest denial could never lower the displayed score.


def _headline_from_breakdown(breakdown) -> float | None:
    """The headline the breakdown itself implies.

    `compute_match_score_from_ledger`'s formula, read off the persisted table:
    sum(earned) / sum(slot). Never a magic number — if the breakdown changes,
    the expected headline changes with it.
    """
    slots = 0.0
    earned = 0.0
    for item in breakdown or []:
        if isinstance(item, dict):
            slots += float(item.get("slot") or 0.0)
            earned += float(item.get("earned") or 0.0)
        else:
            slots += float(item.slot or 0.0)
            earned += float(item.earned or 0.0)
    if slots == 0.0:
        return None
    return earned / slots


def _statuses(response) -> dict[str, str]:
    return {b.requirement: b.status for b in (response.requirement_breakdown or [])}


async def _two_vault_backed_required_skills(db, job, profile) -> None:
    """Both JD requirements are held AND backed by the vault.

    Vault backing matters: #318's `assert_claimable_backed` heals a claimable
    ledger row with no vault evidence down to `gap`, so a requirement that is
    only in the mock's classification list scores 0 and could never demonstrate
    a denial-driven DROP.
    """
    new_json = _profile_json()
    new_json["skills"] = [
        {"name": "Python", "category": "technical", "proficiency": "expert"},
        {"name": "Docker", "category": "technical", "proficiency": "advanced"},
    ]
    set_profile_json(profile, new_json)
    job.required_skills = ["Python", "Docker"]
    job.keywords = ["Python"]
    await db.commit()
    return new_json


@pytest.mark.asyncio
async def test_refresh_after_a_denial_lowers_the_score_and_matches_its_breakdown(
    db, seeded
):
    """A denial recorded between two analyses must LOWER the published headline.

    And in both arms the headline must equal its own table's arithmetic — the
    assertion the pre-B-1 code had nowhere.
    """
    job, profile, flow = seeded
    spy = _SpyProvider()

    base_json = await _two_vault_backed_required_skills(db, job, profile)

    r1 = await analyze_gaps(job.id, db, spy)
    assert _statuses(r1) == {"Python": "direct", "Docker": "direct"}
    assert r1.match_score == pytest.approx(1.0)
    assert r1.match_score == pytest.approx(_headline_from_breakdown(r1.requirement_breakdown))

    # The interview persists the candidate's denial exactly here: a
    # `denied_concepts` entry on the profile metadata, which is the deterministic
    # floor build_keyword_ledger applies (#231 / ADR-064).
    denied_json = json.loads(json.dumps(base_json))
    denied_json.setdefault("metadata", {})["denied_concepts"] = [
        {
            "concept": "Docker",
            "denial_level": "direct",
            "statement": "I have never run Docker myself.",
        }
    ]
    set_profile_json(profile, denied_json)
    await db.commit()

    r2 = await analyze_gaps(job.id, db, spy, answer_scope=AnswerScope())

    assert r2.id != r1.id, "a recorded denial changes the fingerprint → new row"
    assert _statuses(r2) == {"Python": "direct", "Docker": "denied"}, (
        "the denial must reach the published table"
    )
    # THE defect: pre-B-1 this was 1.0 — the pre-denial headline above a table
    # that says 1.0/2.0.
    assert r2.match_score == pytest.approx(0.5), (
        "an honest denial must lower the displayed score (ruling B-1)"
    )
    assert r2.match_score < r1.match_score
    assert r2.match_score == pytest.approx(
        _headline_from_breakdown(r2.requirement_breakdown)
    ), "the headline must equal the arithmetic of its own requirement_breakdown"
    # The rest of the slice is this run's own too — no half-clamped row.
    assert "Docker" not in (r2.critical_gaps or []), (
        "a denied requirement enters no gap list (#383)"
    )


@pytest.mark.asyncio
async def test_a_widened_jd_is_a_fresh_analysis_not_a_merge(db, seeded):
    """A JD change is the non-answer path (ADR-089 clauses 4/5): the denominator
    grows, the headline drops with it, and it still equals its own table.

    (Before ADR-089 the whole-slice clamp republished the previous row here —
    a score that no longer described the posting.)
    """
    job, profile, flow = seeded
    spy = _SpyProvider()

    await _two_vault_backed_required_skills(db, job, profile)
    r1 = await analyze_gaps(job.id, db, spy)
    assert r1.match_score == pytest.approx(1.0)

    # A third requirement the candidate has no signal for.
    job.required_skills = ["Python", "Docker", "GraphQL"]
    await db.commit()

    r2 = await analyze_gaps(job.id, db, spy, answer_scope=AnswerScope())

    assert r2.id != r1.id
    assert {b.requirement: b.status for b in r2.requirement_breakdown}.get("GraphQL") == "gap"
    assert r2.match_score == pytest.approx(2.0 / 3.0)
    assert r2.match_score == pytest.approx(
        _headline_from_breakdown(r2.requirement_breakdown)
    ), "the headline equals its own table"
