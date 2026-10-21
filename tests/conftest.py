"""
Shared fixtures for Applire integration tests.

Setup: docker compose build + up, wait for API readiness, run migrations.
Teardown: containers are left running so the developer can inspect state.
To stop: docker compose down

Two lanes (ADR-091 D-1):

* **Harness lane** (default): the CI stack runs the fenced NoAuth test harness
  (``.env.ci``: ``AUTH_HARNESS=true`` on the ``applire_ci`` database), so every
  request is the stub admin and the integration tests use plain ``requests``.
* **Real-auth lane** (``APPLIRE_E2E_AUTH=1``): login is on
  (``docker-compose.ci-auth.yml``, nginx on a non-80 port). This file then never
  starts, rebuilds or wipes a stack — the lane's stack is brought up by the
  workflow or the developer — and the ``auth_session`` fixture yields a
  ``requests.Session`` signed in as the instance admin (claiming the instance
  with the setup code from the backend log on first use).
"""
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import pytest
import requests

PROJECT_ROOT = Path(__file__).parent.parent
#: The real-auth lane talks to nginx (e.g. http://localhost:8091), never to :8001.
API_BASE = os.environ.get("APPLIRE_API_BASE", "http://localhost:8001").rstrip("/")
REAL_AUTH_LANE = os.environ.get("APPLIRE_E2E_AUTH") == "1"
_READY_TIMEOUT = 120  # seconds

#: How the real-auth lane's stack is addressed for `docker compose logs backend`.
REAL_AUTH_COMPOSE_ARGS = os.environ.get(
    "APPLIRE_E2E_COMPOSE_ARGS",
    "-p applire-ci-auth -f docker-compose.yml -f docker-compose.ci-auth.yml",
)
REAL_AUTH_ADMIN_EMAIL = os.environ.get("APPLIRE_E2E_ADMIN_EMAIL", "admin@ci-auth.example.org")
# Synthetic lane credential (public repo, throwaway stack); policy: 12-256 chars.
REAL_AUTH_ADMIN_PASSWORD = os.environ.get(
    "APPLIRE_E2E_ADMIN_PASSWORD", "ci-auth lane admin passphrase"
)
#: The setup block prints ``… and enter: XXXX-XXXX-…`` (auth/setup.py setup_block).
_SETUP_CODE_RE = re.compile(r"enter: ([A-Z2-7]{4}(?:-[A-Z2-7]{4})+)")

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
    if REAL_AUTH_LANE:
        # The real-auth stack is external to this fixture: never `down -v`,
        # build or recreate anything (it would also be the WRONG project).
        _wait_for_api()
        yield
        return
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


# ---------------------------------------------------------------------------
# Real-auth lane only (APPLIRE_E2E_AUTH=1) — the harness lane never uses these.
# ---------------------------------------------------------------------------


