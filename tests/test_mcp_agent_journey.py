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
Agent-channel journey test ("agent PQ") — the Kaile equivalent of the UI PQ tier.

Where OQ/PQ drive the *browser*, this drives the *MCP server* end-to-end over the
real stdio JSON-RPC transport, against the live CI stack (real PostgreSQL, mock
LLM). It is the only tier that executes ``applire/mcp/server.py`` against a real
database — unit tests mock the session, and no browser tier touches the agent
channel at all.

What it proves that lower tiers cannot:
  * cross-call flow state persisted in the DB + ``flow_id`` recovery (an agent
    reconnecting and re-reading state)
  * the **black-box invariant**: ``import_cv`` returns only an extraction summary,
    never raw profile PII — while ``get_profile`` (deliberately) does expose it
  * the MCP glue layer (base64/size handling, error-code mapping, user resolution)
    against a real, Pydantic-validating stack

This is a PROTOTYPE for the tier (single canonical journey). The full spec will
fan it out across personas and error branches.

Run: requires the Docker CI stack with LLM_PROVIDER=mock (brought up by the
session-scoped ``docker_environment`` fixture in tests/conftest.py).
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

# Talk to the same Docker daemon the developer's CLI uses (mirrors conftest.py).
_uid = os.getuid()
_rootless_sock = Path(f"/run/user/{_uid}/docker.sock")
_DOCKER_ENV = {
    **os.environ,
    "DOCKER_HOST": (
        f"unix://{_rootless_sock}" if _rootless_sock.exists() else "unix:///var/run/docker.sock"
    ),
}

# A short CV / JD — content is irrelevant under the mock provider (it returns a
# canned "Anna Bauer" profile and a canned JobAnalysis), but we send realistic
# text so the journey reads like a real agent hand-off.
_CV_TEXT = (
    "Anna Bauer — Senior Software Engineer, Munich. "
    "TechVision GmbH (2021–present): Python, FastAPI, PostgreSQL, Docker. "
    "Built REST APIs for 50k DAU; introduced CI/CD."
)
_JD_TEXT = (
    "Senior Software Engineer (m/w/d). Requirements: 5+ years Python, FastAPI, "
    "PostgreSQL, Docker, CI/CD. German B2 or fluent English."
)

# Keys the black-box summary is allowed to expose. Anything beyond this set —
# names, emails, work_history, contact — would be a privacy regression.
_ALLOWED_SUMMARY_KEYS = {
    "profile_id",
    "positions",
    "skills_count",
    "completeness",
    "merge_conflicts",
    # #615 (ADR-063 amended 2026-08-28, second entry; ADR-058 door parity): the
    # merge's own honesty fact — `merge_status: applied | partial` and the
    # `not_applied` list of entries of the document the caller JUST submitted
    # that the merge carried into no op and no vault entry (section + label +
    # reason). These are facts about the caller's own input, never the stored
    # vault, so the black-box property below (no names, contact, work history)
    # still holds — the PII sweep runs over the whole summary regardless.
    "merge_status",
    "not_applied",
}


class MCPToolError(RuntimeError):
    """A tool call returned a JSON-RPC error or an isError result."""


class MCPSession:
    """Persistent stdio MCP client: one subprocess, handshake once, thread calls.

    A fresh-per-call approach would also work (state lives in PostgreSQL), but a
    single held connection is the genuine shape of an agent driving a journey —
    and it avoids the EOF-vs-tool-execution race that batched stdin hits.
    """

    def __init__(self) -> None:
        self._id = 0
        self._proc = subprocess.Popen(
            ["docker", "compose", "exec", "-iT", "backend", "python", "-m", "applire.mcp"],
            cwd=PROJECT_ROOT,
            env=_DOCKER_ENV,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._handshake()

    def _send(self, obj: dict) -> None:
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _read_for(self, want_id: int) -> dict:
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == want_id:
                return msg
        raise MCPToolError(f"stream closed before response to id={want_id}")

    def _handshake(self) -> None:
        self._id += 1
        self._send({
            "jsonrpc": "2.0", "id": self._id, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "agent-pq", "version": "0.1"},
            },
        })
        init = self._read_for(self._id)
        assert "result" in init, f"initialize failed: {init}"
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, **arguments):
        """Invoke a tool; return its parsed JSON result, or raise MCPToolError."""
        self._id += 1
        self._send({
            "jsonrpc": "2.0", "id": self._id, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })
        resp = self._read_for(self._id)
        if "error" in resp:
            raise MCPToolError(f"{tool}: JSON-RPC error {resp['error']}")
        result = resp["result"]
        blocks = result.get("content") or []
        text = next((b["text"] for b in blocks if b.get("type") == "text"), None)
        if result.get("isError"):
            raise MCPToolError(f"{tool}: {text}")
        # FastMCP puts dict returns directly in structuredContent, but wraps
        # list/scalar returns as {"result": <value>} (content[0] is only the
        # first item, so we must not rely on it for list-returning tools).
        sc = result.get("structuredContent")
        if isinstance(sc, dict):
            return sc["result"] if set(sc) == {"result"} else sc
        return json.loads(text) if text is not None else result

    def close(self) -> None:
        try:
            self._proc.stdin.close()
        finally:
            self._proc.terminate()


