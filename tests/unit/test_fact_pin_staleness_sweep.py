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

"""ADR-077 clause 7 / SF-PIN.4 — the committer's post-write pin sweep.

Every vault write through `commit_ops` re-verifies the fact pins immediately
(the `_refloor_persisted_denials` pattern): a pin whose quote no longer
resolves — or whose entry the candidate retracted — is marked stale on the
application row in the same transaction. Stale pins are excluded and
surfaced, never deleted.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.application import FactPin
from applire.schemas.profile import MasterProfileData, Skill
from applire.services.profile.commit import CommitProvenance, commit_ops
from applire.services.profile.reconcile.ops import DemoteSkill, UpsertSkill

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


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


def _provenance() -> CommitProvenance:
    return CommitProvenance(
        source="testimony",
        intake="testimony",
        session_id=str(uuid.uuid4()),
        actor="candidate",
    )


@pytest_asyncio.fixture
async def scene(db):
    """Persisted profile with a confirmed skill + application pinning it."""
    from applire.models.application import Application
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile, authorized_profile_write

    profile_data = MasterProfileData(skills=[Skill(name="Kubernetes")])
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
    pin = FactPin(
        entry_type="skill",
        entry_id=profile_data.skills[0].id,
        quote="Kubernetes",
    )
    application = Application(
        user_id=_STUB_USER_ID,
        job_analysis_id=job.id,
        pinned_facts=[pin.model_dump(mode="json")],
    )
    db.add(application)
    await db.flush()
    return {"application": application, "record": profile}


@pytest.mark.asyncio
async def test_demoting_a_pinned_skill_marks_the_pin_stale(db, scene):
    await commit_ops(
        db,
        [DemoteSkill(name="Kubernetes", declared_denial="kubernetes")],
        _provenance(),
        record=scene["record"],
    )
    await db.refresh(scene["application"])
    assert scene["application"].pinned_facts[0]["stale"] is True


@pytest.mark.asyncio
async def test_an_unrelated_write_leaves_the_pin_fresh(db, scene):
    await commit_ops(
        db,
        [UpsertSkill(name="Terraform", category="technical")],
        _provenance(),
        record=scene["record"],
    )
    await db.refresh(scene["application"])
    assert scene["application"].pinned_facts[0]["stale"] is False
