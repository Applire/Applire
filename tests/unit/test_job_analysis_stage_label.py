# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#617 / contract 2 (#538/#539 stage-label pattern) — the JD extractor call site.

`services/reviewer.py` labels the calls it makes (`set_llm_log_stage(chain_id)`),
but `analyze_jd`'s FIRST call — the one that produces the draft the whole loop
then argues about — fires before the loop starts and therefore inherits whatever
the contextvar already holds. Measured on the captured corpus: **1,528 of 1,531
extractor records carry no usable stage**, and the three that do carry it
inherited it from a previous invocation in the same task.

Both halves are asserted here, because only the second one is a real bug: an
unlabelled call is merely invisible, an INHERITED label is actively wrong — it
files a JD extraction under someone else's chain.
"""
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture(autouse=True)
def _disable_jd_review(monkeypatch):
    """This file is about the extractor call site, not the review loop (which
    labels its own calls and is covered by test_review_job_analysis.py)."""
    monkeypatch.setattr("applire.services.job.LLM_REVIEW_MAX_RETRIES", 0)


@pytest_asyncio.fixture
async def db():
    import importlib
    import pkgutil

    import applire.models  # noqa: F401

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


_DRAFT = {
    "company_name": "Rheinwerk Verpackungen GmbH",
    "role_title": "Leiter Operations",
    "required_skills": ["Instandhaltung"],
    "nice_to_have_skills": [],
    "keywords": ["Produktion"],
    "seniority_level": "Lead",
    "company_culture_signals": [],
    "language_requirement": "German (C1)",
    "berufsbild_code": None,
    "berufsbild_label": None,
}


def _capturing_provider(seen: list):
    """Records the call-site triple AT CALL TIME — reading it afterwards would
    read whatever the contextvar decayed to, not what the record would carry."""
    from applire.providers.llm.debug_log import current_call_site

    provider = AsyncMock()

    async def aparse_json(*_a, **_kw):
        seen.append(current_call_site())
        return dict(_DRAFT)

    provider.aparse_json = aparse_json
    return provider


@pytest.mark.asyncio
async def test_the_jd_extractor_call_is_labelled_job_analysis(db):
    from applire.services.job import analyze_jd

    seen: list = []
    await analyze_jd("Leiter Operations bei Rheinwerk. Instandhaltung.", db, _capturing_provider(seen))

    assert seen, "the extractor call never happened"
    stage, role, attempt = seen[0]
    assert stage == "job_analysis", f"extractor call logged under stage {stage!r}"
    # Outside the loop, so no role/attempt — those belong to reviewer.py.
    assert (role, attempt) == (None, None)


@pytest.mark.asyncio
async def test_the_jd_extractor_does_not_inherit_a_previous_chains_label(db):
    """The failure that matters. A JD analysis running after another chain in
    the same task used to be filed under that chain — `cv_terminal_review` in
    the #538 incident — so a per-chain count over the log is wrong in BOTH
    directions at once."""
    from applire.providers.llm.debug_log import set_stage
    from applire.services.job import analyze_jd

    set_stage("cv_terminal_review")
    seen: list = []
    await analyze_jd("Leiter Operations bei Rheinwerk. Anderer Text.", db, _capturing_provider(seen))

    assert seen[0][0] == "job_analysis", (
        f"the extractor inherited {seen[0][0]!r} from the previous chain"
    )
