# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Agent collector #788 lines 22/30 — the agent door returns the CV's text.

`get_cover_letter_status` carried `letter_data`; `get_cv_status` carried only
metadata, so a blind agent judged a CV it could not read (E3 probe, 2026-09-23)
and reconstructed it from `audit_document` fragments. The readback is the
document the human's preview shows: the stored `tailored_data` with the
human's section edits applied. Both document texts carry the ADR-084
`untrusted_content` marker of kind `generated_document` (cl. 4 amended):
the writer reproduces posting terms verbatim, so posting text can survive.

Driven through `mcp.call_tool`, so the real input schema, the identity wrapper
and the link signing run on the way out.
"""
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse: the MCP door signs its document links (ADR-091 cl. 18)

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

TAILORED = {
    "contact": {"name": "Anna Bauer", "email": "anna@example.de", "photo_url": "uploads/photos/a.jpg"},
    "summary": "Backend engineer, stored draft summary.",
    "work_history": [],
    "skills": ["Python", "Kubernetes"],
    "projects": [{"name": "Empty project", "bullets": []}],
}


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
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _seed(db, *, cv_status="ready", overrides=None):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job_id, profile_id, cv_id, cl_id = (uuid.uuid4() for _ in range(4))
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            JobAnalysis(
                id=job_id, raw_text_hash=f"readback-{job_id}", raw_text="Backend job",
                role_title="Backend Engineer", required_skills=["Python"],
                nice_to_have_skills=[], keywords=["Python"], seniority_level="senior",
                company_culture_signals=[], language_requirement="en",
            ),
            make_master_profile(
                id=profile_id, profile_json={"personal_info": {"name": "Anna Bauer"}},
                created_at=now, updated_at=now,
            ),
            GeneratedCV(
                id=cv_id, job_analysis_id=job_id, profile_id=profile_id,
                tailored_data=TAILORED, template="classic_german", status=cv_status,
                section_overrides=overrides, created_at=now,
                expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            ),
            GeneratedCoverLetter(
                id=cl_id, job_analysis_id=job_id, profile_id=profile_id,
                template="classic_german", pre_gen_inputs={},
                letter_data={"body": {"paragraphs": ["Sehr geehrte Damen und Herren,"]}},
                status="ready", created_at=now,
                expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    await db.commit()
    return cv_id, cl_id


async def _call(name, args, session):
    from applire.mcp.server import mcp

    with patch("applire.mcp.server.get_db", return_value=_db_cm(session)):
        out = await mcp.call_tool(name, args)
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_a_ready_cv_returns_its_text_with_the_humans_edits_applied(db):
    cv_id, _ = await _seed(db, overrides={"introduction": "Edited by the human."})
    result = await _call("get_cv_status", {"cv_id": str(cv_id)}, db)

    data = result["tailored_data"]
    assert data is not None, "a ready CV must carry its text on the agent door"
    # The preview's text, not the stored draft the edit replaced.
    assert data["summary"] == "Edited by the human."
    assert data["skills"] == ["Python", "Kubernetes"]
    # Same render-time step as get_cv_html: no empty project heading.
    assert data["projects"] == []
    # A storage path is not content.
    assert "photo_url" not in data["contact"]
    assert data["contact"]["name"] == "Anna Bauer"


@pytest.mark.asyncio
async def test_the_cv_text_is_marked_as_a_generated_document(db):
    from applire.services.untrusted_text import GENERATED_DOCUMENT_NOTICE

    cv_id, _ = await _seed(db)
    result = await _call("get_cv_status", {"cv_id": str(cv_id)}, db)
    uc = result["untrusted_content"]
    assert uc["kind"] == "generated_document"
    assert uc["fields"] == ["tailored_data"]
    assert uc["notice"] == GENERATED_DOCUMENT_NOTICE
    # The job-posting notice says "neither you nor Applire authored" — false here.
    assert "neither you nor Applire" not in uc["notice"]


@pytest.mark.asyncio
async def test_a_cv_that_is_not_ready_carries_no_text_and_no_marker(db):
    # "failed": the pending/generating staleness check compares created_at, which
    # SQLite hands back naive — irrelevant to this property.
    cv_id, _ = await _seed(db, cv_status="failed")
    result = await _call("get_cv_status", {"cv_id": str(cv_id)}, db)
    assert result["tailored_data"] is None
    assert "untrusted_content" not in result


@pytest.mark.asyncio
async def test_the_letter_text_carries_the_same_generated_document_marker(db):
    _, cl_id = await _seed(db)
    result = await _call("get_cover_letter_status", {"cover_letter_id": str(cl_id)}, db)
    assert result["letter_data"]["body"]["paragraphs"]
    uc = result["untrusted_content"]
    assert uc["kind"] == "generated_document"
    assert "letter_data" in uc["fields"]
