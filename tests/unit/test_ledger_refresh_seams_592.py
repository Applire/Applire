# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#592 — one named seam test per call site of ``refresh_ledger_against_vault``.

The refresh is a shared helper applied at N call sites, so the verification
hierarchy asks for one named test per SITE driving the real service path — not
just the helper's own unit tests. The two sites are the CV chain's and the letter
chain's ``_latest_keyword_ledger``, which every generation and every ATS-report
read now funnels through (the two previously-inline duplicate queries were
collapsed onto them, ADR-066 clause 2).

Revert either site independently and exactly its own test goes red.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# The vault as it stands NOW: it carries the term the persisted ledger forbids.
# Verbatim from the captured 2026-08-25 Anna-Bauer CV-writer prompt.
_VAULT_NOW = {
    "personal_info": {"name": "Anna Bauer", "email": "anna@example.com"},
    "work_experience": [
        {
            "id": "w1",
            "company": "StartupX AG",
            "position": "Backend Engineer",
            "responsibilities": [
                "Built and maintained REST APIs in FastAPI serving 2 million-plus daily requests."
            ],
            "achievements": [],
        }
    ],
    "skills": [],
    "metadata": {"denied_concepts": []},
}

# The ledger as it was PERSISTED, when those work entries carried no bullets.
_STALE_LEDGER = [
    {
        "concept": "REST APIs",
        "surface_forms": ["REST APIs"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    },
    {
        "concept": "GraphQL",
        "surface_forms": ["GraphQL"],
        "sources": ["nice_to_have"],
        "fit_weight": 0.5,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    },
]


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="ledger-refresh-592@test.com")
    db.add(user)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="ledgerrefresh592",
        raw_text="Senior Backend Engineer",
        role_title="Senior Backend Engineer",
        company_name="Vector Analytics",
        required_skills=["REST APIs"],
        nice_to_have_skills=["GraphQL"],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="en",
    )
    db.add(job)
    profile = make_master_profile(profile_json=_VAULT_NOW)
    db.add(profile)
    await db.flush()
    db.add(
        GapAnalysis(
            job_analysis_id=job.id,
            profile_id=profile.id,
            keyword_ledger=[dict(row) for row in _STALE_LEDGER],
        )
    )
    await db.commit()
    return db, job, profile


@pytest.mark.asyncio
async def test_cv_latest_keyword_ledger_seam_refreshes_against_the_current_vault(seeded):
    """Seam 1 — ``services/cv.py::_latest_keyword_ledger``."""
    db, job, _profile = seeded
    from applire.services.cv import _latest_keyword_ledger

    ledger = await _latest_keyword_ledger(db, job.id)

    rows = {e["concept"]: e for e in ledger or []}
    assert rows["REST APIs"]["status"] == "direct", "the stale gap row was not refreshed"
    assert rows["REST APIs"]["claimable"] is True
    assert "REST APIs" in rows["REST APIs"]["evidence"]
    assert rows["GraphQL"]["status"] == "gap", "a genuine gap must stay forbidden"


@pytest.mark.asyncio
async def test_letter_latest_keyword_ledger_seam_refreshes_against_the_current_vault(seeded):
    """Seam 2 — ``services/cover_letter.py::_latest_keyword_ledger``."""
    db, job, _profile = seeded
    from applire.services.cover_letter import _latest_keyword_ledger

    ledger = await _latest_keyword_ledger(db, job.id)

    rows = {e["concept"]: e for e in ledger or []}
    assert rows["REST APIs"]["status"] == "direct", "the stale gap row was not refreshed"
    assert rows["REST APIs"]["claimable"] is True
    assert rows["GraphQL"]["status"] == "gap"


@pytest.mark.asyncio
async def test_cv_seam_accepts_a_caller_supplied_profile_without_a_second_query(seeded):
    """The generation path already holds the profile; passing it must give the
    same answer as letting the seam load it (one implementation, two entries)."""
    db, job, profile = seeded
    from applire.services.cv import _latest_keyword_ledger

    passed = await _latest_keyword_ledger(db, job.id, profile_json=profile.profile_json)
    loaded = await _latest_keyword_ledger(db, job.id)

    assert passed == loaded


@pytest.mark.asyncio
async def test_seam_returns_none_for_a_job_with_no_gap_analysis(seeded):
    db, _job, _profile = seeded
    from applire.services.cv import _latest_keyword_ledger

    assert await _latest_keyword_ledger(db, uuid.uuid4()) is None
