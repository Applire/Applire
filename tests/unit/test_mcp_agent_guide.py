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

"""
E047 / ADR-056 — Agent-usage guide & honesty contract.

Covers US262 (one canonical guide served through four layers) and US263's
CI drift guard + always-on surface budget.

Run:
    pytest tests/unit/test_mcp_agent_guide.py -v
"""
import asyncio
import json

import pytest

# ADR-056 §4 target ≈ 3.5k tokens for the serialized tool surface. Budget in
# chars (≈ tokens * 4) with headroom so an added tool doesn't instantly trip
# it, while still catching a slide back toward the 17.5k-char baseline.
TOOL_SURFACE_CHAR_BUDGET = 15_000


def _guide() -> str:
    from applire.mcp.server import _load_guide

    return _load_guide()


# ---------------------------------------------------------------------------
# US262 — the canonical guide and its four delivery layers
# ---------------------------------------------------------------------------


def test_guide_loads_and_carries_the_mandated_sections():
    """ADR-056 §2 content contract: path choice, honesty contract (both
    halves), story-selection contract, gotchas, helper framing."""
    guide = _guide()
    assert len(guide) > 2000  # substantive, not a stub
    lowered = guide.lower()
    for marker in (
        "honesty",           # the contract itself
        "get_guide",         # self-reference so agents can re-fetch
        "submit_claims",     # à-la-carte door
        "render_document",
        "audit_document",
        "schema://",         # read-before-call pattern
        "create_application",  # UI-visibility gotcha
        "never fabricate",   # the expected-of-the-agent core rule
        "story",             # BYOI story-selection contract (ADR-055 ruling)
    ):
        assert marker in lowered, f"guide is missing mandated content: {marker!r}"


def test_guide_states_uniform_90d_ttl_and_no_24h_fiction():
    """The 2026-07-19 adversarial fact-check refuted the '24h agent TTL' —
    the guide must state the real, uniform TTL from constants.py and must
    not resurrect the fiction."""
    from applire.constants import GENERATED_DOCUMENTS_TTL_DAYS

    guide = _guide()
    assert str(GENERATED_DOCUMENTS_TTL_DAYS) in guide
    lowered = guide.lower()
    assert "24h" not in lowered and "24 hours" not in lowered


@pytest.mark.asyncio
async def test_get_guide_tool_returns_guide_and_version():
    from applire.mcp.server import GUIDE_VERSION, get_guide

    result = await get_guide()
    assert result["guide"] == _guide()
    assert result["version"] == GUIDE_VERSION
    assert GUIDE_VERSION  # non-empty, date-stamped revision


@pytest.mark.asyncio
async def test_guide_resource_serves_identical_content():
    from applire.mcp.server import resource_guide_usage

    assert await resource_guide_usage() == _guide()


@pytest.mark.asyncio
async def test_guide_prompt_registered_and_serves_identical_content():
    from applire.mcp.server import mcp, prompt_how_to_use_applire

    prompts = await mcp.list_prompts()
    assert "how-to-use-applire" in {p.name for p in prompts}
    assert await prompt_how_to_use_applire() == _guide()


def test_server_instructions_carry_pointer_and_honesty_core():
    """ADR-056 §1(d): the initialize-handshake instructions reach even a
    guide-skipping agent — identity, get_guide pointer, honesty core."""
    from applire.mcp.server import mcp

    instructions = mcp.instructions
    assert instructions and "get_guide" in instructions
    assert "fabricate" in instructions.lower()
    # short by design (~120 tokens): always-on context where injected
    assert len(instructions) < 800


def test_get_guide_description_obeys_the_slim_convention():
    """The guidance tool itself must not be fat (E047 trap #3)."""
    from applire.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    desc = next(t.description for t in tools if t.name == "get_guide")
    assert "guide" in desc.lower()
    assert len(desc) < 300


# ---------------------------------------------------------------------------
# US263 — drift guard + always-on surface budget
# ---------------------------------------------------------------------------


def test_every_registered_tool_appears_in_the_guide():
    """ADR-056 §5: a new tool cannot ship guide-invisible."""
    from applire.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    guide = _guide()
    missing = [t.name for t in tools if t.name not in guide]
    assert not missing, f"tools missing from AGENT_GUIDE.md: {missing}"


def test_serialized_tool_surface_stays_under_budget():
    """ADR-056 §4: the always-on cost target. Baseline before slimming was
    17,477 chars across 24 tools (measured 2026-07-19)."""
    from applire.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    total = sum(
        len(json.dumps({"name": t.name, "description": t.description, "inputSchema": t.inputSchema}))
        for t in tools
    )
    assert total < TOOL_SURFACE_CHAR_BUDGET, (
        f"serialized tool surface is {total} chars (budget {TOOL_SURFACE_CHAR_BUDGET}); "
        "move guidance prose into AGENT_GUIDE.md instead of tool descriptions (ADR-056 §4)"
    )
