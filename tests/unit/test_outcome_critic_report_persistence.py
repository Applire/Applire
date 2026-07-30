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

"""ADR-060 Pass B (#322) — getter round-trip + REST/MCP door-parity surface.

Mirrors ``test_truthfulness_report_persistence.py``'s "Getters"/"Routers"
sections exactly, for the new ``critic_report`` column/endpoint/MCP tool.
The generation-path wiring itself (facts -> advisory -> persisted report) is
covered end to end in ``test_outcome_critic_integration.py``; this file pins
the READ surface: null-until-computed, malformed-degrades-to-None,
404-on-missing, and the REST + MCP tools resolving to the SAME service
function (ADR-066 clause 2 — one implementation, not two that happen to
agree).
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def _make_critic_report_dict() -> dict:
    from applire.schemas.outcome_critic import CriticAdvisory, OutcomeCriticReport

    report = OutcomeCriticReport(
        ran=True,
        reason=None,
        advisories=[
            CriticAdvisory(
                concept="ISO 9001",
                cv_state="Verantwortlich für ISO 9001 Zertifizierungsaudits.",
                letter_state="Mit zehn Jahren ISO-9001-Auditpraxis ...",
                changed=False,
                message={"de": "Ihr Anschreiben nennt ...", "en": "Your letter states ..."},
            )
        ],
    )
    return report.model_dump(mode="json")


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
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    cl_id = uuid.uuid4()
    db.add_all(
        [
            User(id=uuid.uuid4(), email="critic-rp@test.com"),
            JobAnalysis(
                id=(job_id := uuid.uuid4()),
                raw_text_hash="criticrp123",
                raw_text="Qualitätsmanager",
                role_title="Qualitätsmanager",
                required_skills=["ISO 9001"],
                nice_to_have_skills=[],
                keywords=["ISO 9001"],
                seniority_level="senior",
                company_culture_signals=[],
                language_requirement="de",
            ),
            (profile := MasterProfile(profile_json={})),
        ]
    )
    await db.flush()
    db.add(
        GeneratedCoverLetter(
            id=cl_id,
            job_analysis_id=job_id,
            profile_id=profile.id,
            template="classic_german",
            letter_data={},
            pre_gen_inputs={},
            status=CoverLetterStatus.ready.value,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )
    )
    await db.commit()
    return {"db": db, "cl_id": cl_id}


@pytest.mark.asyncio
async def test_get_cover_letter_critic_report_roundtrip(seeded):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import get_cover_letter_critic_report

    session = seeded["db"]
    cl_id = seeded["cl_id"]

    # NULL column -> report None, never a 500.
    response = await get_cover_letter_critic_report(cl_id, session)
    assert response.document_id == cl_id
    assert response.report is None
    assert response.status == "ready"

    # Persisted -> round-trips faithfully.
    cl = await session.get(GeneratedCoverLetter, cl_id)
    cl.critic_report = _make_critic_report_dict()
    await session.commit()
    response = await get_cover_letter_critic_report(cl_id, session)
    assert response.report is not None
    assert response.report.ran is True
    assert response.report.advisories[0].concept == "ISO 9001"
    assert response.report.advisories[0].changed is False

    # Malformed -> degrades to None, never raises (mirrors ATS/truthfulness).
    cl.critic_report = {"ran": "not-a-bool", "advisories": "also-not-a-list"}
    await session.commit()
    response = await get_cover_letter_critic_report(cl_id, session)
    assert response.report is None

    with pytest.raises(LookupError):
        await get_cover_letter_critic_report(uuid.uuid4(), session)


# ---------------------------------------------------------------------------
# Router — GET /api/cover-letter/{id}/critic-report
# ---------------------------------------------------------------------------

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from applire.auth import get_auth_provider
from applire.auth.no_auth import NoAuthProvider
from applire.db.session import get_db


@pytest_asyncio.fixture
async def client(seeded):
    from applire.routers.cover_letter import router as cl_router

    _app = FastAPI()
    _app.include_router(cl_router)
    _app.dependency_overrides[get_db] = lambda: seeded["db"]
    _app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()

    transport = ASGITransport(app=_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, seeded


@pytest.mark.asyncio
async def test_router_cl_critic_report(client):
    ac, ctx = client
    session = ctx["db"]
    from applire.models.cover_letter import GeneratedCoverLetter

    resp = await ac.get(f"/api/cover-letter/{ctx['cl_id']}/critic-report")
    assert resp.status_code == 200, resp.text
    assert resp.json()["report"] is None

    cl = await session.get(GeneratedCoverLetter, ctx["cl_id"])
    cl.critic_report = _make_critic_report_dict()
    await session.commit()
    resp = await ac.get(f"/api/cover-letter/{ctx['cl_id']}/critic-report")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report"]["advisories"][0]["concept"] == "ISO 9001"
    assert body["report"]["advisories"][0]["changed"] is False
    assert set(body["report"]["advisories"][0]["message"].keys()) == {"de", "en"}

    resp = await ac.get(f"/api/cover-letter/{uuid.uuid4()}/critic-report")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# MCP door parity — no new tool needed (ADR-056 §4 tool-surface budget):
# critic_report rides the EXISTING get_cover_letter_status poll, which the
# REST router's /status endpoint and the MCP get_cover_letter_status tool
# both call as the SAME function (ADR-066 clause 2 — one implementation).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_cover_letter_status_carries_the_critic_report(seeded):
    """SF-CRITIC.3 (door parity): the ONE function both the REST `/status`
    endpoint and the MCP `get_cover_letter_status` tool call must surface
    the persisted advisory — not just the dedicated getter tested above."""
    import applire.mcp.server as mcp_server
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services import cover_letter as cover_letter_svc

    # The MCP tool wraps cover_letter_svc.get_cover_letter_status directly —
    # no parallel query, no second read path to drift out of parity.
    assert (
        mcp_server.cover_letter_svc.get_cover_letter_status
        is cover_letter_svc.get_cover_letter_status
    )

    session = seeded["db"]
    cl = await session.get(GeneratedCoverLetter, seeded["cl_id"])
    cl.critic_report = _make_critic_report_dict()
    await session.commit()

    result = await cover_letter_svc.get_cover_letter_status(
        seeded["cl_id"], session, "http://localhost:8001"
    )
    dumped = result.model_dump(mode="json")
    assert dumped["critic_report"]["advisories"][0]["concept"] == "ISO 9001"
    assert dumped["critic_report"]["advisories"][0]["changed"] is False
