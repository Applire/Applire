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

"""#258 — MCP `submit_testimony`: the agent door for free-text testimony.

The dedicated-tool branch of the hypothesis in the issue: `submit_claims`
cannot carry a whole free-text dossier (`ClaimItem.statement` caps at 2000
chars; the panel-case dossiers under tests/files/panel_review_case/*/ run
2.6-3.1k chars each), so testimony gets its own tool calling the SAME
`submit_testimony` service the UI door's router endpoint calls."""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from mcp.shared.exceptions import McpError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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

    db.add(MasterProfile(profile_json={"personal_info": {"full_name": "Anna Bauer"}}))
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
async def test_happy_path_returns_testimony_envelope(seeded):
    from applire.mcp.server import submit_testimony

    provider = _Provider(
        {
            "ops": [{"op": "upsert_skill", "name": "Kubernetes", "category": "technical"}],
            "ambiguities": [],
            "denials": [],
        }
    )
    p1, p2 = _patches(seeded, provider)
    with p1, p2:
        result = await submit_testimony(
            text="I administered Kubernetes clusters for 3 years."
        )
    assert result["schema_version"] == "testimony/1"
    assert result["submission_id"]
    assert result["status"] == "applied"


@pytest.mark.asyncio
async def test_denial_reported_as_denial_recorded(seeded):
    from applire.mcp.server import submit_testimony

    provider = _Provider({"ops": [], "ambiguities": [], "denials": ["blockchain"]})
    p1, p2 = _patches(seeded, provider)
    with p1, p2:
        result = await submit_testimony(
            text="I have no blockchain experience — that's an honest gap."
        )
    assert result["status"] == "denial_recorded"
    assert len(result["changes"]) == 1


@pytest.mark.asyncio
async def test_empty_text_rejected_32602(seeded):
    from applire.mcp.server import submit_testimony

    p1, p2 = _patches(seeded)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await submit_testimony(text="")
    assert exc.value.error.code == -32602


@pytest.mark.asyncio
async def test_no_profile_not_found_32001(db):
    from applire.mcp.server import submit_testimony

    p1, p2 = _patches(db)
    with p1, p2:
        with pytest.raises(McpError) as exc:
            await submit_testimony(text="Something about me.")
    assert exc.value.error.code == -32001


@pytest.mark.asyncio
async def test_tool_registered_and_appears_in_guide():
    from applire.mcp.server import mcp as server_mcp, _load_guide

    tools = await server_mcp.list_tools()
    names = {t.name for t in tools}
    assert "submit_testimony" in names
    assert "submit_testimony" in _load_guide()


@pytest.mark.asyncio
async def test_tool_description_names_the_whole_document_vs_claims_distinction():
    from applire.mcp.server import mcp as server_mcp

    tools = await server_mcp.list_tools()
    tool = next(t for t in tools if t.name == "submit_testimony")
    desc = tool.description
    assert "schema://testimony" in desc
    assert "submit_claims" in desc  # points the caller at the itemized alternative
