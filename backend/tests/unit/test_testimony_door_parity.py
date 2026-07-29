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


def test_every_ledger_upgrade_call_site_passes_the_live_denials():
    """#341 — structural parity guard over the denial floor.

    `upgrade_ledger_for_concepts` has two floors, and the second one (the
    candidate's LIVE denials, the only floor that can see a denial recorded in
    the same call) exists only if the caller passes `denied_concepts`. The
    interview door did; the agent door did not, and had not since it was
    written — so the floor was structurally unreachable there while looking
    perfectly present in the diff, in the tests and in the ADR.

    Behavioural tests cover the two doors that exist today. This one covers the
    door that does not exist yet: a THIRD caller added later is an omission the
    author cannot see, because the parameter is optional and silently defaults
    to no floor. Asserting over the AST rather than over behaviour is
    deliberate — it fails on the new call site itself, not on the eventual
    truthfulness incident.

    What it does NOT do (stated so nobody trusts it further than it goes): it
    checks co-occurrence, not dataflow. A function that calls
    `is_denied_concept` for some unrelated purpose and separately calls
    `upgrade_ledger_for_concepts` reads as guarded here while being unguarded
    at runtime. This catches the omission nobody thought about — which is what
    actually happened on the agent door — not a call site engineered to look
    compliant.

    The invariant is "denial-aware", not "passes the kwarg". A caller may
    instead filter denied concepts out BEFORE the call with the same shared
    predicate — `reevaluate_gap_ledger_against_vault` does exactly that, and
    on purpose: it passes a corpus to `is_denied_concept` (so an independently
    affirmed concept survives) and must never write `status="denied"` itself.
    Both shapes satisfy the floor; neither-shape is the bug."""
    import ast
    from pathlib import Path

    import applire

    # `applire` is a NAMESPACE package (ADR-031 — `applire.cloud.*` shares it),
    # so it has no `__file__`; `__path__` is the portion list.
    package_root = Path(applire.__path__[0])
    call_sites: list[str] = []
    unguarded: list[str] = []

    def _name(call: ast.Call) -> str | None:
        f = call.func
        return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # An aliased import (`import upgrade_ledger_for_concepts as _ulc`)
        # renames every call site out of this guard's sight. Ban the alias
        # rather than try to follow it.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert not (
                        alias.name == "upgrade_ledger_for_concepts" and alias.asname
                    ), (
                        f"{path.relative_to(package_root)}:{node.lineno} imports "
                        "upgrade_ledger_for_concepts under an alias, which hides its "
                        "call sites from this guard — import it under its own name"
                    )
        funcs = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _name(node) != "upgrade_ledger_for_concepts":
                continue
            where = f"{path.relative_to(package_root)}:{node.lineno}"
            call_sites.append(where)
            if any(kw.arg == "denied_concepts" for kw in node.keywords):
                continue
            # Innermost enclosing function: the deepest-starting one whose span
            # contains the call. Filtering in an OUTER function would not prove
            # this call is covered.
            enclosing = max(
                (
                    f
                    for f in funcs
                    if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)
                ),
                key=lambda f: f.lineno,
                default=None,
            )
            pre_filtered = enclosing is not None and any(
                isinstance(n, ast.Call) and _name(n) == "is_denied_concept"
                for n in ast.walk(enclosing)
            )
            if not pre_filtered:
                unguarded.append(where)

    assert call_sites, "guard is inert — no call sites found (was the function renamed?)"
    assert not unguarded, (
        "these callers upgrade ledger entries without either passing the candidate's "
        "live denials or filtering them out first, so the ADR-059 denial floor cannot "
        f"fire for them: {unguarded}"
    )


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
