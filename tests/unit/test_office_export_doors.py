# Copyright (C) 2026 Tobias Rosenbaum
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

"""US298 (E057 task 1.5, ADR-058 cl.2 / ADR-066) — the office-export DOOR
TEST.

REST `GET /api/{cv,cover-letter}/{id}/docx` and the MCP `render_document`
tool (``format="docx"``) must produce the SAME content set for the SAME
document — the "one writer, both doors" invariant. Proven by rendering one
document through the MCP tool, then fetching THAT SAME id's `.docx` a second
time over the real ASGI stack, and comparing EXTRACTED TEXT.

Text, never raw bytes: a `.docx` is a zip container (OPC package) and is not
guaranteed byte-stable across two independent writes of identical content
(python-docx does not pin timestamps/compression settings) — the task brief
for this story says so explicitly, and `test_cv_docx_endpoint.py` (US296)
already established `extract_docx_text` as this repo's own way to assert on
`.docx` content rather than bytes.

arc42 §8.8 "Door Parity Testing" is specified and UNBUILT (ADR-079 clause 7,
epic spec E057 task 1.5 boundary). This file is this story's OWN door test —
it does not build that broader suite and claims no credit for it.
"""
import base64
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

AGENT_CV_CONTENT = {
    "contact": {"name": "Petra Lindqvist", "email": "petra@example.de", "location": "Hamburg"},
    "summary": "Backend-Entwicklerin mit Schwerpunkt verteilte Systeme.",
    "work_history": [
        {
            "company": "Nordwind Systeme GmbH",
            "role": "Senior Backend-Entwicklerin",
            "start_date": "2020-01",
            "end_date": None,
            "bullets": ["Leitung der Migration auf ein Microservice-Fundament."],
        }
    ],
    "skills": ["Python", "Kubernetes"],
    "show_photo": False,
}

AGENT_LETTER_CONTENT = {
    "header": {"name": "Petra Lindqvist", "address": "Hafenstraße 3, 20457 Hamburg"},
    "recipient": {"name": "Herr Bauer", "company": "Nordwind Systeme GmbH"},
    "body": {
        "paragraphs": [
            "Sehr geehrter Herr Bauer,",
            "Ich bewerbe mich hiermit.",
            "Mit freundlichen Grüßen",
        ]
    },
    "signature": {"name": "Petra Lindqvist"},
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


@pytest_asyncio.fixture
async def client(db):
    """The REST door: the real ASGI stack, both document routers mounted,
    sharing the SAME db session `render_document` (patched per-test below)
    will use — the parity assertion only means something if both doors
    read/write the SAME row."""
    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.cover_letter import router as cl_router
    from applire.routers.cv import router as cv_router

    app = FastAPI()
    app.include_router(cv_router)
    app.include_router(cl_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_auth_provider] = lambda: object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest_asyncio.fixture
async def seeded(db):
    """analyze_jd-equivalent state: one job, one profile — mirrors
    tests/unit/test_mcp_render_document.py's own `seeded` fixture."""
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job_id, profile_id = uuid.uuid4(), uuid.uuid4()
    db.add_all(
        [
            JobAnalysis(
                id=job_id,
                raw_text_hash="door-parity",
                raw_text="Senior Backend-Entwicklerin gesucht",
                role_title="Senior Backend-Entwicklerin",
                required_skills=["Python"],
                nice_to_have_skills=[],
                keywords=["Python"],
                seniority_level="senior",
                company_culture_signals=[],
                language_requirement="de",
            ),
            make_master_profile(
                id=profile_id,
                profile_json={"personal_info": {"name": "Petra Lindqvist"}},
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    await db.commit()
    return {"db": db, "job_id": job_id}


@pytest.mark.asyncio
async def test_cv_docx_door_parity(client, seeded):
    """The bytes render_document(format='docx') hands back and the bytes a
    direct GET .../docx fetches for the SAME cv_id extract to the SAME
    text — proof both doors ran the ONE writer (render_cv_docx via
    services.cv.get_cv_docx), not two independently-constructed documents."""
    from applire.mcp.server import render_document
    from applire.services.office_export.extract import extract_docx_text

    with (
        patch("applire.mcp.server.get_db", return_value=_db_cm(seeded["db"])),
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF")),
        patch(
            "applire.services.ats_audit.extract_text_and_pages",
            return_value=("Petra Lindqvist Python", 1),
        ),
    ):
        mcp_result = await render_document(
            document_kind="cv",
            content=dict(AGENT_CV_CONTENT),
            job_id=str(seeded["job_id"]),
            format="docx",
        )

    cv_id = mcp_result["document_id"]
    rest_response = await client.get(f"/api/cv/{cv_id}/docx")
    assert rest_response.status_code == 200

    mcp_text = extract_docx_text(base64.b64decode(mcp_result["docx_base64"]))
    rest_text = extract_docx_text(rest_response.content)

    assert mcp_text == rest_text
    # Not a vacuous match on two empty strings — both must actually carry
    # the candidate's own content.
    assert "Petra Lindqvist" in mcp_text
    assert "Leitung der Migration auf ein Microservice-Fundament." in mcp_text


@pytest.mark.asyncio
async def test_cover_letter_docx_door_parity(client, seeded):
    """Letter twin of the CV door-parity test above."""
    from applire.mcp.server import render_document
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport
    from applire.services.office_export.extract import extract_docx_text

    report = ATSReport(
        document="cover_letter",
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
    )
    with (
        patch("applire.mcp.server.get_db", return_value=_db_cm(seeded["db"])),
        patch(
            "applire.services.cover_letter_pdf.render_pdf",
            new=AsyncMock(return_value=b"%PDF"),
        ),
        patch("applire.services.ats_audit.audit_cover_letter", return_value=report),
    ):
        mcp_result = await render_document(
            document_kind="cover_letter",
            content=dict(AGENT_LETTER_CONTENT),
            job_id=str(seeded["job_id"]),
            format="docx",
        )

    cl_id = mcp_result["document_id"]
    rest_response = await client.get(f"/api/cover-letter/{cl_id}/docx")
    assert rest_response.status_code == 200

    mcp_text = extract_docx_text(base64.b64decode(mcp_result["docx_base64"]))
    rest_text = extract_docx_text(rest_response.content)

    assert mcp_text == rest_text
    assert "Petra Lindqvist" in mcp_text
    assert "Ich bewerbe mich hiermit." in mcp_text
