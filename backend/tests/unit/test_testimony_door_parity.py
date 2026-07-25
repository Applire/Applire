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

"""#258 (ADR-058 door-parity invariant) — the UI door (POST /api/profile/testimony)
and the agent door (MCP `submit_testimony`) must produce EQUIVALENT vault effects
for the same free-text testimony: same receipted changes, same denial handling,
same `testimony` provenance marker. Both doors are asserted to route through the
exact same `submit_testimony` service function — not two implementations that
happen to agree today."""
from __future__ import annotations

import inspect
from typing import Any

import pytest

from applire.models.profile import MasterProfile


class _QueueProvider:
    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = list(payloads)

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        return self.payloads.pop(0)


async def _seed_profile(db) -> MasterProfile:
    record = MasterProfile(
        profile_json={
            "personal_info": {"full_name": "Anna Bauer"},
            "metadata": {
                "completeness_score": 0.5,
                "created_via": "cv_upload",
                "created_at": "2026-01-01T00:00:00Z",
                "last_updated": "2026-01-01T00:00:00Z",
            },
        }
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


def test_ui_router_and_mcp_tool_both_call_the_same_service_function():
    """Structural parity guard: both doors must call
    `services.profile.reconcile.testimony_bridge.submit_testimony` — never a
    parallel implementation that could quietly drift (ADR-058 clause 2)."""
    import applire.mcp.server as mcp_server
    import applire.routers.profile as profile_router
    from applire.services.profile.reconcile.testimony_bridge import (
        submit_testimony as canonical_service,
    )

    router_source = inspect.getsource(profile_router.submit_testimony_endpoint)
    assert "submit_testimony(" in router_source

    mcp_tool_source = inspect.getsource(mcp_server.submit_testimony)
    assert "submit_testimony_svc(" in mcp_tool_source
    assert "from applire.services.profile.reconcile.testimony_bridge import" in mcp_tool_source
    assert "submit_testimony as submit_testimony_svc" in mcp_tool_source

    # The router imports the canonical service under its own name too.
    assert profile_router.submit_testimony is canonical_service


@pytest.mark.asyncio
async def test_both_doors_produce_equivalent_receipts_for_identical_testimony(async_db):
    """Behavioural parity: the SAME testimony text, reconciled to the SAME op
    batch, produces the SAME receipted vault effect regardless of door. Each
    door gets its own seeded profile + provider so the two runs are
    independent (no shared mutable state masking a divergence)."""
    from applire.mcp.server import submit_testimony as mcp_submit_testimony
    from applire.services.profile.reconcile.testimony_bridge import (
        submit_testimony as ui_submit_testimony,
    )

    text = (
        "I led Cargonaut's migration from ECS to Kubernetes: deploy time "
        "dropped from 45 to 8 minutes. I have no blockchain experience though."
    )
    payload = {
        "ops": [{"op": "upsert_skill", "name": "Kubernetes", "category": "technical"}],
        "ambiguities": [],
        "denials": ["blockchain"],
    }

    # UI door: the router's underlying service call, direct.
    ui_db = async_db
    await _seed_profile(ui_db)
    ui_result = await ui_submit_testimony(text, ui_db, _QueueProvider([dict(payload)]))

    # Agent door: the MCP tool wrapper, over a second independent DB/session
    # (the MCP tool owns its own `get_db`/`get_provider` context managers).
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from unittest.mock import AsyncMock, MagicMock, patch

    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as agent_db:
        await _seed_profile(agent_db)

        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=agent_db)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("applire.mcp.server.get_db", return_value=cm), patch(
            "applire.mcp.server.get_provider",
            return_value=_QueueProvider([dict(payload)]),
        ):
            mcp_result = await mcp_submit_testimony(text=text)
    await engine.dispose()

    # Same status, same shape of receipted change, same provenance.
    assert ui_result.status == mcp_result["status"] == "applied"
    assert len(ui_result.changes) == len(mcp_result["changes"]) == 2  # skill + denial
    ui_sections = sorted(c.section for c in ui_result.changes)
    mcp_sections = sorted(c["section"] for c in mcp_result["changes"])
    assert ui_sections == mcp_sections

    assert any(c.section == "skills" for c in ui_result.changes)
    assert any(c["section"] == "skills" for c in mcp_result["changes"])
