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

"""Wave-6 Task 3 wiring: analyze_jd() must run the settled JD-analysis draft
through the deterministic jd_shape_guard before it is persisted, so a
sentence-shaped duplicate the review loop failed to catch never reaches the
stored JobAnalysis row (and therefore never reaches build_keyword_ledger()).

No Docker, no real LLM — SQLite in-memory + stub provider, review loop
disabled (see test_job_service.py for that fixture's rationale).
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture(autouse=True)
def _disable_jd_review(monkeypatch):
    """This file is about the shape-guard call site, not the review loop
    itself (see test_review_job_analysis.py for that coverage)."""
    monkeypatch.setattr("applire.services.job.LLM_REVIEW_MAX_RETRIES", 0)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user          # noqa: F401
    import applire.models.job           # noqa: F401
    import applire.models.profile       # noqa: F401
    import applire.models.gap           # noqa: F401
    import applire.models.cv            # noqa: F401
    import applire.models.session       # noqa: F401
    import applire.models.flow          # noqa: F401
    import applire.models.uploads       # noqa: F401
    import applire.models.application   # noqa: F401
    import applire.models.color_profile # noqa: F401
    import applire.models.company       # noqa: F401
    import applire.models.user_settings # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_analyze_jd_drops_sentence_shaped_duplicate_before_persisting(db):
    from applire.services.job import analyze_jd

    response = {
        "company_name": "Acme GmbH",
        "role_title": "Senior AI Engineer",
        "required_skills": [
            "RAG pipelines",
            "Embeddings",
            "Production experience with RAG, embeddings and retrieval pipelines.",
        ],
        "nice_to_have_skills": ["Kubernetes"],
        "keywords": ["AI evaluation"],
        "seniority_level": "Senior",
        "company_culture_signals": [],
        "language_requirement": "German (C1)",
        "berufsbild_code": None,
        "berufsbild_label": None,
    }
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=response)

    result = await analyze_jd("Senior AI Engineer at Acme GmbH. RAG, embeddings.", db, provider)

    assert result.required_skills == ["RAG pipelines", "Embeddings"]
    assert not any(len(s.split()) > 6 for s in result.required_skills)


@pytest.mark.asyncio
async def test_analyze_jd_leaves_genuine_short_concepts_untouched(db):
    from applire.services.job import analyze_jd

    response = {
        "company_name": "Acme GmbH",
        "role_title": "Senior AI Engineer",
        "required_skills": ["Python", "RAG pipelines", "AI evaluation"],
        "nice_to_have_skills": ["Kubernetes"],
        "keywords": ["Retrieval systems"],
        "seniority_level": "Senior",
        "company_culture_signals": [],
        "language_requirement": "German (C1)",
        "berufsbild_code": None,
        "berufsbild_label": None,
    }
    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=response)

    result = await analyze_jd("Senior AI Engineer at Acme GmbH.", db, provider)

    assert result.required_skills == ["Python", "RAG pipelines", "AI evaluation"]
    assert result.keywords == ["Retrieval systems"]