@pytest.fixture()
def agent():
    session = MCPSession()
    yield session
    session.close()


def test_kaile_agent_journey(agent):
    """End-to-end agent journey through the MCP channel (CV generation excluded —
    see test_mcp_generate_cv_over_stdio for the known break)."""

    # 1. Ingest a CV. The return is a black-box summary, never the raw profile.
    summary = agent.call("import_cv", text=_CV_TEXT)
    assert set(summary).issubset(_ALLOWED_SUMMARY_KEYS), (
        f"import_cv leaked non-summary keys: {set(summary) - _ALLOWED_SUMMARY_KEYS}"
    )
    assert summary.get("profile_id"), "summary must reference the created profile"
    leaked = json.dumps(summary).lower()
    for pii in ("anna", "bauer", "@", "munich", "work_history", "contact"):
        assert pii not in leaked, f"black-box violation: {pii!r} present in summary"

    # Contrast: the raw profile resource DOES expose the full structure — proving
    # the summary withholds it deliberately, not because the data is absent.
    raw = agent.call("get_profile")
    assert "profile" in raw, "get_profile should expose the full MasterProfile structure"
    assert len(json.dumps(raw)) > 3 * len(json.dumps(summary)), (
        "raw profile should be substantially richer than the black-box summary"
    )

    # 2. Analyse a JD → job_id.
    job = agent.call("analyze_jd", text=_JD_TEXT)
    job_id = job["id"]

    # 3. Open a flow session — flow_id is the stable recovery handle.
    flow = agent.call("start_flow", job_id=job_id)
    flow_id = flow["flow_id"]
    assert flow["user_type"] in ("new", "returning")

    # 4. Gap analysis → advance the flow with the gap artifact.
    gaps = agent.call("analyze_gaps", job_id=job_id)
    gap_id = gaps["id"]
    agent.call("advance_flow", flow_id=flow_id, step="gap_analysis", artifact_id=gap_id)

    # 5. Interview: start, link to flow, answer one question.
    interview = agent.call("run_interview", job_id=job_id)
    session_id = interview["session_id"]
    assert interview.get("first_question"), "interview must return a first question"
    agent.call("advance_flow", flow_id=flow_id, step="interview", artifact_id=session_id)
    reply = agent.call(
        "send_message",
        session_id=session_id,
        message="I have 8 years of Python, 5 with FastAPI in production microservices.",
    )
    assert isinstance(reply, dict)  # next question or {complete: true}

    # 6. Recovery: a cold state read returns the step we advanced to.
    state = agent.call("get_flow_state", flow_id=flow_id)
    assert state["current_step"] == "interview", (
        f"flow recovery returned wrong step: {state['current_step']}"
    )

    # 7. Generate the tailored CV. Over the agent channel this renders inline,
    #    so the status is terminal on the first poll.
    cv = agent.call("generate_cv", job_id=job_id)
    cv_id = cv["cv_id"]
    assert cv_id, "generate_cv must return a cv_id"
    status = agent.call("get_cv_status", cv_id=cv_id)
    assert status["status"] == "ready", f"CV not ready over MCP: {status}"
    agent.call("advance_flow", flow_id=flow_id, step="cv_generation")
    agent.call("advance_flow", flow_id=flow_id, step="complete", artifact_id=cv_id)

    # 8. Generate a cover letter for the same job (#170). Like generate_cv, the
    #    agent channel renders inline (no BackgroundTasks) — terminal status is
    #    expected on the first poll, not after retries.
    cl = agent.call("generate_cover_letter", job_id=job_id)
    cl_id = cl["cover_letter_id"]
    assert cl_id, "generate_cover_letter must return a cover_letter_id"
    cl_status = agent.call("get_cover_letter_status", cover_letter_id=cl_id)
    assert cl_status["status"] == "ready", f"Cover letter not ready over MCP: {cl_status}"
    ats = agent.call("get_cover_letter_ats_report", cover_letter_id=cl_id)
    assert ats["document_id"] == cl_id
    assert ats["status"] == "ready"
    # E057/ADR-079 clause 4 groundwork (#629, story #637): the not_applicable
    # counter must reach the agent over the real stdio channel. This journey
    # runs the REAL audit engine with no producer wired in yet (that is a
    # separate, pending decision), so it can only prove the field survives
    # the full MCP round-trip — never None here, since _finish() (this
    # worktree's schema) always populates it once the report is computed.
    # The "excluded from passed/failed" property itself is proven with a
    # synthetic not_applicable check at the unit tier
    # (tests/unit/test_mcp_render_document.py), which this Docker-gated tier
    # cannot construct without a real producer.
    assert ats["report"]["not_applicable"] == 0

    # 9. Log the application to the pipeline and confirm it is listed.
    app = agent.call("create_application", job_id=job_id)
    app_id = app["id"]
    pipeline = agent.call("list_applications")
    assert any(item["id"] == app_id for item in pipeline), "application not in pipeline"

    # 10. Final recovery read: the flow has reached completion.
    final = agent.call("get_flow_state", flow_id=flow_id)
    assert final["current_step"] == "complete", (
        f"flow did not reach completion: {final['current_step']}"
    )
