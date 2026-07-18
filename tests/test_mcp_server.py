"""
Iteration 7 — MCP Server (integration test)

Done when:
  - ``python -m applire.mcp`` starts and completes the MCP handshake
  - ``tools/list`` returns the 7 expected core tools with descriptions
  - ``resources/list`` returns the 3 expected resource URIs with descriptions
  - ``MCP_TRANSPORT=sse`` is rejected at startup with exit code 1

The MCP server runs as a stdio subprocess (not an HTTP service), so these
tests drive it directly via ``docker compose exec`` rather than HTTP requests.
"""

import json
import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

_uid = os.getuid()
_rootless_sock = Path(f"/run/user/{_uid}/docker.sock")
_DOCKER_ENV = {
    **os.environ,
    "DOCKER_HOST": (
        f"unix://{_rootless_sock}" if _rootless_sock.exists() else "unix:///var/run/docker.sock"
    ),
}

# Kept as a strict-equality guard (see test_mcp_tools_list_returns_all_tools) —
# every new tool must be added here deliberately. This drifted silently for a
# while (18 registered vs. 9 listed) because this file is a CI orphan; it's
# now wired into the integration-and-e2e-tests job (see .github/workflows/test.yml)
# and mirrored by a hermetic cross-check in tests/unit/test_mcp_tools.py.
_EXPECTED_TOOLS = {
    "analyze_jd",
    "get_profile",
    "update_profile",
    "analyze_gaps",
    "run_interview",
    "send_message",
    "generate_cv",
    "get_cv_status",
    "get_cv_ats_report",
    # Cover letters (#170)
    "generate_cover_letter",
    "get_cover_letter_status",
    "get_cover_letter_ats_report",
    # Truthfulness Oracle (E043/US248, ADR-052/ADR-054)
    "audit_document",
    # BYOI agent-door rendering (E044/US251, ADR-054)
    "render_document",
    # Flow orchestrator (US109)
    "start_flow",
    "advance_flow",
    "get_flow_state",
    # Profile ingestion / roles
    "import_cv",
    "add_role",
    # Applications (sprint 7, Task 21.14, and later)
    "create_application",
    "update_application",
    "list_applications",
    "get_application",
}

_EXPECTED_STATIC_RESOURCE_URIS = {
    "profile://current",
    # E044/US251 (ADR-054): the public versioned document-content contracts.
    "schema://cv",
    "schema://cover-letter",
}

_EXPECTED_TEMPLATE_URIS = {
    "job://{job_id}",
    "cv://{cv_id}",
    "flow://{flow_id}",
}

# ---------------------------------------------------------------------------
# MCP JSON-RPC message helpers
# ---------------------------------------------------------------------------


def _msg(id_, method, params=None) -> str:
    m: dict = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        m["id"] = id_
    if params is not None:
        m["params"] = params
    return json.dumps(m) + "\n"


