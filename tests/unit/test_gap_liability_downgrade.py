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

"""#260 exit (b) — downgrade_keyword_liability(): the candidate's own choice
to DROP a keyword-liability concept (a JD hard requirement, claimable, but
with no narrative anywhere in the vault) rather than tell its story via
resolve_gap (exit a). Deterministic, no LLM: a real (in-memory sqlite) DB
fixture, no MockLLMProvider needed since no analysis is re-run.
"""

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.services.gap import downgrade_keyword_liability


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


_LIABILITY_LEDGER = [
    {
        "concept": "RAG",
        "surface_forms": ["RAG"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "direct",
        "evidence": "listed under Skills",
        "claimable": True,
        "narrative_backed": False,
    },
    {
        "concept": "Python",
        "surface_forms": ["Python"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "direct",
        "evidence": "5y building services",
        "claimable": True,
        "narrative_backed": True,
    },
]


@pytest_asyncio.fixture
async def seeded_gap_analysis(db):
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="hash-liability",
        raw_text="Senior ML Engineer",
        role_title="Senior ML Engineer",
        required_skills=["RAG", "Python"],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="DE",
    )
    # #318 / ADR-061: both ledger rows below are `claimable`, so the vault has
    # to be able to back both — a claimable row with no vault evidence is now
    # healed away at every persist seam, this one included. `RAG` keeps the
    # keyword-LIABILITY shape (a bare skills-list entry, no narrative anywhere:
    # `narrative_backed: False`); `Python` is the ordinary backed row this test
    # needs to stay untouched.
    profile = MasterProfile(
        id=uuid.uuid4(),
        profile_json={
            "skills": [{"name": "RAG"}, {"name": "Python"}],
            "work_experience": [
                {
                    "id": "w1",
                    "company": "Acme",
                    "position": "Engineer",
                    "responsibilities": ["Built backend services in Python for five years"],
                }
            ],
        },
    )
    db.add_all([job, profile])
    await db.commit()

    gap_analysis = GapAnalysis(
        job_analysis_id=job.id,
        profile_id=profile.id,
        match_score=1.0,
        input_fingerprint="fp-liability",
        critical_gaps=[],
        minor_gaps=[],
        strengths=["RAG", "Python"],
        keyword_gaps=[],
        category_a=["RAG", "Python"],
        category_b=[],
        category_c=[],
        keyword_ledger=[dict(e) for e in _LIABILITY_LEDGER],
        requirement_breakdown=[],
        created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    db.add(gap_analysis)
    await db.commit()
    await db.refresh(gap_analysis)
    return job, gap_analysis


@pytest.mark.asyncio
async def test_downgrade_flips_the_matching_entry_to_an_honest_gap(db, seeded_gap_analysis):
    job, gap_analysis = seeded_gap_analysis

    result = await downgrade_keyword_liability(job.id, "RAG", db)

    by_concept = {e.concept: e for e in result.keyword_ledger}
    assert by_concept["RAG"].claimable is False
    assert by_concept["RAG"].status == "gap"
    # Untouched sibling entry.
    assert by_concept["Python"].claimable is True
    assert by_concept["Python"].status == "direct"


@pytest.mark.asyncio
async def test_downgrade_recomputes_match_score_honestly_down(db, seeded_gap_analysis):
    """Dropping a claimed concept must never leave a stale, now-inflated
    score on the record — the same formula analyze_gaps uses re-derives it."""
    job, gap_analysis = seeded_gap_analysis
    assert gap_analysis.match_score == 1.0

    result = await downgrade_keyword_liability(job.id, "RAG", db)

    assert result.match_score is not None
    assert result.match_score < 1.0
    assert "RAG" in result.category_c
    assert "RAG" not in result.category_a


@pytest.mark.asyncio
async def test_downgrade_persists_to_the_same_row(db, seeded_gap_analysis):
    job, gap_analysis = seeded_gap_analysis
    original_id = gap_analysis.id

    result = await downgrade_keyword_liability(job.id, "RAG", db)

    assert result.id == original_id


@pytest.mark.asyncio
async def test_downgrade_no_match_is_a_noop(db, seeded_gap_analysis):
    job, gap_analysis = seeded_gap_analysis

    result = await downgrade_keyword_liability(job.id, "Nonexistent Concept", db)

    assert result.match_score == 1.0
    by_concept = {e.concept: e for e in result.keyword_ledger}
    assert by_concept["RAG"].claimable is True


@pytest.mark.asyncio
async def test_downgrade_no_gap_analysis_raises_lookup_error(db):
    with pytest.raises(LookupError):
        await downgrade_keyword_liability(uuid.uuid4(), "RAG", db)
