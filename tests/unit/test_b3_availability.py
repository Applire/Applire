# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut b3 WP-V — the notice period is a profile field (rulings 787:L3 +
V-4; ADR-063 amended 2026-10-22, D-B3V7): one write door that stamps
`recorded_at`/`source`, a reminder condition computed in one place, and a
server-side close that holds until the value changes or another stale period
passes."""
from datetime import date, timedelta

import pytest

from applire.constants import AVAILABILITY_STALE_AFTER_DAYS
from applire.schemas.profile import Availability, MasterProfileData
from applire.services.profile.availability import availability_reminder
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import DismissAvailabilityReminder, SetAvailability

TODAY = date(2026, 10, 22)
P = AVAILABILITY_STALE_AFTER_DAYS


def _iso(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _with(**kw) -> MasterProfileData:
    return MasterProfileData(availability=Availability(**kw))


def test_missing_when_nothing_is_known():
    assert availability_reminder(MasterProfileData(), today=TODAY) == "missing"
    assert availability_reminder(_with(text="  "), today=TODAY) == "missing"


def test_current_value_needs_no_reminder_until_the_period_passes():
    assert availability_reminder(_with(text="3 Monate", recorded_at=_iso(P)), today=TODAY) is None
    assert availability_reminder(_with(text="3 Monate", recorded_at=_iso(P + 1)), today=TODAY) == "stale"


def test_a_close_hides_the_reminder_for_one_period_then_it_returns():
    stale = dict(text="3 Monate", recorded_at=_iso(200))
    assert availability_reminder(_with(**stale, reminder_dismissed_at=_iso(10)), today=TODAY) is None
    assert availability_reminder(_with(**stale, reminder_dismissed_at=_iso(P + 1)), today=TODAY) == "stale"
    assert availability_reminder(_with(reminder_dismissed_at=_iso(5)), today=TODAY) is None
    assert availability_reminder(_with(reminder_dismissed_at=_iso(P + 1)), today=TODAY) == "missing"


def test_the_door_stamps_date_and_source_and_clears_a_close():
    profile = _with(text="alt", recorded_at=_iso(300), source="manual_edit", reminder_dismissed_at=_iso(3))
    result = apply_ops(profile, [SetAvailability(text=" 3 Monate zum Quartalsende ")], "agent_interview")
    a = result.profile.availability
    assert a.text == "3 Monate zum Quartalsende"
    assert a.source == "agent_interview"
    assert date.fromisoformat(a.recorded_at) >= TODAY
    assert a.reminder_dismissed_at is None
    assert [(c.section, c.action, c.old_value, c.new_value) for c in result.changes] == [
        ("availability", "updated", "alt", "3 Monate zum Quartalsende")]
    assert profile.availability.text == "alt"  # apply_ops never mutates its input


def test_an_empty_text_clears_the_value():
    result = apply_ops(_with(text="sofort", recorded_at=_iso(1)), [SetAvailability(text="")], "manual_edit")
    assert result.profile.availability is None
    assert result.changes[0].action == "removed"


def test_dismiss_keeps_the_value():
    result = apply_ops(_with(text="sofort", recorded_at=_iso(200)), [DismissAvailabilityReminder()], "manual_edit")
    assert result.profile.availability.text == "sofort"
    assert result.profile.availability.reminder_dismissed_at
    assert availability_reminder(result.profile) is None


def test_the_model_cannot_emit_the_op():
    from pydantic import TypeAdapter, ValidationError
    from applire.services.profile.reconcile.ops import ReconcileOp

    with pytest.raises(ValidationError):
        TypeAdapter(ReconcileOp).validate_python({"op": "set_availability", "text": "sofort"})


# ── the REST doors ──────────────────────────────────────────────────────────

import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture
async def client():
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.auth import get_auth_provider
    from applire.db.session import Base, get_db
    from applire.models.profile import MasterProfile
    from applire.models.user import User
    from applire.routers.profile import router
    from tests.support.owners import HARNESS_USER_ID
    from tests.support.profile_factory import make_master_profile

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(
            c, tables=[MasterProfile.__table__, User.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(make_master_profile(profile_json={"personal_info": {"name": "Anna Bauer"}}))
        await session.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: session
        auth = MagicMock()
        auth.get_current_user = AsyncMock(return_value=MagicMock(id=HARNESS_USER_ID))
        app.dependency_overrides[get_auth_provider] = lambda: auth
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    await engine.dispose()


@pytest.mark.asyncio
async def test_rest_put_dismiss_and_the_reminder_on_the_profile_response(client):
    first = await client.get("/api/profile")
    assert first.json()["availability_reminder"] == "missing"
    dismissed = await client.post("/api/profile/availability/reminder/dismiss")
    assert dismissed.json()["availability_reminder"] is None
    put = await client.put("/api/profile/availability", json={"text": "3 Monate zum Quartalsende"})
    body = put.json()
    assert body["profile"]["availability"]["text"] == "3 Monate zum Quartalsende"
    assert body["profile"]["availability"]["source"] == "manual_edit"
    assert body["profile"]["availability"]["reminder_dismissed_at"] is None
    assert body["availability_reminder"] is None
    cleared = await client.put("/api/profile/availability", json={"text": ""})
    assert cleared.json()["profile"]["availability"] is None
    assert cleared.json()["availability_reminder"] == "missing"