def _setup_code_from_logs() -> str:
    """The most recent setup code printed by the real-auth stack's backend.

    The code rotates on every boot until the instance is claimed (MD-1), so the
    LAST one in the log is the valid one. ``APPLIRE_E2E_SETUP_CODE`` overrides.
    """
    given = os.environ.get("APPLIRE_E2E_SETUP_CODE")
    if given:
        return given
    out = subprocess.run(
        ["docker", "compose", *shlex.split(REAL_AUTH_COMPOSE_ARGS), "logs", "--no-color", "backend"],
        cwd=PROJECT_ROOT,
        env=_DOCKER_ENV,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    codes = _SETUP_CODE_RE.findall(out)
    if not codes:
        raise RuntimeError(
            "real-auth lane: no setup code in the backend log "
            f"(docker compose {REAL_AUTH_COMPOSE_ARGS} logs backend)"
        )
    return codes[-1]


def _signed_in_session(email: str, password: str) -> requests.Session:
    """A session that sends the Origin a browser on API_BASE would send.

    The backend refuses unsafe cookie requests without a same-origin Origin
    (ADR-091 cl. 12); behind nginx the Host it compares with includes the port.
    """
    s = requests.Session()
    s.headers["Origin"] = API_BASE
    r = s.post(f"{API_BASE}/api/auth/login", json={"email": email, "password": password}, timeout=30)
    if r.status_code != 204:
        raise RuntimeError(f"real-auth lane: login as {email} -> {r.status_code} {r.text[:300]}")
    return s


@pytest.fixture(scope="session")
def auth_session() -> requests.Session:
    """The instance admin, signed in through the real login (real-auth lane only).

    First use on a fresh stack claims the instance (``POST /api/setup`` with the
    code from the backend log — the same path an operator takes); afterwards it
    signs in with ``APPLIRE_E2E_ADMIN_EMAIL`` / ``APPLIRE_E2E_ADMIN_PASSWORD``.
    The session carries the ``applire_session`` cookie and an ``Origin`` header.
    """
    if not REAL_AUTH_LANE:
        pytest.skip("real-auth lane only (APPLIRE_E2E_AUTH=1 against docker-compose.ci-auth.yml)")
    state = requests.get(f"{API_BASE}/api/auth/state", timeout=10)
    state.raise_for_status()
    body = state.json()
    if body.get("harness"):
        raise RuntimeError("real-auth lane: the stack serves the NoAuth harness (AUTH_HARNESS is set)")
    if body.get("setup_required"):
        s = requests.Session()
        s.headers["Origin"] = API_BASE
        r = s.post(
            f"{API_BASE}/api/setup",
            json={
                "setup_token": _setup_code_from_logs(),
                "email": REAL_AUTH_ADMIN_EMAIL,
                "password": REAL_AUTH_ADMIN_PASSWORD,
            },
            timeout=60,
        )
        if r.status_code != 204:
            raise RuntimeError(f"real-auth lane: setup -> {r.status_code} {r.text[:300]}")
    else:
        s = _signed_in_session(REAL_AUTH_ADMIN_EMAIL, REAL_AUTH_ADMIN_PASSWORD)
    me = s.get(f"{API_BASE}/api/auth/me", timeout=10)
    if me.status_code != 200 or str(me.json().get("email", "")).lower() != REAL_AUTH_ADMIN_EMAIL.lower():
        raise RuntimeError(f"real-auth lane: /api/auth/me -> {me.status_code} {me.text[:300]}")
    return s


# ---------------------------------------------------------------------------
# Import through the async job (ruling V-4 = A, Hazelnut build 2): the sync
# doors POST /api/profile/upload and POST /api/profile/import answer 410 Gone.
# ---------------------------------------------------------------------------


def import_via_job(
    api_base: str,
    filename: str,
    content: bytes,
    content_type: str,
    *,
    source: str | None = None,
    session: requests.Session | None = None,
    timeout: float = 600.0,
    poll_interval: float = 2.0,
) -> requests.Response:
    """Submit a file to ``POST /api/profile/import-jobs`` and poll until it settles.

    Returns the FINAL poll response (status 200). ``.json()["status"]`` is
    ``ready`` (``["result"]`` holds the CVUploadResponse) or ``failed``
    (``["error_code"]``). A LinkedIn PDF or JSON export passes
    ``source="linkedin"``; a ZIP needs no hint. Raises on a non-202 submit or a
    timeout, so a caller asserting on the result never sees a half-run job.
    """
    http = session or requests
    params = {"source": source} if source else None
    r = http.post(
        f"{api_base}/api/profile/import-jobs",
        files={"file": (filename, content, content_type)},
        params=params,
        timeout=60,
    )
    if r.status_code != 202:
        raise AssertionError(f"POST /api/profile/import-jobs -> {r.status_code} {r.text[:300]}")
    import_id = r.json()["import_id"]
    deadline = time.monotonic() + timeout
    while True:
        poll = http.get(f"{api_base}/api/profile/import-jobs/{import_id}", timeout=30)
        if poll.status_code != 200:
            raise AssertionError(f"GET import-jobs/{import_id} -> {poll.status_code} {poll.text[:300]}")
        if poll.json().get("status") in ("ready", "failed"):
            return poll
        if time.monotonic() > deadline:
            raise AssertionError(f"import job {import_id} did not settle within {timeout:.0f}s")
        time.sleep(poll_interval)


def import_result_via_job(api_base: str, filename: str, content: bytes, content_type: str, **kw) -> dict:
    """``import_via_job`` that requires ``ready`` and returns the CVUploadResponse dict."""
    poll = import_via_job(api_base, filename, content, content_type, **kw)
    body = poll.json()
    if body.get("status") != "ready":
        raise AssertionError(f"import job failed: {body.get('error_code')} {str(body)[:300]}")
    return body["result"]


@pytest.fixture(scope="session")
def import_job():
    """The submit-and-poll helpers for live-stack tests (V-4 = A).

    ``import_job.run(api, name, bytes, mime, source=...)`` → final poll response;
    ``import_job.result(...)`` → the ready CVUploadResponse dict;
    ``import_job.linkedin_json(api, export_dict)`` → the ready result of a
    LinkedIn JSON export (the retired ``linkedin_json`` form field).
    """
    import json as _json
    from types import SimpleNamespace

    def _linkedin_json(api_base: str, export: dict, **kw) -> dict:
        return import_result_via_job(
            api_base,
            "linkedin-export.json",
            _json.dumps(export).encode("utf-8"),
            "application/json",
            source="linkedin",
            **kw,
        )

    return SimpleNamespace(run=import_via_job, result=import_result_via_job, linkedin_json=_linkedin_json)
