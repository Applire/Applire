# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Interview collector #787 line 28 — a JD analysis says how its review settled.

Founder's self-hosted 0.42.0b0 (2026-09-16): the `job_analysis` reviewer hit
its verdict budget (2048 → 4096 → `LLMTruncatedError`), `review_and_refine`
settled on `reviewer_call_failed` ("shipping current draft un-reviewed"), and
the analysis every later step reads went out with nothing on
`JobAnalysisResponse` saying so. The response now carries `review`, projected
from the loop's own `on_settle` report by the same function the document
chains use (`settle_to_outcome`): `pass` / `fail` / `not_applicable` (unknown).
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse: the MCP door signs links

_VALID = {
    "company_name": "Acme GmbH",
    "role_title": "Senior Backend Engineer",
    "required_skills": ["Python", "PostgreSQL"],
    "nice_to_have_skills": ["AWS"],
    "keywords": ["backend"],
    "seniority_level": "Senior",
    "company_culture_signals": [],
    "language_requirement": "German (C1)",
    "berufsbild_code": None,
    "berufsbild_label": None,
}
_JD = "Senior Backend Engineer at Acme GmbH. Python and PostgreSQL required, AWS a plus."


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _provider(*review_steps):
    """First aparse_json = the extraction; each later call pops a review step —
    a dict is returned, an exception instance is raised."""
    steps = list(review_steps)
    seen = {"n": 0}

    async def _dispatch(*args, **kwargs):
        seen["n"] += 1
        if seen["n"] == 1:
            return dict(_VALID)
        step = steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_dispatch)
    return provider


@pytest.mark.asyncio
async def test_an_approved_analysis_reports_pass(db):
    from applire.services.job import analyze_jd

    result = await analyze_jd(_JD, db, _provider({"approved": True, "issues": [], "feedback": ""}))
    assert result.review is not None
    assert result.review.status == "pass"
    assert result.review.settle_path == "approved"
    assert result.review.open_findings == 0


@pytest.mark.asyncio
async def test_the_incident_shape_a_failed_reviewer_call_reports_unknown_not_clean(db):
    """The 2026-09-16 shape: the reviewer call itself fails (truncated verdict)."""
    from applire.exceptions import LLMTruncatedError
    from applire.services.job import analyze_jd

    provider = _provider(
        LLMTruncatedError("verdict truncated"), LLMTruncatedError("verdict truncated"),
        LLMTruncatedError("verdict truncated"), LLMTruncatedError("verdict truncated"),
    )
    result = await analyze_jd(_JD, db, provider)
    assert result.review is not None
    assert result.review.status == "not_applicable", result.review
    assert result.review.settle_path == "reviewer_call_failed"


@pytest.mark.asyncio
async def test_a_cache_hit_reports_no_review_rather_than_a_clean_one(db):
    """The shared posting row stores no review state; the second caller is told
    "not recorded" (None), never handed the first call's verdict as its own."""
    from applire.services.job import analyze_jd

    await analyze_jd(_JD, db, _provider({"approved": True, "issues": [], "feedback": ""}))
    again = await analyze_jd(_JD, db, _provider())
    assert again.review is None


@pytest.mark.asyncio
async def test_the_agent_door_carries_the_review_object(db):
    from applire.mcp.server import mcp


    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch(
            "applire.mcp.server.get_provider",
            return_value=_provider({"approved": True, "issues": [], "feedback": ""}),
        ),
    ):
        out = await mcp.call_tool("analyze_jd", {"text": _JD})
    blocks = out[0] if isinstance(out, tuple) else out
    payload = json.loads(blocks[0].text)
    assert payload["review"]["status"] == "pass"
    # Counts only — the review object is not in the job-posting field map.
    assert not any(f.startswith("review") for f in payload["untrusted_content"]["fields"])
