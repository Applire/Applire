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

"""#222 — analyze_jd caller-supplied role_title/company_name overrides.

LinkedIn and most boards separate the title/company from the description body;
without an override the LLM infers a title from the body (a heading leaked into
the adesso letter subject in the 2026-07-21 edge UAT run). The overrides let the
caller pass the authoritative values alongside pasted text."""
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

import importlib
import pkgutil

import applire.models  # noqa: F401
from applire.db.session import Base
from applire.services import job as job_svc

# Import every model module so all tables/FKs are registered before create_all
# (models/__init__ doesn't pull in every table, e.g. generated_cover_letters).
for _m in pkgutil.iter_modules(applire.models.__path__):
    importlib.import_module(f"applire.models.{_m.name}")


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _disable_jd_review(monkeypatch):
    """#264: analyze_jd() now runs its draft through review_and_refine. These
    override tests use a single fixed mocked response and aren't about the
    review loop — disable it for a deterministic, single-shot call sequence."""
    monkeypatch.setattr("applire.services.job.LLM_REVIEW_MAX_RETRIES", 0)


class _EmbedFails:
    """Embedding provider stub → NULL embedding (no network in unit tests)."""

    async def embed(self, text: str):
        raise RuntimeError("no embeddings in unit tests")


def _provider(**data):
    p = AsyncMock()
    base = {
        "role_title": "Strategischer Aufbau AI-Plattformen",  # a body heading, not the title
        "company_name": "adesso",
        "required_skills": ["Python"],
        "nice_to_have_skills": [],
        "keywords": ["AI"],
        "seniority_level": "senior",
    }
    base.update(data)
    p.aparse_json = AsyncMock(return_value=base)
    return p


@pytest.mark.asyncio
async def test_overrides_replace_inferred_title_and_company(db):
    result = await job_svc.analyze_jd(
        "…job body with a heading the LLM would mistake for the title…",
        db,
        _provider(),
        embedding_provider=_EmbedFails(),
        role_title_override="Head of AI Platforms",
        company_name_override="adesso SE",
    )
    assert result.role_title == "Head of AI Platforms"
    assert result.company_name == "adesso SE"


@pytest.mark.asyncio
async def test_override_titles_a_skillful_but_titleless_jd(db):
    # A real JD whose body lists requirements but no explicit title line (the
    # LinkedIn case): the override supplies the title; the JD is valid.
    result = await job_svc.analyze_jd(
        "We need someone strong in Kubernetes and Go. Full details below…",
        db,
        _provider(role_title="", required_skills=["Kubernetes", "Go"], nice_to_have_skills=[]),
        embedding_provider=_EmbedFails(),
        role_title_override="Site Reliability Engineer",
    )
    assert result.role_title == "Site Reliability Engineer"


@pytest.mark.asyncio
async def test_override_does_not_rescue_non_jd_text(db):
    # No inferred title AND no requirements = garbage. An override must NOT turn
    # non-JD text into a valid JobAnalysis (the garbage detector is the only one).
    with pytest.raises(ValueError):
        await job_svc.analyze_jd(
            "asdf random text that is not a job posting",
            db,
            _provider(role_title="", required_skills=[], nice_to_have_skills=[], keywords=[]),
            embedding_provider=_EmbedFails(),
            role_title_override="Senior Engineer",
        )


@pytest.mark.asyncio
async def test_override_applies_on_cache_hit(db):
    # First pass (no override) persists the LLM-inferred (wrong) title.
    text = "adesso is hiring — Strategischer Aufbau AI-Plattformen. Python, ML."
    first = await job_svc.analyze_jd(
        text, db, _provider(), embedding_provider=_EmbedFails()
    )
    assert first.role_title == "Strategischer Aufbau AI-Plattformen"
    # Re-analysing the SAME text with the authoritative title must not silently
    # return the stale cached title — the override is applied to the cached row.
    corrected = await job_svc.analyze_jd(
        text,
        db,
        _provider(),
        embedding_provider=_EmbedFails(),
        role_title_override="Head of AI Platforms",
        company_name_override="adesso SE",
    )
    assert corrected.role_title == "Head of AI Platforms"
    assert corrected.company_name == "adesso SE"
    assert corrected.id == first.id  # same cached record, corrected in place


@pytest.mark.asyncio
async def test_blank_override_falls_back_to_inferred(db):
    result = await job_svc.analyze_jd(
        "some jd body",
        db,
        _provider(role_title="Data Engineer", company_name="Acme"),
        embedding_provider=_EmbedFails(),
        role_title_override="   ",
        company_name_override="",
    )
    assert result.role_title == "Data Engineer"
    assert result.company_name == "Acme"
