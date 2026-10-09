# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#702 — the derived ``cross_document`` reaches every door that returns a
critic report, on a LEGACY persisted row (stored before the field existed).

One seam test per call site: the letter status poll (REST ``/status`` and the
MCP ``get_cover_letter_status`` tool share it), the dedicated letter getter
(REST ``/critic-report``), and the CV status poll (Pass A: always empty).
"""
import json
from pathlib import Path

import pytest

import uuid
from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile


# Same in-memory seed as test_outcome_critic_report_persistence.py.
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
            (profile := make_master_profile(profile_json={})),
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



FIXTURE = Path(__file__).resolve().parents[1] / "files" / "review_signals" / "critic-report-2026-09-13-marcus.json"


def _legacy_raw() -> dict:
    raw = json.loads(FIXTURE.read_text())["report"]
    assert "cross_document" not in raw  # the fixture IS a pre-#702 row
    return raw


async def _store(seeded, raw):
    from applire.models.cover_letter import GeneratedCoverLetter

    session = seeded["db"]
    cl = await session.get(GeneratedCoverLetter, seeded["cl_id"])
    cl.critic_report = raw
    await session.commit()
    return session


@pytest.mark.asyncio
async def test_letter_status_door_derives_cross_document_on_a_legacy_row(seeded):
    from applire.services import cover_letter as svc

    session = await _store(seeded, _legacy_raw())
    result = await svc.get_cover_letter_status(seeded["cl_id"], session, "http://localhost:8001")
    report = result.model_dump(mode="json")["critic_report"]
    assert report["cross_document"][0]["weight"] == "high"
    assert "Sauberraumbereich seit 2021" in report["cross_document"][0]["concepts"]
    assert len(report["advisories"]) == 7  # the producer's population is untouched


@pytest.mark.asyncio
async def test_letter_status_door_keeps_a_malformed_blob_unchanged(seeded):
    from applire.services import cover_letter as svc

    session = await _store(seeded, {"ran": "not-a-bool-or-anything", "advisories": "nope"})
    result = await svc.get_cover_letter_status(seeded["cl_id"], session, "http://localhost:8001")
    assert result.model_dump(mode="json")["critic_report"]["advisories"] == "nope"


@pytest.mark.asyncio
async def test_letter_critic_report_getter_derives_cross_document(seeded):
    from applire.services import cover_letter as svc

    session = await _store(seeded, _legacy_raw())
    resp = await svc.get_cover_letter_critic_report(seeded["cl_id"], session)
    dumped = resp.model_dump(mode="json")
    assert dumped["report"]["cross_document"][0]["weight"] == "high"


def test_cv_status_door_routes_through_the_same_helper():
    """The CV status builder calls the shared door helper (Pass A has no letter,
    so its cross_document is always empty — the field still exists)."""
    import inspect

    from applire.services import cv as cv_svc

    src = inspect.getsource(cv_svc.get_cv_status)
    assert "_critic_report_for_door(record.critic_report)" in src
    from applire.services.outcome_critic import critic_report_for_door

    out = critic_report_for_door({"ran": True, "mount": "cv", "advisories": []})
    assert out["cross_document"] == []
