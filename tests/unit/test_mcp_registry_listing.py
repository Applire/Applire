# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""US371 (#791) — the MCP Registry entry and the Glama listing stay honest.

The registry verifies ownership by reading the image label
`io.modelcontextprotocol.server.name` off the very tag `server.json` names, and
refuses at PUBLISH time (not at `mcp-publisher validate`) an OCI package that
carries its own `version`. Neither failure would show before the 1.0 release
job runs, so the invariants are pinned here, offline.
"""
import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SERVER = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
OCI = [p for p in SERVER["packages"] if p["registryType"] == "oci"]


def _stamp_module():
    spec = importlib.util.spec_from_file_location(
        "server_json_release", ROOT / "scripts" / "server_json_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_image_label_names_the_registry_entry():
    label = re.search(
        r'^LABEL io\.modelcontextprotocol\.server\.name="([^"]+)"$',
        (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert label, "backend/Dockerfile must carry the registry ownership label"
    assert label.group(1) == SERVER["name"]


def test_the_namespace_is_the_org_login_the_oidc_grant_matches():
    """github-oidc grants `io.github.<repository_owner>/*`, case-sensitively;
    the org login is `Applire` (B2-1, casing recorded 2026-10-20)."""
    assert SERVER["name"] == "io.github.Applire/applire"
    assert SERVER["repository"] == {"url": "https://github.com/Applire/Applire", "source": "github"}


def test_registry_field_limits_hold():
    assert SERVER["$schema"] == (
        "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    )
    assert 1 <= len(SERVER["description"]) <= 100
    assert 1 <= len(SERVER["title"]) <= 100
    assert "running Applire instance" in SERVER["description"]  # US371: say so


def test_one_oci_package_whose_tag_is_the_version_and_nothing_else():
    assert len(OCI) == 1
    pkg = OCI[0]
    for forbidden in ("version", "registryBaseUrl", "fileSha256"):
        assert forbidden not in pkg, f"the registry refuses an OCI package with {forbidden!r}"
    image, tag = pkg["identifier"].rsplit(":", 1)
    assert image == "ghcr.io/applire/applire-backend"
    assert tag == SERVER["version"]
    assert pkg["transport"] == {"type": "stdio"}  # Community Edition: stdio only


def test_the_committed_version_is_the_backends_version():
    """The release commit bumps both, or this test names the one it forgot."""
    project = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    assert SERVER["version"] == project["project"]["version"]


def test_the_token_is_a_required_secret_passed_into_the_container():
    env = {e["name"]: e for e in OCI[0]["environmentVariables"]}
    token = env["APPLIRE_AGENT_TOKEN"]
    assert token["isRequired"] is True and token["isSecret"] is True
    assert "value" not in token and "default" not in token  # never a fixed token
    forwards = [
        a for a in OCI[0]["runtimeArguments"] if a["name"] == "-e" and a.get("value") == "APPLIRE_AGENT_TOKEN"
    ]
    assert forwards, "docker run must forward the client's APPLIRE_AGENT_TOKEN"


def test_the_launch_runs_the_same_command_as_the_compose_mcp_service():
    args = [a["value"] for a in OCI[0]["packageArguments"]]
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert " ".join(args) == compose["services"]["mcp"]["command"] == "python -m applire.mcp"
    names = [a["name"] for a in OCI[0]["runtimeArguments"]]
    # The instance's network, .env and uploads volume: the server joins a RUNNING
    # instance rather than pretending to be one.
    assert {"--network", "--env-file", "--volume"} <= set(names)


@pytest.mark.parametrize(
    "tag, version",
    [("v1.0.0", "1.0.0"), ("v1.2.3", "1.2.3"), ("v0.45.0-beta", "0.45.0-beta")],
)
def test_the_release_stamp_sets_version_and_image_tag_together(tag, version):
    stamped = _stamp_module().stamp(SERVER, tag)
    assert stamped["version"] == version
    oci = [p for p in stamped["packages"] if p["registryType"] == "oci"][0]
    assert oci["identifier"] == f"ghcr.io/applire/applire-backend:{version}"
    # The committed entry is not mutated by the stamp (it returns a copy).
    assert OCI[0]["identifier"].endswith(":" + SERVER["version"])


@pytest.mark.parametrize("bad", ["1.0.0", "v1.0", "latest", "vX", "v1.0.0+build"])
def test_the_release_stamp_refuses_a_tag_that_is_not_a_release(bad):
    with pytest.raises(ValueError):
        _stamp_module().version_from_tag(bad)


def test_the_publish_job_runs_only_on_a_latest_moving_release_with_oidc():
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "release.yml").read_text())["jobs"]
    job = jobs["publish-mcp-registry"]
    assert job["needs"] == "merge-backend"  # the labelled tag must exist first
    assert "github.event.release.prerelease == false" in job["if"]
    assert "github.event_name == 'release'" in job["if"]  # never on a manual dispatch
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    run = "\n".join(s.get("run", "") for s in job["steps"])
    assert "login github-oidc" in run and "validate server.json" in run
    assert "sha256sum -c" in run  # the publisher binary is checksum-pinned
    assert re.fullmatch(r"[0-9a-f]{64}", job["env"]["MCP_PUBLISHER_SHA256"])


def test_glama_json_names_the_maintainer():
    glama = json.loads((ROOT / "glama.json").read_text(encoding="utf-8"))
    assert glama == {
        "$schema": "https://glama.ai/mcp/schemas/server.json",
        "maintainers": ["tobias-rosenbaum"],
    }


def test_the_glama_image_has_no_token_and_starts_the_real_server():
    dockerfile = (ROOT / "glama" / "Dockerfile").read_text(encoding="utf-8")
    entry = (ROOT / "glama" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "APPLIRE_AGENT_TOKEN" not in dockerfile and "apl_" not in dockerfile
    assert "apl_" not in entry
    assert entry.rstrip().endswith("exec python -m applire.mcp")
    assert 'APPLIRE_AGENT_TOKEN="$(python /app/glama_seed.py)"' in entry