_INIT = _msg(
    1,
    "initialize",
    {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0.1"},
    },
)
_INITIALIZED = _msg(None, "notifications/initialized")
_TOOLS_LIST = _msg(2, "tools/list")
_RESOURCES_LIST = _msg(3, "resources/list")
_RESOURCE_TEMPLATES_LIST = _msg(4, "resources/templates/list")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_mcp(stdin: str, env_overrides: dict | None = None, timeout: float = 20.0) -> tuple[int, list[dict]]:
    """
    Run ``python -m applire.mcp`` inside the backend container.

    Returns (returncode, list of parsed JSON-RPC response objects from stdout).
    """
    extra: list[str] = []
    if env_overrides:
        for k, v in env_overrides.items():
            extra += ["-e", f"{k}={v}"]

    cmd = [
        "docker", "compose", "exec", "-iT",
        *extra,
        "backend", "python", "-m", "applire.mcp",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=PROJECT_ROOT,
        env=_DOCKER_ENV,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, _ = proc.communicate(input=stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, _ = proc.communicate()

    responses = []
    for line in stdout.splitlines():
        line = line.strip()
        if line:
            try:
                responses.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return proc.returncode, responses


def _find(responses: list[dict], id_: int) -> dict | None:
    return next((r for r in responses if r.get("id") == id_), None)


# ---------------------------------------------------------------------------
# Startup and handshake
# ---------------------------------------------------------------------------


def test_mcp_server_responds_to_initialize():
    """Server must return a result (not an error) for the initialize request."""
    _, responses = _run_mcp(_INIT + _INITIALIZED)
    init_resp = _find(responses, 1)
    assert init_resp is not None, f"No response to initialize. Responses: {responses}"
    assert "result" in init_resp, f"initialize returned error: {init_resp}"


def test_mcp_server_advertises_applire_name():
    """serverInfo.name must be 'Applire'."""
    _, responses = _run_mcp(_INIT + _INITIALIZED)
    init_resp = _find(responses, 1)
    assert init_resp and "result" in init_resp
    assert init_resp["result"].get("serverInfo", {}).get("name") == "Applire"


def test_mcp_sse_transport_rejected_with_exit_code_1():
    """MCP_TRANSPORT=sse must exit immediately with code 1."""
    proc = subprocess.Popen(
        [
            "docker", "compose", "exec", "-iT",
            "-e", "MCP_TRANSPORT=sse",
            "backend", "python", "-m", "applire.mcp",
        ],
        cwd=PROJECT_ROOT,
        env=_DOCKER_ENV,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _, stderr = proc.communicate(input="", timeout=10)
    assert proc.returncode == 1, (
        f"Expected exit code 1 for SSE transport, got {proc.returncode}"
    )
    assert "MCP_TRANSPORT" in stderr or "Community" in stderr, (
        f"Expected error mentioning MCP_TRANSPORT or Community Edition; got: {stderr!r}"
    )


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------


def test_mcp_tools_list_returns_all_tools():
    """tools/list must advertise all registered Community tools."""
    _, responses = _run_mcp(_INIT + _INITIALIZED + _TOOLS_LIST)
    resp = _find(responses, 2)
    assert resp is not None, f"No response to tools/list. Responses: {responses}"
    assert "result" in resp, f"tools/list returned error: {resp}"
    names = {t["name"] for t in resp["result"].get("tools", [])}
    assert names == _EXPECTED_TOOLS, (
        f"Tool name mismatch.\nExpected: {sorted(_EXPECTED_TOOLS)}\nGot:      {sorted(names)}"
    )


def test_mcp_tools_all_have_description():
    _, responses = _run_mcp(_INIT + _INITIALIZED + _TOOLS_LIST)
    resp = _find(responses, 2)
    assert resp and "result" in resp
    for tool in resp["result"].get("tools", []):
        assert tool.get("description"), f"Tool {tool['name']!r} has no description"


def test_mcp_tools_all_have_input_schema():
    _, responses = _run_mcp(_INIT + _INITIALIZED + _TOOLS_LIST)
    resp = _find(responses, 2)
    assert resp and "result" in resp
    for tool in resp["result"].get("tools", []):
        assert "inputSchema" in tool, f"Tool {tool['name']!r} is missing inputSchema"


# ---------------------------------------------------------------------------
# resources/list  (static resources)
# ---------------------------------------------------------------------------


def test_mcp_resources_list_returns_static_resource():
    """resources/list must advertise the static profile://current resource."""
    _, responses = _run_mcp(_INIT + _INITIALIZED + _RESOURCES_LIST)
    resp = _find(responses, 3)
    assert resp is not None, f"No response to resources/list. Responses: {responses}"
    assert "result" in resp, f"resources/list returned error: {resp}"
    uris = {r["uri"] for r in resp["result"].get("resources", [])}
    assert _EXPECTED_STATIC_RESOURCE_URIS <= uris, (
        f"Static resource URI mismatch.\n"
        f"Expected at least: {sorted(_EXPECTED_STATIC_RESOURCE_URIS)}\nGot: {sorted(uris)}"
    )


def test_mcp_static_resources_have_description():
    _, responses = _run_mcp(_INIT + _INITIALIZED + _RESOURCES_LIST)
    resp = _find(responses, 3)
    assert resp and "result" in resp
    for resource in resp["result"].get("resources", []):
        assert resource.get("description"), f"Resource {resource['uri']!r} has no description"


# ---------------------------------------------------------------------------
# resources/templates/list  (URI-template resources)
# ---------------------------------------------------------------------------


def test_mcp_resource_templates_list_returns_parametric_resources():
    """resources/templates/list must advertise job://{job_id} and cv://{cv_id}."""
    _, responses = _run_mcp(_INIT + _INITIALIZED + _RESOURCE_TEMPLATES_LIST)
    resp = _find(responses, 4)
    assert resp is not None, f"No response to resources/templates/list. Responses: {responses}"
    assert "result" in resp, f"resources/templates/list returned error: {resp}"
    uris = {r["uriTemplate"] for r in resp["result"].get("resourceTemplates", [])}
    assert uris == _EXPECTED_TEMPLATE_URIS, (
        f"Resource template URI mismatch.\n"
        f"Expected: {sorted(_EXPECTED_TEMPLATE_URIS)}\nGot:      {sorted(uris)}"
    )


def test_mcp_resource_templates_have_description():
    _, responses = _run_mcp(_INIT + _INITIALIZED + _RESOURCE_TEMPLATES_LIST)
    resp = _find(responses, 4)
    assert resp and "result" in resp
    for tmpl in resp["result"].get("resourceTemplates", []):
        assert tmpl.get("description"), f"Resource template {tmpl.get('uriTemplate')!r} has no description"
