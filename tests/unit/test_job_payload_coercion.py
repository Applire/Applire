# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Charter run #8: `analyze_jd` must coerce the LLM payload to its column types.

The run-8 crash, verbatim from `backend/logs/llm/2026-07-28.jsonl`: the
`job_analysis` review loop exhausted all five retries (it has never converged on
this JD — runs 6, 7 and 8 all ran to exhaustion), and the fifth corrector round
returned

    "language_requirement": {"Deutsch": "sehr gut", "Englisch": "gut"}

where every earlier round returned a string. `language_requirement` is a `Text`
column, and the service's `data.get("language_requirement") or ""` guards
EMPTINESS, not TYPE — a dict is truthy, so it reached the ORM and the endpoint
returned 500. `POST /api/job/analyze` is the first call of the whole journey, so
the failure is total: nothing can be imported, analysed, or generated.

Run 7 shipped an equally unreviewed fifth-round draft and merely happened to get a
string. That is the point of these tests: the bug is not one bad field, it is that
an unconverged review loop is free to drift the payload's SHAPE and nothing stood
between it and the schema.

No Docker, no real LLM — SQLite in-memory + stub provider.
"""

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture(autouse=True)
def _disable_jd_review(monkeypatch):
    """These tests are about the coercion boundary, not the review loop."""
    monkeypatch.setattr("applire.services.job.LLM_REVIEW_MAX_RETRIES", 0)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user           # noqa: F401
    import applire.models.job            # noqa: F401
    import applire.models.profile        # noqa: F401
    import applire.models.gap            # noqa: F401
    import applire.models.cv             # noqa: F401
    import applire.models.session        # noqa: F401
    import applire.models.flow           # noqa: F401
    import applire.models.uploads        # noqa: F401
    import applire.models.application    # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company        # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter   # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _payload(**overrides):
    base = {
        "company_name": "Rheinwerk Verpackungen GmbH",
        "role_title": "Leiter Operations (m/w/d)",
        "required_skills": ["5S", "SMED", "MES"],
        "nice_to_have_skills": ["IFS"],
        "keywords": ["Mehrschichtbetrieb"],
        "seniority_level": "Leitung",
        "company_culture_signals": ["kurze Wege"],
        "language_requirement": "Deutsch sehr gut",
        "berufsbild_code": None,
        "berufsbild_label": None,
    }
    base.update(overrides)
    return base


async def _run(db, payload):
    from applire.services.job import analyze_jd

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(return_value=payload)
    return await analyze_jd(
        "Leiter Operations bei Rheinwerk Verpackungen GmbH. Lean, MES, SAP.", db, provider
    )


@pytest.mark.asyncio
async def test_run8_dict_language_requirement_no_longer_crashes(db):
    """The exact run-8 payload. Both languages must survive as text — trading the
    crash for silent data loss would be the wrong fix."""
    result = await _run(
        db, _payload(language_requirement={"Deutsch": "sehr gut", "Englisch": "gut"})
    )
    assert isinstance(result.language_requirement, str)
    assert "Deutsch: sehr gut" in result.language_requirement
    assert "Englisch: gut" in result.language_requirement


@pytest.mark.asyncio
async def test_a_list_valued_text_field_is_flattened(db):
    result = await _run(db, _payload(language_requirement=["Deutsch (C2)", "Englisch (B2)"]))
    assert result.language_requirement == "Deutsch (C2), Englisch (B2)"


@pytest.mark.asyncio
async def test_a_dict_valued_list_field_keeps_the_concepts(db):
    """`{"5S": "required"}` — the KEYS are the concepts; the values are the model
    editorialising. Losing the keys would silently empty the keyword ledger."""
    result = await _run(
        db, _payload(required_skills={"5S": "required", "SMED": "required", "MES": "core"})
    )
    assert result.required_skills == ["5S", "SMED", "MES"]


@pytest.mark.asyncio
async def test_a_scalar_valued_list_field_becomes_a_single_entry_list(db):
    result = await _run(db, _payload(nice_to_have_skills="IFS"))
    assert result.nice_to_have_skills == ["IFS"]


@pytest.mark.asyncio
async def test_non_string_list_entries_are_stringified_not_dropped(db):
    result = await _run(db, _payload(keywords=["Schicht", 120, None]))
    assert result.keywords == ["Schicht", "120", "None"]


@pytest.mark.asyncio
async def test_a_clean_payload_is_untouched(db):
    """The coercion must be a no-op on well-shaped output — it is a boundary, not
    a transformer."""
    clean = _payload()
    result = await _run(db, clean)
    assert result.language_requirement == clean["language_requirement"]
    assert result.required_skills == clean["required_skills"]
    assert result.nice_to_have_skills == clean["nice_to_have_skills"]
    assert result.keywords == clean["keywords"]
    assert result.company_culture_signals == clean["company_culture_signals"]
    assert result.seniority_level == clean["seniority_level"]


@pytest.mark.asyncio
async def test_coercion_is_logged_because_it_is_evidence_of_loop_drift(db, caplog):
    """A silent coercion would hide the fact that the review loop drifted the
    payload's shape — the coercion is a symptom, and the symptom must stay visible."""
    import logging

    with caplog.at_level(logging.WARNING, logger="applire.services.job"):
        await _run(db, _payload(language_requirement={"Deutsch": "sehr gut"}))
    assert any("shape drift coerced" in r.getMessage() for r in caplog.records)
    assert any("language_requirement(dict→str)" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_garbage_check_still_fires_on_non_jd_text(db):
    """The coercion runs BEFORE the garbage check reads the same fields; it must
    not accidentally manufacture content that rescues non-JD text into a 200."""
    with pytest.raises(ValueError, match="does not appear to be a job description"):
        await _run(
            db,
            _payload(
                role_title="", required_skills=[], nice_to_have_skills=[], keywords=[]
            ),
        )
