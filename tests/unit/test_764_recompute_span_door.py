# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#764 — the Health hub's recompute action survives the request and does what
the item says (ADR-061 cl. 7 amended 2026-10-16, ruling H-2).

Drives the real door (`routers/profile_health.recompute_span_endpoint`) against
a FILE-backed database and re-reads over a separate connection (the #480 PR 3
pattern: `commit_ops` flushes, the door commits — a forgotten commit is a
silent no-write). Pins: the span is re-derived and labelled `computed`; only
that one skill moves; the write is an ordinary receipted `manual_edit`; the
Health item is gone afterwards; a stale click is a 409 and writes nothing.
"""
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_SEED = {
    "personal_info": {"name": "Lena Brandt", "email": "lena@example.invalid"},
    "work_experience": [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "company": "Talwerk Feinmechanik GmbH",
            "role": "Controllerin",
            "start_date": "2019-02",
            "responsibilities": ["Monatsabschluss mit SAP CO und SAP FI verantwortet."],
        }
    ],
    "skills": [
        {"id": "s-co", "name": "SAP CO", "years_experience": 8, "source": "transcribed"},
        {"id": "s-py", "name": "Python", "years_experience": 3, "source": "transcribed",
         "proficiency": "advanced"},
    ],
    "metadata": {},
}


@pytest_asyncio.fixture
async def durable_db(tmp_path):
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
    from applire.db.session import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'vault.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine, async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(factory) -> uuid.UUID:
    from applire.models.profile import MasterProfile, authorized_profile_write

    async with factory() as session:
        with authorized_profile_write():
            record = MasterProfile(profile_json=dict(_SEED))
        session.add(record)
        await session.commit()
        return record.id


async def _read_back(engine, profile_id) -> dict:
    from applire.models.profile import MasterProfile

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        row = (
            await session.execute(select(MasterProfile).where(MasterProfile.id == profile_id))
        ).scalar_one()
        return dict(row.profile_json)


def _skill(stored, name):
    return next(s for s in stored["skills"] if s["name"] == name)


@pytest.mark.asyncio
async def test_recompute_rederives_the_span_labels_it_computed_and_survives(durable_db):
    from applire.routers.profile_health import RecomputeSpanRequest, recompute_span_endpoint
    from applire.services.skill_enrichment import stored_span_years
    from applire.schemas.profile import MasterProfileData

    engine, factory = durable_db
    profile_id = await _seed(factory)
    expected = stored_span_years(MasterProfileData.model_validate(_SEED), "SAP CO", date.today())

    async with factory() as session:
        await recompute_span_endpoint(RecomputeSpanRequest(skill_id="s-co"), session, None)

    stored = await _read_back(engine, profile_id)
    co = _skill(stored, "SAP CO")
    assert co["source"] == "computed"
    assert co["years_experience"] == expected
    assert co["id"] == "s-co"
    # Only the addressed skill moved — the candidate's own Python span is untouched.
    py = _skill(stored, "Python")
    assert (py["years_experience"], py["source"], py["proficiency"]) == (3, "transcribed", "advanced")
    record = stored["metadata"]["enrichment_history"][-1]
    assert record["source"] == "manual_edit"
    assert any(c["section"] == "skills" for c in record["changes"])


@pytest.mark.asyncio
async def test_the_health_item_is_gone_after_the_recompute(durable_db):
    from applire.routers.profile_health import RecomputeSpanRequest, recompute_span_endpoint
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.health import assess_health

    engine, factory = durable_db
    profile_id = await _seed(factory)
    before = assess_health(MasterProfileData.model_validate(await _read_back(engine, profile_id)))
    assert [i.skill_id for i in before.issues if i.thread == "span_provenance"] == ["s-co"]

    async with factory() as session:
        await recompute_span_endpoint(RecomputeSpanRequest(skill_id="s-co"), session, None)

    after = assess_health(MasterProfileData.model_validate(await _read_back(engine, profile_id)))
    assert not [i for i in after.issues if i.thread == "span_provenance"]


@pytest.mark.asyncio
@pytest.mark.parametrize("skill_id", ["s-py", "no-such-skill"])
async def test_a_stale_or_unflagged_skill_is_a_409_and_writes_nothing(durable_db, skill_id):
    from fastapi import HTTPException

    from applire.routers.profile_health import RecomputeSpanRequest, recompute_span_endpoint

    engine, factory = durable_db
    profile_id = await _seed(factory)
    async with factory() as session:
        with pytest.raises(HTTPException) as exc:
            await recompute_span_endpoint(RecomputeSpanRequest(skill_id=skill_id), session, None)
    assert exc.value.status_code == 409
    stored = await _read_back(engine, profile_id)
    assert not (stored.get("metadata") or {}).get("enrichment_history")
    assert _skill(stored, "SAP CO")["source"] == "transcribed"


@pytest.mark.asyncio
async def test_a_second_click_is_stale(durable_db):
    from fastapi import HTTPException

    from applire.routers.profile_health import RecomputeSpanRequest, recompute_span_endpoint

    engine, factory = durable_db
    await _seed(factory)
    async with factory() as session:
        await recompute_span_endpoint(RecomputeSpanRequest(skill_id="s-co"), session, None)
    async with factory() as session:
        with pytest.raises(HTTPException) as exc:
            await recompute_span_endpoint(RecomputeSpanRequest(skill_id="s-co"), session, None)
    assert exc.value.status_code == 409
