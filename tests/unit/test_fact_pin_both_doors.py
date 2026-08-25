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

"""SF-PIN.5 (ADR-077 clause 7) — the both-doors test.

A section-override edit that removes the pin's carrier from the actually
delivered render is never blocked and never silent: `_update_ats_report` —
the ONE implementation the generation door, the section-editor re-audit door
and the agent door all call — measures presence against the OVERRIDE-APPLIED
content, so the persisted report flips the pin to unmet.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.application import FactPin
from applire.schemas.cv import TailoredCVData

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ACHIEVEMENT = "Cut deployment time by 70% across 12 teams"


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user        # noqa: F401
    import applire.models.job         # noqa: F401
    import applire.models.profile     # noqa: F401
    import applire.models.gap         # noqa: F401
    import applire.models.cv          # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session     # noqa: F401
    import applire.models.flow        # noqa: F401
    import applire.models.uploads     # noqa: F401
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
async def scene(db):
    from applire.models.application import Application
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile, authorized_profile_write
    from applire.schemas.profile import MasterProfileData, WorkEntry
    from applire.services.cv_section_editor import build_content_snapshot

    profile_data = MasterProfileData(
        work_experience=[
            WorkEntry(role="Lead", company="Acme", achievements=[ACHIEVEMENT])
        ]
    )
    entry_id = profile_data.work_experience[0].id
    with authorized_profile_write():
        profile = MasterProfile(profile_json=profile_data.model_dump(mode="json"))
        db.add(profile)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"hash-{uuid.uuid4()}",
        raw_text="JD",
        role_title="Lead",
        company_name="X",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
    )
    db.add(job)
    await db.flush()

    pin = FactPin(entry_type="work", entry_id=entry_id, quote=ACHIEVEMENT)
    application = Application(
        user_id=_STUB_USER_ID,
        job_analysis_id=job.id,
        pinned_facts=[pin.model_dump(mode="json")],
    )
    db.add(application)

    tailored = TailoredCVData.model_validate({
        "contact": {"name": "X"},
        "work_history": [{
            "id": entry_id, "company": "Acme", "role": "Lead",
            "start_date": "2020-01", "bullets": [f"Delivered: {ACHIEVEMENT}"],
        }],
        "skills": [],
    })
    snapshot = build_content_snapshot(tailored)
    record = GeneratedCV(
        id=uuid.uuid4(),
        job_analysis_id=job.id,
        profile_id=profile.id,
        tailored_data=tailored.model_dump(),
        content_snapshot=snapshot,
        template="classic_german",
        status="ready",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(days=90),
    )
    db.add(record)
    await db.flush()
    return {"record": record, "snapshot": snapshot, "pin": pin}


def _measured():
    from applire.services.cv import MeasuredRender

    return MeasuredRender(
        text="rendered text", page_count=2, condensation_exhausted=False,
        target=2, region="DACH",
    )


@pytest.mark.asyncio
async def test_report_shows_the_pin_met_without_overrides(db, scene):
    from applire.services.cv import _update_ats_report

    await _update_ats_report(scene["record"], db, measured=_measured(), commit=False)
    entries = scene["record"].ats_report["pinned_facts"]
    assert len(entries) == 1 and entries[0]["present"] is True


@pytest.mark.asyncio
async def test_override_removing_the_carrier_flips_the_report_to_unmet(db, scene):
    from applire.services.cv import _update_ats_report

    position_id = scene["snapshot"]["positions"][0]["id"]
    scene["record"].section_overrides = {
        f"position::{position_id}": "A hand-edited bullet without the fact"
    }
    await db.flush()
    await _update_ats_report(scene["record"], db, measured=_measured(), commit=False)
    entries = scene["record"].ats_report["pinned_facts"]
    assert len(entries) == 1 and entries[0]["present"] is False
