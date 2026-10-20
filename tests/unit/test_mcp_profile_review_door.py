# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ruling A-1 (agent collector #788 lines 13/20/23/29/37) — the agent door
answers the vault's parked confirmations and conflicts.

`submit_claims`, `submit_testimony`, `import_cv` and `resolve_gap` park an
ambiguity they will not guess (`metadata.pending_confirmations` /
`pending_conflicts`); the Health hub shows it, and the UI's "Resolve" button
answers it through the standalone profile-review interview
(`POST /api/session/profile-review`, then `/message`). The agent door had no
way to START that session, so an agent could read the question and never close
it. `run_interview` without `job_id` now starts the same session; the answer is
an ordinary `send_message` turn. Driven through `mcp.call_tool` (FastMCP drops
`McpError.data`, and the real input schema must accept the omitted id).
"""
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse
from tests.support.profile_factory import make_master_profile

OPTIONS = ["Keep as separate roles", "Merge into existing role", "Replace existing role"]
QUESTION = "Is 'Lead Developer' at Acme the same role as your 'Engineer' entry?"


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
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

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _provider():
    """The confirmation turn is deterministic — the LLM must never be asked."""
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="(must not be called)")
    provider.aparse_json = AsyncMock(return_value={})
    return provider


async def _seed(db, *, confirmations=True):
    pending = (
        [{"confirmation_id": uuid.uuid4().hex[:12], "question": QUESTION,
          "options": OPTIONS, "source": "agent:claims", "resolved": False}]
        if confirmations else []
    )
    prof = make_master_profile(profile_json={
        "personal_info": {"name": "Max Muster", "email": "max@example.de"},
        "work_experience": [{"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}],
        "metadata": {"pending_conflicts": [], "pending_confirmations": pending,
                     "enrichment_history": []},
    })
    db.add(prof)
    await db.commit()
    return prof


async def _call(name, args, db, provider):
    from applire.mcp.server import mcp

    with (
        patch("applire.mcp.server.get_db", return_value=_db_cm(db)),
        patch("applire.mcp.server.get_provider", return_value=provider),
    ):
        out = await mcp.call_tool(name, args)
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_job_id_is_optional_in_the_published_input_schema():
    """Required → optional is a relaxation: every existing call still validates."""
    from applire.mcp.server import mcp

    tool = next(t for t in await mcp.list_tools() if t.name == "run_interview")
    assert "job_id" in tool.inputSchema["properties"]
    assert "job_id" not in tool.inputSchema.get("required", [])


@pytest.mark.asyncio
async def test_without_job_id_the_parked_confirmation_is_the_first_question(db):
    await _seed(db)
    provider = _provider()
    started = await _call("run_interview", {}, db, provider)

    assert started["first_question"] == QUESTION
    assert started["choices"] == OPTIONS
    assert started["session_id"]
    # Deterministic: no LLM call to surface a parked ask.
    assert provider.acomplete.await_count == 0
    assert provider.aparse_json.await_count == 0
    # No job-posting text in a vault review — so no job-posting marker.
    assert "untrusted_content" not in started


@pytest.mark.asyncio
async def test_a_verbatim_option_through_send_message_resolves_the_park(db):
    from applire.models.profile import MasterProfile
    from applire.schemas.profile import MasterProfileData

    prof = await _seed(db)
    provider = _provider()
    started = await _call("run_interview", {}, db, provider)
    turn = await _call(
        "send_message",
        {"session_id": started["session_id"], "message": "Merge into existing role"},
        db, provider,
    )

    assert turn["complete"] is True
    refreshed = await db.get(MasterProfile, prof.id)
    data = MasterProfileData.model_validate(refreshed.profile_json)
    assert [c for c in data.metadata.pending_confirmations if not c.resolved] == []
    # The same health read the human's hub uses no longer lists it.
    from applire.services.profile.health import assess_health

    assert not [i for i in assess_health(data).issues if i.thread == "confirmation"]


@pytest.mark.asyncio
async def test_nothing_parked_answers_all_clear_without_a_question(db):
    await _seed(db, confirmations=False)
    started = await _call("run_interview", {}, db, _provider())
    assert "No open issues" in started["first_question"]
