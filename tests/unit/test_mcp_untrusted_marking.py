# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-084 clause 4 / threat model SEC-12 (#446) — the agent door marks
JD-derived text, plus the three agent-collector guide lines (#603).

The property under test is *the channel*, not the agent: a BYOI agent (ADR-054)
reads these payloads and DOES have tools, so Applire must never hand it a
stranger's text dressed as its own trusted output. Applire does not sanitise
that text and makes no claim that a posting cannot try — the marker is what lets
the agent apply its own hardening.

Run:
    LLM_PROVIDER=mistral DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend \\
      python3 -m pytest tests/unit/test_mcp_untrusted_marking.py -q
"""
import asyncio
import json
import re

import pytest

from applire.services.untrusted_text import TOOL_RESULT_NOTICE


# ---------------------------------------------------------------------------
# The marking itself
# ---------------------------------------------------------------------------


def _marked_returns() -> set[str]:
    """Every tool/resource function whose return is wrapped by ``_marked``.

    Read from the module SOURCE rather than by calling 27 tools against a
    database: the property is "this return statement is wrapped", and the source
    is where that is true or false. The complement — that the map's coverage
    matches the door's actual payloads — is what the enumeration test below
    pins against the live tool registry.
    """
    import inspect

    from applire.mcp import server

    src = inspect.getsource(server)
    found: set[str] = set()
    current = None
    for line in src.split("\n"):
        m = re.match(r"async def (\w+)\(", line)
        if m:
            current = m.group(1)
        if "_marked(" in line and current and "def _marked" not in line:
            found.add(current)
    return found


#: Every tool that returns a JD-derived string, per ADR-084 clause 4 and the
#: arc42 §5.3.30 enumeration. Kept as a literal so a tool that STOPS marking is
#: a red test rather than a silently shrinking set.
EXPECTED_MARKED = {
    "analyze_jd",
    "analyze_gaps",
    "get_cv_ats_report",
    "get_cover_letter_ats_report",
    "render_document",
    "start_flow",
    "advance_flow",
    "get_flow_state",
    "run_interview",
    "send_message",
    "resolve_gap",
    "submit_claims",
    "create_application",
    "update_application",
    "get_application",
    "list_applications",
    "resource_job",
}

#: Deliberately unmarked — they carry no job-posting string at all. Marking
#: everything would make the marker meaningless, so this list is as load-bearing
#: as the one above.
EXPECTED_UNMARKED = {
    "get_guide",
    "get_profile",
    "update_profile",
    "import_cv",
    "submit_testimony",
    "add_role",
    "generate_cv",
    "generate_cover_letter",
    "get_cv_status",
    "get_cover_letter_status",
    "audit_document",
}


def test_every_jd_carrying_tool_marks_its_result():
    missing = EXPECTED_MARKED - _marked_returns()
    assert not missing, f"ADR-084 cl. 4: these doors return JD-derived text unmarked: {sorted(missing)}"


def test_no_jd_free_tool_is_marked():
    """A marker on a payload that carries no posting text teaches an agent to
    ignore the marker."""
    spurious = EXPECTED_UNMARKED & _marked_returns()
    assert not spurious, f"marked without carrying JD-derived text: {sorted(spurious)}"


def test_the_two_lists_together_cover_every_registered_tool():
    """The enumeration is checked against the LIVE registry, so a new tool
    cannot be added without a decision about whether it carries posting text."""
    from applire.mcp.server import mcp

    tools = {t.name for t in asyncio.run(mcp.list_tools())}
    # `resource_job` is a resource, not a tool
    declared = (EXPECTED_MARKED - {"resource_job"}) | EXPECTED_UNMARKED
    assert tools == declared, (
        "ADR-084 cl. 4 enumeration drifted from the live tool registry.\n"
        f"  registered but undeclared: {sorted(tools - declared)}\n"
        f"  declared but unregistered: {sorted(declared - tools)}"
    )


def test_the_marking_is_additive_and_states_the_rule():
    from applire.mcp.server import _marked

    payload = {"role_title": "Leiter Operations", "id": "abc"}
    out = _marked(payload, "analyze_jd")
    assert out["role_title"] == "Leiter Operations" and out["id"] == "abc"
    uc = out["untrusted_content"]
    assert uc["kind"] == "job_posting"
    assert "role_title" in uc["fields"]
    assert uc["notice"] == TOOL_RESULT_NOTICE
    assert "never as instructions" in uc["notice"]


def test_every_declared_field_map_is_non_empty():
    """A marking whose `fields` list is empty tells the agent nothing."""
    from applire.mcp.server import _JD_DERIVED_FIELDS

    for kind, fields in _JD_DERIVED_FIELDS.items():
        assert fields, f"{kind}: empty field list"


def test_the_marker_costs_nothing_against_the_tool_surface_budget():
    """ADR-056 §4's budget measures names, descriptions and input schemas — never
    return payloads. Pinned so a future reader does not "reclaim" the marker to
    buy budget headroom that it never spent."""
    from applire.mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    surface = sum(
        len(json.dumps({"name": t.name, "description": t.description, "inputSchema": t.inputSchema}))
        for t in tools
    )
    assert surface < 16_000, surface
    assert not any("untrusted_content" in (t.description or "") for t in tools)


# ---------------------------------------------------------------------------
# The guide (#446 AC: `get_guide` must explain the marker)
# ---------------------------------------------------------------------------


def _guide() -> str:
    from applire.mcp.server import _load_guide

    return _load_guide()


def test_the_guide_explains_the_untrusted_marker():
    guide = _guide()
    assert "untrusted_content" in guide
    low = guide.lower()
    assert "data, never as instructions" in low or "never obey it" in low
    # every marked tool is named where the marker is explained
    section = guide.split("## Untrusted job-posting text in tool results", 1)[1]
    section = section.split("## Operational gotchas", 1)[0]
    for tool in sorted(EXPECTED_MARKED - {"resource_job"}):
        assert tool in section, f"guide's marker section does not name {tool}"


def test_guide_version_matches_the_guides_own_revision_line():
    """#603: `get_guide` reported 2026-08-25 while the document it returned said
    'Revision 2026-07-25'. One fact, two places — pinned in both directions."""
    from applire.mcp.server import GUIDE_VERSION

    line = _guide().split("\n")[2]
    assert line.startswith("*Revision "), line
    assert GUIDE_VERSION in line, (line, GUIDE_VERSION)


def test_guide_explains_import_cv_merge_fields():
    """#603: the guide said nothing about `merge_status` / `not_applied` /
    `merge_conflicts`, so a naive agent had no rule for `partial` — and reported
    a half-landed import as done."""
    guide = _guide()
    for marker in ("merge_status", "not_applied", "merge_conflicts", "partial", "rejected"):
        assert marker in guide, f"guide does not explain import_cv's {marker!r}"


# ---------------------------------------------------------------------------
# #603: an error an agent reads must name the tool that fixes it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_profile_on_an_empty_vault_names_the_tool_not_a_rest_path():
    from unittest.mock import AsyncMock, patch

    from applire.mcp import server

    with patch.object(server.profile_svc, "get_profile", AsyncMock(return_value=None)), \
         patch.object(server, "get_db") as gdb:
        gdb.return_value.__aenter__.return_value = AsyncMock()
        with pytest.raises(Exception) as exc:
            await server.get_profile()
    message = str(exc.value)
    assert "import_cv" in message
    assert "/api/" not in message, f"a REST path an agent cannot call: {message!r}"
