# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""788:L24 (founder ruling on the E-12 walk, 2026-10-22; ADR-089/090 amended) —
the agent door's ``analyze_gaps`` takes the UI's refresh path once a gap row
exists: door parity, one gap number.

Driven through ``mcp.call_tool`` (FastMCP drops McpError.data in tool bodies, so
the real dispatch is the only honest seam), against the real ``analyze_gaps`` and
an in-memory DB, with the classifier scripted to flip an UNRELATED requirement
down on the second call — the stochastic flip the per-requirement merge exists
to absorb (ADR-089 cl. 5).
"""
from __future__ import annotations

import copy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The integration half of the per-requirement-merge suite is the fixture this
# door must agree with — loaded by path (tests/unit is not a package), so the
# door and the UI route are proven against ONE scripted classifier and seed.
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "_gap_answer_merge_suite", Path(__file__).with_name("test_gap_answer_merge.py")
)
_suite = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_suite)

BASELINE, CLUSTERS, cls = _suite.BASELINE, _suite.CLUSTERS, _suite.cls
_Scripted, _seed, _change_profile = _suite._Scripted, _suite._seed, _suite._change_profile
db = _suite.db  # the in-memory DB fixture
_site_prompt_without_the_shared_context = _suite._site_prompt_without_the_shared_context


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _call(job_id, db, provider) -> dict:
    from applire.mcp.server import mcp

    with (
        patch("applire.mcp.server.get_db", return_value=_db_cm(db)),
        patch("applire.mcp.server.get_provider", return_value=provider),
        patch("applire.mcp.server._owned_job", AsyncMock(return_value=None)),
    ):
        out = await mcp.call_tool("analyze_gaps", {"job_id": str(job_id)})
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


def _statuses(payload: dict) -> dict[str, str]:
    return {b["requirement"]: b["status"] for b in payload["requirement_breakdown"]}


@pytest.mark.asyncio
async def test_a_second_agent_analyze_gaps_does_not_move_an_unrelated_requirement(db):
    provider = _Scripted()
    job, profile = await _seed(db)
    provider.classifications.append(BASELINE)
    provider.clusters.append(copy.deepcopy(CLUSTERS))
    first = await _call(job.id, db, provider)
    assert _statuses(first)["Python"] == "direct"

    # The vault changed (new evidence), the classifier flips Python down.
    await _change_profile(db, profile, extra_skill="Jira")
    provider.classifications.append(
        [cls("Python", "gap"), cls("Docker", "direct"), cls("Kubernetes", "partial"), cls("Terraform", "gap")]
    )
    second = await _call(job.id, db, provider)

    assert provider.gap_calls == 2, "the inputs changed: a real recompute ran"
    assert second["id"] != first["id"]
    assert _statuses(second)["Python"] == "direct", (
        "the door took the first-analysis path: an unrelated requirement moved down"
    )
    assert second["match_score"] == pytest.approx(first["match_score"])
    assert [c["id"] for c in second["gap_clusters"]] == [c["id"] for c in first["gap_clusters"]]


@pytest.mark.asyncio
async def test_the_first_agent_analyze_gaps_is_the_fresh_analysis(db):
    provider = _Scripted()
    job, _profile = await _seed(db)
    provider.classifications.append(BASELINE)
    provider.clusters.append(copy.deepcopy(CLUSTERS))
    with patch("applire.services.gap.analyze_gaps", wraps=__import__(
        "applire.services.gap", fromlist=["analyze_gaps"]).analyze_gaps) as spy:
        await _call(job.id, db, provider)
    assert spy.await_args.kwargs["answer_scope"] is None
