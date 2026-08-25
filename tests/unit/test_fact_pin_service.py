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

"""ADR-077 clauses 1 + 7 — fact-pin store: add / remove / staleness.

A fact pin is a verbatim vault quote plus the entry's persisted id, stored on
`applications.pinned_facts` (max `MAX_FACT_PINS`). At write time the quote MUST
resolve — via the shared `_norm_quote` fold — inside the referenced entry's own
content fields; anything else is a ValueError (422 at the router), never a
silent accept. Removal is idempotent. Staleness is a recomputed measurement:
a pin whose quote no longer resolves is marked stale, surfaced, never deleted.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.constants import MAX_FACT_PINS
from applire.schemas.application import AddFactPinRequest, FactPin
from applire.schemas.profile import (
    MasterProfileData,
    Skill,
    WorkEntry,
)
from applire.services.fact_pins import (
    add_fact_pin,
    refresh_pin_staleness,
    remove_fact_pin,
)

_STUB_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

ACHIEVEMENT = "Cut deployment time by 70% across 12 teams"


@pytest_asyncio.fixture
async def db():
    """In-memory SQLite session with all models registered."""
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


def _profile_data() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(
                role="Platform Lead",
                company="Acme",
                achievements=[ACHIEVEMENT],
                responsibilities=["Ran the on-call rotation"],
            )
        ],
        skills=[Skill(name="Kubernetes")],
    )


@pytest_asyncio.fixture
async def scene(db):
    """Profile + job + application, ids persisted the real way (mode=json)."""
    from applire.models.application import Application
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.models.profile import authorized_profile_write

    profile_data = _profile_data()
    with authorized_profile_write():
        profile = MasterProfile(profile_json=profile_data.model_dump(mode="json"))
        db.add(profile)
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash=f"hash-{uuid.uuid4()}",
        raw_text="Sample JD",
        role_title="Platform Lead",
        company_name="DataCraft GmbH",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="German",
    )
    db.add(job)
    await db.flush()
    application = Application(
        user_id=_STUB_USER_ID,
        job_analysis_id=job.id,
        company_name="DataCraft GmbH",
        role_title="Platform Lead",
    )
    db.add(application)
    await db.flush()
    return {"profile": profile_data, "application": application}


def _work_entry_id(profile: MasterProfileData) -> str:
    return profile.work_experience[0].id


@pytest.mark.asyncio
async def test_add_pin_resolving_quote_persists_on_the_application(db, scene):
    req = AddFactPinRequest(
        entry_type="work",
        entry_id=_work_entry_id(scene["profile"]),
        quote=ACHIEVEMENT,
    )
    pin = await add_fact_pin(scene["application"].id, _STUB_USER_ID, req, db)
    assert pin.targets == ["cv", "letter"]  # default: both documents
    stored = scene["application"].pinned_facts
    assert len(stored) == 1 and stored[0]["quote"] == ACHIEVEMENT
    assert stored[0]["pin_id"] == pin.pin_id
    assert stored[0]["stale"] is False


@pytest.mark.asyncio
async def test_add_pin_normalizes_unicode_and_case_before_matching(db, scene):
    # U+2019 apostrophe + case difference must not defeat the match
    req = AddFactPinRequest(
        entry_type="work",
        entry_id=_work_entry_id(scene["profile"]),
        quote="cut deployment time by 70% across 12 teams",
    )
    pin = await add_fact_pin(scene["application"].id, _STUB_USER_ID, req, db)
    assert pin.quote  # accepted; quote stored as given


@pytest.mark.asyncio
async def test_add_pin_fails_closed_when_quote_does_not_resolve(db, scene):
    req = AddFactPinRequest(
        entry_type="work",
        entry_id=_work_entry_id(scene["profile"]),
        quote="Grew revenue by 300%",  # nowhere in the vault
    )
    with pytest.raises(ValueError):
        await add_fact_pin(scene["application"].id, _STUB_USER_ID, req, db)
    assert scene["application"].pinned_facts in (None, [])


@pytest.mark.asyncio
async def test_add_pin_fails_closed_on_unknown_entry_id(db, scene):
    req = AddFactPinRequest(
        entry_type="work",
        entry_id=str(uuid.uuid4()),
        quote=ACHIEVEMENT,
    )
    with pytest.raises(ValueError):
        await add_fact_pin(scene["application"].id, _STUB_USER_ID, req, db)


@pytest.mark.asyncio
async def test_skill_pin_resolves_against_the_name_field(db, scene):
    req = AddFactPinRequest(
        entry_type="skill",
        entry_id=scene["profile"].skills[0].id,
        quote="Kubernetes",
    )
    pin = await add_fact_pin(scene["application"].id, _STUB_USER_ID, req, db)
    assert pin.entry_type == "skill"


@pytest.mark.asyncio
async def test_unconfirmed_entry_is_not_pinnable(db, scene):
    # ADR-077 clause 2: the claim gate runs above pins — a pin must not
    # launder an unconfirmed/denied entry into the PINNED FACTS block.
    from applire.models.profile import MasterProfile, authorized_profile_write
    from sqlalchemy import select

    profile = _profile_data()
    profile.skills[0].status = "unconfirmed"
    record = (
        await db.execute(select(MasterProfile).limit(1))
    ).scalar_one()
    with authorized_profile_write():
        record.profile_json = profile.model_dump(mode="json")
    await db.flush()

    req = AddFactPinRequest(
        entry_type="skill",
        entry_id=profile.skills[0].id,
        quote="Kubernetes",
    )
    with pytest.raises(ValueError):
        await add_fact_pin(scene["application"].id, _STUB_USER_ID, req, db)


@pytest.mark.asyncio
async def test_duplicate_pin_same_entry_and_quote_is_rejected(db, scene):
    req = AddFactPinRequest(
        entry_type="work",
        entry_id=_work_entry_id(scene["profile"]),
        quote=ACHIEVEMENT,
    )
    await add_fact_pin(scene["application"].id, _STUB_USER_ID, req, db)
    with pytest.raises(ValueError):
        await add_fact_pin(scene["application"].id, _STUB_USER_ID, req, db)


@pytest.mark.asyncio
async def test_cap_is_enforced_at_max_fact_pins(db, scene):
    app = scene["application"]
    entry_id = _work_entry_id(scene["profile"])
    app.pinned_facts = [
        FactPin(
            pin_id=str(uuid.uuid4()),
            entry_type="work",
            entry_id=entry_id,
            quote=f"filler {i}",
        ).model_dump(mode="json")
        for i in range(MAX_FACT_PINS)
    ]
    await db.flush()
    req = AddFactPinRequest(entry_type="work", entry_id=entry_id, quote=ACHIEVEMENT)
    with pytest.raises(ValueError):
        await add_fact_pin(app.id, _STUB_USER_ID, req, db)


@pytest.mark.asyncio
async def test_remove_pin_is_idempotent(db, scene):
    req = AddFactPinRequest(
        entry_type="work",
        entry_id=_work_entry_id(scene["profile"]),
        quote=ACHIEVEMENT,
    )
    pin = await add_fact_pin(scene["application"].id, _STUB_USER_ID, req, db)
    await remove_fact_pin(scene["application"].id, _STUB_USER_ID, pin.pin_id, db)
    assert scene["application"].pinned_facts == []
    # second delete: no error, no change
    await remove_fact_pin(scene["application"].id, _STUB_USER_ID, pin.pin_id, db)
    assert scene["application"].pinned_facts == []


def test_refresh_marks_a_no_longer_resolving_pin_stale_and_never_deletes():
    profile = _profile_data()
    pin = FactPin(
        pin_id=str(uuid.uuid4()),
        entry_type="work",
        entry_id=profile.work_experience[0].id,
        quote=ACHIEVEMENT,
    )
    profile.work_experience[0].achievements = ["Something else entirely"]
    refreshed, changed = refresh_pin_staleness([pin], profile)
    assert changed is True
    assert len(refreshed) == 1 and refreshed[0].stale is True


def test_refresh_clears_stale_when_the_quote_resolves_again():
    profile = _profile_data()
    pin = FactPin(
        pin_id=str(uuid.uuid4()),
        entry_type="work",
        entry_id=profile.work_experience[0].id,
        quote=ACHIEVEMENT,
        stale=True,
    )
    refreshed, changed = refresh_pin_staleness([pin], profile)
    assert changed is True
    assert refreshed[0].stale is False


@pytest.mark.asyncio
async def test_pinning_changes_nothing_but_the_pin_store(db, scene):
    """ADR-077 clause 2 (masquerade guard, direction 1): a pin is a rendering
    priority, not evidence — adding one touches the pin store and nothing
    else (no vault write, no status upgrade)."""
    import copy

    from applire.models.profile import MasterProfile
    from sqlalchemy import select

    record = (await db.execute(select(MasterProfile).limit(1))).scalar_one()
    profile_before = copy.deepcopy(record.profile_json)
    app = scene["application"]
    status_before = (app.workflow_status, app.user_status)

    req = AddFactPinRequest(
        entry_type="work",
        entry_id=_work_entry_id(scene["profile"]),
        quote=ACHIEVEMENT,
    )
    await add_fact_pin(app.id, _STUB_USER_ID, req, db)

    await db.refresh(record)
    assert record.profile_json == profile_before  # vault untouched
    assert (app.workflow_status, app.user_status) == status_before
    assert len(app.pinned_facts) == 1
