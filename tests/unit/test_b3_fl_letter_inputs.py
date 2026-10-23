# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 3, fix round WP-FL — the letter's inputs at every door.

* MD3-4 / A-8: the free-text inputs are capped from ONE constant
  (``constants.LETTER_INPUT_MAX_CHARS``: motivation 2,000, every other 300) on
  REST (422 naming the field) and MCP (invalid input naming the field).
* B3-11 + MD3-5: ``save_availability_to_profile`` writes the letter's notice
  period to ``profile.availability`` (``source: "letter_form"``) when it differs;
  REST default true, MCP default false.
* A-7: a whitespace-only PUT ``/availability`` with nothing stored is a no-op —
  it keeps the reminder close.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.owners import HARNESS_USER_ID
from tests.support.profile_factory import make_master_profile

FREE_TEXT = ("recipient_name", "recipient_company", "salary", "availability", "motivation")


# ── MD3-4: the caps ─────────────────────────────────────────────────────────


def test_the_caps_are_the_dictated_numbers():
    from applire.constants import LETTER_INPUT_MAX_CHARS

    assert LETTER_INPUT_MAX_CHARS == {
        "recipient_name": 300, "recipient_company": 300, "salary": 300,
        "availability": 300, "motivation": 2000,
    }


@pytest.mark.parametrize("field", FREE_TEXT)
def test_the_request_model_refuses_one_char_over_the_cap_and_names_the_field(field):
    from pydantic import ValidationError

    from applire.constants import LETTER_INPUT_MAX_CHARS
    from applire.schemas.cover_letter import CoverLetterGenerateRequest

    cap = LETTER_INPUT_MAX_CHARS[field]
    ok = CoverLetterGenerateRequest(job_id=uuid.uuid4(), **{field: "x" * cap})
    assert len(getattr(ok, field)) == cap
    with pytest.raises(ValidationError) as exc:
        CoverLetterGenerateRequest(job_id=uuid.uuid4(), **{field: "x" * (cap + 1)})
    assert exc.value.errors()[0]["loc"] == (field,)


@pytest.mark.asyncio
async def test_rest_answers_422_naming_the_field():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.cover_letter import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: MagicMock()
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=HARNESS_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    svc = AsyncMock()
    with patch("applire.routers.cover_letter.generate_cover_letter", svc):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post("/api/cover-letter/generate", json={
                "job_id": str(uuid.uuid4()), "motivation": "m" * 2001,
            })
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "motivation"]
    svc.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_refuses_an_over_cap_input_naming_the_field():
    from mcp.shared.exceptions import McpError

    from applire.mcp.server import generate_cover_letter

    svc = AsyncMock()
    with patch("applire.mcp.server.cover_letter_svc.generate_cover_letter", svc):
        with pytest.raises(McpError) as exc:
            await generate_cover_letter(job_id=str(uuid.uuid4()), recipient_name="n" * 301)
    assert "recipient_name" in str(exc.value)
    svc.assert_not_awaited()


# ── MD3-5: the two door defaults ────────────────────────────────────────────


def test_rest_defaults_to_saving_and_mcp_does_not():
    from applire.schemas.cover_letter import CoverLetterGenerateRequest

    assert CoverLetterGenerateRequest(job_id=uuid.uuid4()).save_availability_to_profile is True


@pytest.mark.asyncio
async def test_mcp_advertises_the_flag_default_false_and_forwards_it():
    from applire.mcp.server import mcp

    tool = next(t for t in await mcp.list_tools() if t.name == "generate_cover_letter")
    prop = tool.inputSchema["properties"]["save_availability_to_profile"]
    assert prop["type"] == "boolean" and prop["default"] is False
    assert tool.inputSchema.get("required") == ["job_id"]

    cm = MagicMock()
    db = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.model_dump.return_value = {"cover_letter_id": "x"}
    svc = AsyncMock(return_value=result)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server._owned_job", AsyncMock()),
        patch("applire.mcp.server.cover_letter_svc.generate_cover_letter", svc),
    ):
        await mcp.call_tool("generate_cover_letter", {"job_id": str(uuid.uuid4()), "availability": "sofort"})
        assert svc.await_args.args[0].save_availability_to_profile is False
        await mcp.call_tool("generate_cover_letter", {
            "job_id": str(uuid.uuid4()), "availability": "sofort", "save_availability_to_profile": True,
        })
        assert svc.await_args.args[0].save_availability_to_profile is True


# ── B3-11: the write, through the real letter service ───────────────────────


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


