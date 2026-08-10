# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""US255 (E045, ADR-054) — MCP `submit_claims`.

The agent door for claims: à-la-carte (only a profile required; analyze_gaps
only for gap-linked claims), agent-actionable -32602s, whole-call rejection on
gap-contract violations."""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from mcp.shared.exceptions import McpError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


@pytest_asyncio.fixture
async def db():
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    from applire.db.session import Base

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


class _Provider:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def aparse_json(self, prompt, **kwargs):
        self.calls += 1
        return self.payload


@pytest_asyncio.fixture
async def seeded(db):
    from applire.models.profile import MasterProfile

    db.add(make_master_profile(profile_json={"personal_info": {"full_name": "Anna Bauer"}}))
    await db.commit()
    return db


def _patches(session, provider=None):
    return (
        patch("applire.mcp.server.get_db", return_value=_db_cm(session)),
        patch(
            "applire.mcp.server.get_provider",
            return_value=provider or _Provider({"ops": [], "ambiguities": [], "denials": []}),
        ),
    )


@pytest.mark.asyncio
async def test_happy_path_returns_submission_envelope(seeded):
    from applire.mcp.server import submit_claims

    provider = _Provider(
        {
            "ops": [{"op": "upsert_skill", "name": "Kubernetes", "category": "technical"}],
            "ambiguities": [],
            "denials": [],
        }
    )
    p1, p2 = _patches(seeded, provider)
    with p1, p2:
        result = await submit_claims(
            claims=[{"statement": "I administered Kubernetes clusters for 3 years."}]
        )
    assert result["schema_version"] == "claims/1"
    assert result["submission_id"]
    assert result["results"][0]["status"] == "applied"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims,job_id",
    [
        ([], None),  # empty list
        ([{"statement": "x"}] * 21, None),  # over cap
        ([{"statement": "x", "operation": "upsert_skill"}], None),  # unknown field
        ([{"statement": ""}], None),  # empty statement
        ([{"statement": "x", "gap": "Kubernetes"}], None),  # gap without job_id
        ([{"statement": "x"}], "not-a-uuid"),  # bad uuid
    ],
)
async def test_invalid_input_rejected_32602(seeded, claims, job_id):
    from applire.mcp.server import submit_claims

    p1, p2 = _patches(seeded)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await submit_claims(claims=claims, job_id=job_id)
    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_unknown_field_error_carries_field_path(seeded):
    from applire.mcp.server import submit_claims

    p1, p2 = _patches(seeded)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await submit_claims(claims=[{"statement": "x", "operation": "y"}])
    assert "operation" in exc.value.error.message


@pytest.mark.asyncio
async def test_unknown_job_not_found_32001(seeded):
    from applire.mcp.server import submit_claims

    p1, p2 = _patches(seeded)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await submit_claims(
                claims=[{"statement": "x"}], job_id=str(uuid.uuid4())
            )
    assert exc.value.error.code == -32001


@pytest.mark.asyncio
async def test_no_profile_not_found_32001(db):
    from applire.mcp.server import submit_claims

    p1, p2 = _patches(db)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await submit_claims(claims=[{"statement": "x"}])
    assert exc.value.error.code == -32001


@pytest.mark.asyncio
async def test_non_member_gap_rejects_whole_call_naming_value(seeded):
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from sqlalchemy import select

    from applire.mcp.server import submit_claims

    job = JobAnalysis(
        raw_text_hash="mcp-claims",
        raw_text="JD",
        role_title="Engineer",
        seniority_level="senior",
        language_requirement="en",
    )
    seeded.add(job)
    await seeded.flush()
    seeded.add(
        GapAnalysis(
            job_analysis_id=job.id,
            profile_id=(await seeded.execute(select(MasterProfile.id))).scalar_one(),
            keyword_ledger=[
                {"concept": "Kubernetes", "claimable": False, "status": "missing", "evidence": ""}
            ],
        )
    )
    await seeded.commit()

    provider = _Provider({"ops": [], "ambiguities": [], "denials": []})
    p1, p2 = _patches(seeded, provider)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await submit_claims(
                claims=[{"statement": "I know Go.", "gap": "Go"}],
                job_id=str(job.id),
            )
    assert exc.value.error.code == -32602
    assert "Go" in exc.value.error.message
    assert "analyze_gaps" in exc.value.error.message
    assert provider.calls == 0  # no LLM spend on a rejected call


@pytest.mark.asyncio
async def test_tool_description_carries_doctrine_and_limits():
    from applire.mcp.server import mcp as server_mcp

    tools = await server_mcp.list_tools()
    tool = next(t for t in tools if t.name == "submit_claims")
    desc = tool.description
    assert "schema://claims" in desc
    assert "not verif" in desc or "self-attested" in desc  # ADR-052 §5 limit
    assert "Health hub" in desc  # confirmation-parking note
    assert "analyze_gaps" in desc  # à-la-carte gap prerequisite
