# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""E043/US246 — truthfulness report persistence and REST exposure (ADR-052 §4).

The self-audit rides the SAME commit as the ATS report ("ready implies report
available"), never raises, never blocks delivery, and every persisted report
embeds the ADR-052 §5 stated limit.
"""
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


def _stub_profile_json() -> dict:
    return {
        "professional_summary": {"de": "", "en": "Experienced Python developer."},
        "work_experience": [
            {
                "company": "Acme GmbH",
                "role": "Software Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "responsibilities": ["Backend development with Python"],
                "achievements": [
                    "Rollout of a compliance workflow that targets a ~70% "
                    "reduction in manual effort."
                ],
            }
        ],
        "skills": [{"name": "Python", "category": "technical"}],
    }


def _stub_tailored_data() -> dict:
    return {
        "contact": {"name": "Max Mustermann", "email": "max@example.com"},
        # bug class #1: the aspirational vault target rendered as achieved
        "summary": "Reduced manual effort by 70%.",
        "work_history": [
            {
                "company": "Acme GmbH",
                "role": "Software Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "bullets": ["Backend development with Python"],
            }
        ],
        "skills": ["Python", "React Native"],
    }


def _stub_letter_data() -> dict:
    return {
        "header": {"name": "Max Mustermann"},
        "body": {"paragraphs": ["I reduced manual effort by 70% at Acme."]},
        "signature": {"closing": "Mit freundlichen Grüßen"},
    }


def _make_ats_report(document: str = "cv"):
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport

    return ATSReport(
        document=document,
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1,
        failed=0,
    )


def _make_truthfulness_report_dict() -> dict:
    from applire.schemas.oracle import (
        Claim,
        ClaimResult,
        ClaimVerdict,
        TruthfulnessReport,
    )

    report = TruthfulnessReport.from_results(
        "cv",
        [
            ClaimResult(
                claim=Claim(text="Python", location="skills[0]", kind="skill"),
                verdict=ClaimVerdict(verdict="grounded", checker="grounding"),
            )
        ],
    )
    return report.model_dump(mode="json")


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    """User → Job → Profile → ready GeneratedCV + GeneratedCoverLetter."""
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.user import User

    ids = {
        "job_id": uuid.uuid4(),
        "profile_id": uuid.uuid4(),
        "cv_id": uuid.uuid4(),
        "cl_id": uuid.uuid4(),
    }
    db.add_all(
        [
            User(
                id=uuid.uuid4(),
                email="oracle-test@applire.community",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            JobAnalysis(
                id=ids["job_id"],
                raw_text_hash="oracle123",
                raw_text="Python developer job",
                role_title="Python Developer",
                required_skills=["Python"],
                nice_to_have_skills=[],
                keywords=["Python"],
                seniority_level="mid",
                company_culture_signals=[],
                language_requirement="de",
            ),
            MasterProfile(
                id=ids["profile_id"],
                profile_json=_stub_profile_json(),
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            GeneratedCV(
                id=ids["cv_id"],
                job_analysis_id=ids["job_id"],
                profile_id=ids["profile_id"],
                tailored_data=_stub_tailored_data(),
                template="classic_german",
                status="ready",
                content_snapshot=None,
                section_overrides=None,
                ats_report=None,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            ),
            GeneratedCoverLetter(
                id=ids["cl_id"],
                job_analysis_id=ids["job_id"],
                profile_id=ids["profile_id"],
                template="classic_german",
                letter_data=_stub_letter_data(),
                pre_gen_inputs={},
                status=CoverLetterStatus.ready.value,
                section_overrides=None,
                ats_report=None,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            ),
        ]
    )
    await db.commit()
    return {"db": db, **ids}


# ---------------------------------------------------------------------------
# Hook: _update_ats_report writes the truthfulness report in the same commit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cv_audit_hook_persists_truthfulness_report(seeded):
    from applire.models.cv import GeneratedCV
    from applire.schemas.oracle import ORACLE_STATED_LIMIT
    from applire.services.cv import _update_ats_report

    session = seeded["db"]
    record = await session.get(GeneratedCV, seeded["cv_id"])

    with patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 1)), \
         patch("applire.services.ats_audit._audit_cv_text", return_value=_make_ats_report("cv")):
        await _update_ats_report(record, session)

    session.expire_all()
    record = await session.get(GeneratedCV, seeded["cv_id"])
    report = record.truthfulness_report
    assert report is not None, "truthfulness_report must be persisted with the audit commit"
    assert report["stated_limit"] == ORACLE_STATED_LIMIT  # ADR-052 §5, verbatim
    assert report["document_kind"] == "cv"
    by_loc = {c["claim"]["location"]: c["verdict"]["verdict"] for c in report["claims"]}
    # the seeded inflation trap is caught by the self-audit
    assert by_loc["summary[0]"] == "inflated"
    assert by_loc["skills[1]"] == "unbacked"  # React Native not in the vault
    assert record.status == "ready"


@pytest.mark.asyncio
async def test_cv_truthfulness_failure_is_nonfatal(seeded):
    """An oracle crash leaves truthfulness_report NULL; ATS report + commit stand."""
    from applire.models.cv import GeneratedCV
    from applire.services.cv import _update_ats_report

    session = seeded["db"]
    record = await session.get(GeneratedCV, seeded["cv_id"])

    with patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"%PDF")), \
         patch("applire.services.ats_audit.extract_text_and_pages", return_value=("text", 1)), \
         patch("applire.services.ats_audit._audit_cv_text", return_value=_make_ats_report("cv")), \
         patch(
             "applire.services.oracle.selfaudit.audit_document",
             side_effect=RuntimeError("oracle boom"),
         ):
        await _update_ats_report(record, session)

    session.expire_all()
    record = await session.get(GeneratedCV, seeded["cv_id"])
    assert record.truthfulness_report is None
    assert record.ats_report is not None, "ATS report must still be persisted"
    assert record.status == "ready"


@pytest.mark.asyncio
async def test_letter_audit_hook_persists_truthfulness_report(seeded):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.schemas.oracle import ORACLE_STATED_LIMIT
    from applire.services.cover_letter import _update_ats_report_letter

    session = seeded["db"]
    cl = await session.get(GeneratedCoverLetter, seeded["cl_id"])

    with patch(
        "applire.services.ats_audit.audit_cover_letter",
        return_value=_make_ats_report("cover_letter"),
    ):
        await _update_ats_report_letter(cl, session, pdf=b"%PDF")

    session.expire_all()
    cl = await session.get(GeneratedCoverLetter, seeded["cl_id"])
    report = cl.truthfulness_report
    assert report is not None
    assert report["document_kind"] == "cover_letter"
    assert report["stated_limit"] == ORACLE_STATED_LIMIT
    # the letter's 70% claim is the same inflation trap
    verdicts = [c["verdict"]["verdict"] for c in report["claims"]]
    assert "inflated" in verdicts


# ---------------------------------------------------------------------------
# Getters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_cv_truthfulness_report_roundtrip(seeded):
    from applire.models.cv import GeneratedCV
    from applire.services.cv import get_cv_truthfulness_report

    session = seeded["db"]
    cv_id = seeded["cv_id"]

    # NULL column → report None
    response = await get_cv_truthfulness_report(cv_id, session)
    assert response.document_id == cv_id
    assert response.report is None
    assert response.status == "ready"

    # persisted → round-trips
    record = await session.get(GeneratedCV, cv_id)
    record.truthfulness_report = _make_truthfulness_report_dict()
    await session.commit()
    response = await get_cv_truthfulness_report(cv_id, session)
    assert response.report is not None
    assert response.report.counts["grounded"] == 1

    # malformed → degrades to None, never raises
    record.truthfulness_report = {"claims": "not-a-list", "counts": 7}
    await session.commit()
    response = await get_cv_truthfulness_report(cv_id, session)
    assert response.report is None

    with pytest.raises(LookupError):
        await get_cv_truthfulness_report(uuid.uuid4(), session)


@pytest.mark.asyncio
async def test_get_cover_letter_truthfulness_report_roundtrip(seeded):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import get_cover_letter_truthfulness_report

    session = seeded["db"]
    cl_id = seeded["cl_id"]

    response = await get_cover_letter_truthfulness_report(cl_id, session)
    assert response.report is None

    cl = await session.get(GeneratedCoverLetter, cl_id)
    cl.truthfulness_report = _make_truthfulness_report_dict()
    await session.commit()
    response = await get_cover_letter_truthfulness_report(cl_id, session)
    assert response.report is not None

    with pytest.raises(LookupError):
        await get_cover_letter_truthfulness_report(uuid.uuid4(), session)


# ---------------------------------------------------------------------------
# Routers — GET /api/cv/{id}/truthfulness-report + cover-letter twin
# ---------------------------------------------------------------------------

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from applire.auth import get_auth_provider
from applire.auth.no_auth import NoAuthProvider
from applire.db.session import get_db


@pytest_asyncio.fixture
async def client(seeded):
    from applire.routers.cover_letter import router as cl_router
    from applire.routers.cv import router as cv_router

    _app = FastAPI()
    _app.include_router(cv_router)
    _app.include_router(cl_router)
    _app.dependency_overrides[get_db] = lambda: seeded["db"]
    _app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()

    transport = ASGITransport(app=_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, seeded


@pytest.mark.asyncio
async def test_router_cv_truthfulness_report(client):
    ac, ctx = client
    session = ctx["db"]
    from applire.models.cv import GeneratedCV

    # NULL → 200 with report null
    resp = await ac.get(f"/api/cv/{ctx['cv_id']}/truthfulness-report")
    assert resp.status_code == 200, resp.text
    assert resp.json()["report"] is None

    record = await session.get(GeneratedCV, ctx["cv_id"])
    record.truthfulness_report = _make_truthfulness_report_dict()
    await session.commit()
    resp = await ac.get(f"/api/cv/{ctx['cv_id']}/truthfulness-report")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report"]["document_kind"] == "cv"
    assert body["report"]["stated_limit"]

    resp = await ac.get(f"/api/cv/{uuid.uuid4()}/truthfulness-report")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_router_cl_truthfulness_report(client):
    ac, ctx = client
    session = ctx["db"]
    from applire.models.cover_letter import GeneratedCoverLetter

    resp = await ac.get(f"/api/cover-letter/{ctx['cl_id']}/truthfulness-report")
    assert resp.status_code == 200, resp.text
    assert resp.json()["report"] is None

    cl = await session.get(GeneratedCoverLetter, ctx["cl_id"])
    cl.truthfulness_report = _make_truthfulness_report_dict()
    await session.commit()
    resp = await ac.get(f"/api/cover-letter/{ctx['cl_id']}/truthfulness-report")
    assert resp.status_code == 200, resp.text
    assert resp.json()["report"] is not None

    resp = await ac.get(f"/api/cover-letter/{uuid.uuid4()}/truthfulness-report")
    assert resp.status_code == 404