async def _seed(db, availability: dict | None):
    from applire.models.flow import FlowSession
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    db.add(User(id=HARNESS_USER_ID, email="t@example.com"))
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=uuid.uuid4().hex, raw_text="Werkleiter", role_title="Werkleiter",
        required_skills=[], nice_to_have_skills=[], keywords=[], seniority_level="senior",
        company_culture_signals=[], language_requirement="de",
    )
    db.add(job)
    pj = {"personal_info": {"name": "Marcus Brandt"}}
    if availability is not None:
        pj["availability"] = availability
    profile = make_master_profile(profile_json=pj)
    db.add(profile)
    await db.flush()
    db.add(FlowSession(user_id=HARNESS_USER_ID, job_id=job.id, available_actions={}))
    await db.commit()
    return job, profile


async def _generate(db, job, **inputs):
    from applire.schemas.cover_letter import CoverLetterGenerateRequest
    from applire.services.cover_letter import generate_cover_letter

    bg = MagicMock()
    await generate_cover_letter(
        CoverLetterGenerateRequest(job_id=job.id, **inputs), db, AsyncMock(), bg,
        "http://localhost", user_id=HARNESS_USER_ID,
    )
    bg.add_task.assert_called_once()


async def _stored(db, profile) -> dict | None:
    await db.refresh(profile)
    return (profile.profile_json or {}).get("availability")


@pytest.mark.asyncio
async def test_a_new_notice_period_from_the_form_lands_on_the_profile(db):
    job, profile = await _seed(db, {"text": "6 Wochen", "recorded_at": "2026-01-02",
                                    "source": "manual_edit", "reminder_dismissed_at": "2026-02-01"})
    await _generate(db, job, availability=" 3 Monate zum Quartalsende ")
    a = await _stored(db, profile)
    assert a["text"] == "3 Monate zum Quartalsende"
    assert a["source"] == "letter_form"
    assert a["recorded_at"] > "2026-10-01"
    assert a["reminder_dismissed_at"] is None  # a new value clears the close (V-4)


@pytest.mark.asyncio
async def test_into_an_empty_profile_too(db):
    job, profile = await _seed(db, None)
    await _generate(db, job, availability="sofort")
    assert (await _stored(db, profile))["source"] == "letter_form"


@pytest.mark.asyncio
async def test_the_same_value_is_not_rewritten(db):
    """A pre-filled field sent back unchanged is no re-confirmation: the 90-day
    staleness clock keeps its date (B3-11 "when it differs")."""
    stored = {"text": "3 Monate zum Quartalsende", "recorded_at": "2026-01-02",
              "source": "manual_edit", "reminder_dismissed_at": None}
    job, profile = await _seed(db, dict(stored))
    await _generate(db, job, availability="3  monate zum quartalsende")
    assert await _stored(db, profile) == stored


@pytest.mark.asyncio
async def test_unticked_or_empty_writes_nothing(db):
    stored = {"text": "6 Wochen", "recorded_at": "2026-01-02", "source": "manual_edit",
              "reminder_dismissed_at": None}
    job, profile = await _seed(db, dict(stored))
    await _generate(db, job, availability="sofort", save_availability_to_profile=False)
    assert await _stored(db, profile) == stored
    await _generate(db, job, availability="   ")
    assert await _stored(db, profile) == stored


# ── A-7: whitespace-only PUT keeps the close ────────────────────────────────


def test_clearing_nothing_keeps_the_close():
    from applire.schemas.profile import Availability, MasterProfileData
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import SetAvailability

    profile = MasterProfileData(availability=Availability(reminder_dismissed_at="2026-10-20"))
    result = apply_ops(profile, [SetAvailability(text="  \n ")], "manual_edit")
    assert result.profile.availability.reminder_dismissed_at == "2026-10-20"
    assert result.changes == []


@pytest.mark.asyncio
async def test_rest_whitespace_put_after_a_close_keeps_the_reminder_closed():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from applire.auth import get_auth_provider
    from applire.db.session import Base, get_db
    from applire.models.profile import MasterProfile
    from applire.models.user import User
    from applire.routers.profile import router

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
            assert (await ac.post("/api/profile/availability/reminder/dismiss")).json()["availability_reminder"] is None
            put = await ac.put("/api/profile/availability", json={"text": "   "})
            assert put.status_code == 200
            assert put.json()["availability_reminder"] is None
            assert put.json()["profile"]["availability"]["reminder_dismissed_at"]
    await engine.dispose()
