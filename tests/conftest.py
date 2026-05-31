"""
Shared fixtures for Applire integration tests.

Setup: docker compose build + up, wait for API readiness, run migrations.
Teardown: containers are left running so the developer can inspect state.
To stop: docker compose down
"""
import os
import subprocess
import time
from pathlib import Path

import pytest
import requests

PROJECT_ROOT = Path(__file__).parent.parent
API_BASE = "http://localhost:8001"
_READY_TIMEOUT = 120  # seconds

# Use the host from the active Docker context so we always talk to the same
# daemon the user's `docker` CLI uses — avoids stale Desktop sockets.
def _active_docker_host() -> str:
    try:
        result = subprocess.run(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
            capture_output=True, text=True, check=True,
        )
        host = result.stdout.strip()
        if host:
            return host
    except Exception:
        pass
    # Fallbacks: rootless → system socket.
    _uid = os.getuid()
    for sock in (f"/run/user/{_uid}/docker.sock", "/var/run/docker.sock"):
        if Path(sock).exists():
            return f"unix://{sock}"
    return "unix:///var/run/docker.sock"


_DOCKER_ENV = {
    **os.environ,
    "DOCKER_HOST": _active_docker_host(),
}


def _docker_compose(*args: str) -> None:
    # All three files are required. override.yml publishes the host ports
    # (8001/3000) and the build contexts; without it the API is unreachable on
    # :8001 and stale GHCR images run instead of local source. ci.yml layers on
    # top to force LLM_PROVIDER=mock via .env.ci — using the overlay (not
    # `cp .env.ci .env` like CI) so the developer's real .env is left untouched.
    subprocess.run(
        [
            "docker", "compose",
            "-f", "docker-compose.yml",
            "-f", "docker-compose.override.yml",
            "-f", "docker-compose.ci.yml",
            *args,
        ],
        cwd=PROJECT_ROOT,
        env=_DOCKER_ENV,
        check=True,
    )


def _wait_for_api() -> None:
    deadline = time.time() + _READY_TIMEOUT
    while time.time() < deadline:
        try:
            r = requests.get(f"{API_BASE}/health", timeout=2)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"API did not become ready within {_READY_TIMEOUT}s")


@pytest.fixture(scope="session", autouse=True)
def docker_environment():
    if os.getenv("CI"):
        # In CI the workflow manages Docker; just wait for the API.
        _wait_for_api()
        yield
        return
    _docker_compose("down", "-v")  # wipe volumes for a clean DB every run
    _docker_compose("build")
    _docker_compose("up", "-d", "--force-recreate")
    _wait_for_api()
    _docker_compose("exec", "backend", "python", "-m", "alembic", "upgrade", "head")
    yield
    # Containers intentionally left running after tests.
    # Run `docker compose down` manually to stop.


@pytest.fixture(scope="session")
def api():
    return API_BASE
