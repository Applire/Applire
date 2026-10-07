# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared helpers for the real-auth lane (ADR-091 D-1, Strawberry W4-5b).

Used by ``tests/test_auth_e2e.py`` and ``tests/integration/test_auth_races.py``.
Both load this file by path (the directory name is not an importable package)
under ONE module name, so the instance claim below happens once per pytest run.

Everything talks to the stack the way a browser or an agent client would:

* REST through **nginx** on the lane's non-80 port (``APPLIRE_API_BASE``), with
  the browser's ``Origin`` (host AND port) on every cookie request.
* The agent door through ``python -m applire.mcp`` inside the lane's backend
  container (``docker compose <APPLIRE_E2E_COMPOSE_ARGS> exec``), started with
  ``APPLIRE_AGENT_TOKEN`` — the only MCP door the Community Edition ships
  (``MCP_TRANSPORT`` other than ``stdio`` exits 1).
* Read-only ``psql`` against the lane's own Postgres, only to observe what no
  door can show (a shared posting row that nobody can reach any more).

The LLM is the mock provider (``.env.ci-auth``): no provider call is possible.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
API_BASE = os.environ.get("APPLIRE_API_BASE", "http://localhost:8091").rstrip("/")
REAL_AUTH_LANE = os.environ.get("APPLIRE_E2E_AUTH") == "1"
COMPOSE_ARGS = shlex.split(
    os.environ.get(
        "APPLIRE_E2E_COMPOSE_ARGS",
        "-p applire-ci-auth -f docker-compose.yml -f docker-compose.ci-auth.yml",
    )
)
ADMIN_EMAIL = os.environ.get("APPLIRE_E2E_ADMIN_EMAIL", "admin@ci-auth.example.org")
# Synthetic lane credential (public repo, throwaway stack); policy: 12-256 chars.
ADMIN_PASSWORD = os.environ.get("APPLIRE_E2E_ADMIN_PASSWORD", "ci-auth lane admin passphrase")
USER_PASSWORD = "e2e lane user passphrase"
_SETUP_CODE_RE = re.compile(r"enter: ([A-Z2-7]{4}(?:-[A-Z2-7]{4})+)")

#: How many requests race for the one claim / the one invite link.
RACERS = 8
CV_FILE = PROJECT_ROOT / "tests" / "files" / "cv.pdf"


def run_id() -> str:
    """A short id that keeps emails and postings unique per run (re-runs on a
    claimed stack must not collide with an earlier run's accounts)."""
    return uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# docker compose (the lane's own project only)
# ---------------------------------------------------------------------------


def compose(*args: str, input: str | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *COMPOSE_ARGS, *args],
        cwd=PROJECT_ROOT,
        input=input,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def psql(sql: str) -> str:
    """One read-only query on the lane's database (-tA: bare values)."""
    r = compose(
        "exec", "-T", "postgres",
        "psql", "-U", "applire", "-d", "applire", "-v", "ON_ERROR_STOP=1", "-tAc", sql,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql failed ({r.returncode}): {r.stderr[-500:]}")
    return r.stdout.strip()


def setup_code_from_logs() -> str:
    out = compose("logs", "--no-color", "backend").stdout
    codes = _SETUP_CODE_RE.findall(out)
    if not codes:
        raise RuntimeError("real-auth lane: no setup code in the backend log")
    return codes[-1]


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def browser() -> requests.Session:
    """A cookie session that sends the Origin a browser on API_BASE sends."""
    s = requests.Session()
    s.headers["Origin"] = API_BASE
    return s


@dataclass
class Person:
    """One signed-in person. ``label`` is what every assertion message names."""

    label: str
    email: str
    password: str
    s: requests.Session
    id: str = ""
    role: str = ""
    extra: dict = field(default_factory=dict)

    def url(self, path: str) -> str:
        return f"{API_BASE}{path}"

    def get(self, path: str, **kw) -> requests.Response:
        return self.s.get(self.url(path), timeout=kw.pop("timeout", 30), **kw)

    def post(self, path: str, **kw) -> requests.Response:
        return self.s.post(self.url(path), timeout=kw.pop("timeout", 60), **kw)

    def patch(self, path: str, **kw) -> requests.Response:
        return self.s.patch(self.url(path), timeout=kw.pop("timeout", 30), **kw)

    def delete(self, path: str, **kw) -> requests.Response:
        return self.s.delete(self.url(path), timeout=kw.pop("timeout", 60), **kw)

    def refresh_me(self) -> "Person":
        r = self.get("/api/auth/me")
        assert r.status_code == 200, f"{self.label} REST /api/auth/me -> {r.status_code} {r.text[:200]}"
        body = r.json()
        self.id, self.role = body["id"], body["role"]
        return self


def login(label: str, email: str, password: str) -> requests.Response:
    s = browser()
    r = s.post(f"{API_BASE}/api/auth/login", json={"email": email, "password": password}, timeout=60)
    r.session = s  # type: ignore[attr-defined]
    return r


def sign_in(label: str, email: str, password: str) -> Person:
    r = login(label, email, password)
    assert r.status_code == 204, f"{label} REST login -> {r.status_code} {r.text[:200]}"
    return Person(label, email, password, r.session).refresh_me()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# The claim — raced once per pytest process (RACERS concurrent setups)
# ---------------------------------------------------------------------------


@dataclass
class ClaimResult:
    raced: bool
    statuses: list[int] = field(default_factory=list)
    codes: list[str | None] = field(default_factory=list)
    winner_email: str | None = None
    admin: Person | None = None
    note: str = ""


_claim_lock = threading.Lock()
_claim: ClaimResult | None = None


def _racer_email(i: int) -> str:
    return ADMIN_EMAIL if i == 0 else f"claim-racer-{i}@ci-auth.example.org"


def _error_code(r: requests.Response) -> str | None:
    try:
        detail = r.json().get("detail")
    except ValueError:
        return None
    if isinstance(detail, dict):
        return detail.get("error_code")
    return None


def error_code(r: requests.Response) -> str | None:
    return _error_code(r)


def claim() -> ClaimResult:
    """Claim the instance (or sign in to an already claimed one).

    On an unclaimed stack, ``RACERS`` setup requests with the valid code and
    DIFFERENT emails go out at once through nginx: exactly one may win (ADR-091
    cl. 14, atomic claim) — distinct emails prove the claim is decided by the
    instance, not by the email unique index.
    """
    global _claim
    with _claim_lock:
        if _claim is not None:
            return _claim
        state = requests.get(f"{API_BASE}/api/auth/state", timeout=30)
        state.raise_for_status()
        body = state.json()
        if body.get("harness"):
            raise RuntimeError("real-auth lane: the stack serves the NoAuth harness")
        if body.get("setup_required"):
            code = setup_code_from_logs()
            barrier = threading.Barrier(RACERS)
            sessions = [browser() for _ in range(RACERS)]

            def go(i: int) -> requests.Response:
                barrier.wait()
                return sessions[i].post(
                    f"{API_BASE}/api/setup",
                    json={"setup_token": code, "email": _racer_email(i), "password": ADMIN_PASSWORD},
                    timeout=60,
                )

            with ThreadPoolExecutor(RACERS) as pool:
                responses = list(pool.map(go, range(RACERS)))
            result = ClaimResult(
                raced=True,
                statuses=[r.status_code for r in responses],
                codes=[_error_code(r) for r in responses],
            )
            winners = [i for i, r in enumerate(responses) if r.status_code == 204]
            if len(winners) == 1:
                w = winners[0]
                result.winner_email = _racer_email(w)
                result.admin = Person("admin", result.winner_email, ADMIN_PASSWORD, sessions[w]).refresh_me()
        else:
            result = ClaimResult(raced=False, note="instance was already claimed when the lane started")
            for i in range(RACERS):
                r = login("admin", _racer_email(i), ADMIN_PASSWORD)
                if r.status_code == 204:
                    result.winner_email = _racer_email(i)
                    result.admin = Person(
                        "admin", result.winner_email, ADMIN_PASSWORD, r.session  # type: ignore[attr-defined]
                    ).refresh_me()
                    break
        _claim = result
        return result


def require_admin() -> Person:
    c = claim()
    if c.admin is None:
        raise AssertionError(
            f"admin REST /api/setup: no single winner — statuses {c.statuses} codes {c.codes} {c.note}"
        )
    return c.admin


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------


def invite(admin: Person, email: str, role: str = "user") -> tuple[str, str]:
    """``POST /api/admin/users`` → (user id, invite token from the URL fragment)."""
    r = admin.post("/api/admin/users", json={"email": email, "role": role, "send_mail": False})
    assert r.status_code == 201, f"admin REST POST /api/admin/users {email} -> {r.status_code} {r.text[:200]}"
    body = r.json()
    url = body["link"]["url"]
    assert "#" in url, f"admin REST invite link carries no fragment: {url!r}"
    return body["user"]["id"], url.split("#", 1)[1]


def redeem(token: str, password: str) -> requests.Response:
    s = browser()
    r = s.post(f"{API_BASE}/api/auth/links/redeem", json={"token": token, "password": password}, timeout=60)
    r.session = s  # type: ignore[attr-defined]
    return r


def new_user(admin: Person, label: str, rid: str, role: str = "user") -> Person:
    email = f"{label.lower()}-{rid}@ci-auth.example.org"
    uid, token = invite(admin, email, role)
    r = redeem(token, USER_PASSWORD)
    assert r.status_code == 204, f"{label} REST redeem invite -> {r.status_code} {r.text[:200]}"
    p = Person(label, email, USER_PASSWORD, r.session).refresh_me()  # type: ignore[attr-defined]
    assert p.id == uid, f"{label} REST /api/auth/me id {p.id} != invited id {uid}"
    return p


def make_token(p: Person, scope: str, name: str | None = None) -> dict:
    r = p.post("/api/me/tokens", json={"name": name or f"{p.label}-{scope}", "scope": scope})
    assert r.status_code == 201, f"{p.label} REST POST /api/me/tokens {scope} -> {r.status_code} {r.text[:200]}"
    return r.json()


def bearer(raw: str) -> requests.Session:
    """A script with a bearer token and NO cookie and NO Origin."""
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {raw}"
    return s


# ---------------------------------------------------------------------------
# Content (mock LLM)
# ---------------------------------------------------------------------------


def import_cv(p: Person) -> dict:
    with open(CV_FILE, "rb") as fh:
        r = p.post(
            "/api/profile/import-jobs",
            files={"file": ("cv.pdf", fh, "application/pdf")},
            timeout=120,
        )
    assert r.status_code == 202, f"{p.label} REST POST /api/profile/import-jobs -> {r.status_code} {r.text[:200]}"
    import_id = r.json()["import_id"]
    deadline = time.time() + 180
    while time.time() < deadline:
        poll = p.get(f"/api/profile/import-jobs/{import_id}")
        assert poll.status_code == 200, f"{p.label} REST import poll -> {poll.status_code} {poll.text[:200]}"
        body = poll.json()
        if body["status"] == "ready":
            body["import_id"] = import_id
            return body
        assert body["status"] != "failed", f"{p.label} REST import failed: {body}"
        time.sleep(1)
    raise AssertionError(f"{p.label} REST import did not finish in 180 s")


def posting_text(rid: str, title: str = "Platform Engineer") -> str:
    return (
        f"{title} (m/w/d) — Referenz {rid}\n\n"
        "Wir suchen eine erfahrene Person für unser Plattform-Team in Berlin.\n"
        "Aufgaben: Betrieb und Weiterentwicklung unserer Kubernetes-Plattform, "
        "CI/CD-Pipelines, Observability.\n"
        "Anforderungen: 5+ Jahre Erfahrung mit Python, Docker, PostgreSQL; "
        "gute Deutsch- und Englischkenntnisse.\n"
    )


def analyze(p: Person, text: str) -> dict:
    r = p.post("/api/job/analyze", json={"text": text}, timeout=120)
    assert r.status_code == 200, f"{p.label} REST POST /api/job/analyze -> {r.status_code} {r.text[:300]}"
    return r.json()


def applications(p: Person) -> list[dict]:
    r = p.get("/api/applications")
    assert r.status_code == 200, f"{p.label} REST GET /api/applications -> {r.status_code} {r.text[:200]}"
    body = r.json()
    return body["items"] if isinstance(body, dict) and "items" in body else body.get("applications", body)


# ---------------------------------------------------------------------------
# The agent door (stdio MCP inside the lane's backend container)
# ---------------------------------------------------------------------------


def _msg(id_, method, params=None) -> str:
    m: dict = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        m["id"] = id_
    if params is not None:
        m["params"] = params
    return json.dumps(m) + "\n"


@dataclass
class McpRun:
    returncode: int | None
    responses: dict  # id -> response
    stderr: str


def mcp(
    token: str | None,
    calls: list[tuple[str, dict]],
    *,
    after: dict | None = None,
    timeout: float = 90,
) -> McpRun:
    """Start ``python -m applire.mcp`` with ``APPLIRE_AGENT_TOKEN=token`` and run
    ``tools/call`` for each (name, arguments) in ONE process, in order.

    stdin stays open until every request id has an answer (stdin EOF cancels
    in-flight requests — see tests/test_mcp_server.py, PR #202).
    ``after[n]`` (a callable) runs once call ``n`` (1-based) has its answer and
    before call ``n+1`` is sent — e.g. revoke the token mid-session.
    """
    env = ["-e", f"APPLIRE_AGENT_TOKEN={token}"] if token is not None else []
    proc = subprocess.Popen(
        ["docker", "compose", *COMPOSE_ARGS, "exec", "-iT", *env, "backend", "python", "-m", "applire.mcp"],
        cwd=PROJECT_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    responses: dict = {}
    got = threading.Condition()

    def reader() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.strip()
            if not line:
                continue
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            with got:
                if "id" in parsed:
                    responses[parsed["id"]] = parsed
                got.notify_all()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    deadline = time.time() + timeout

    def wait_for(i: int) -> bool:
        with got:
            while i not in responses:
                left = deadline - time.time()
                if left <= 0 or proc.poll() is not None and i not in responses:
                    return i in responses
                got.wait(min(left, 0.5))
            return True

    try:
        proc.stdin.write(_msg(0, "initialize", {  # type: ignore[union-attr]
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "e2e-auth", "version": "0.1"},
        }))
        proc.stdin.flush()  # type: ignore[union-attr]
        if wait_for(0):
            proc.stdin.write(_msg(None, "notifications/initialized"))  # type: ignore[union-attr]
            for n, (name, args) in enumerate(calls, start=1):
                proc.stdin.write(_msg(n, "tools/call", {"name": name, "arguments": args}))  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
                if not wait_for(n):
                    break
                if after and n in after:
                    after[n]()
    except BrokenPipeError:
        pass
    finally:
        try:
            proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    t.join(timeout=5)
    return McpRun(proc.returncode, responses, proc.stderr.read() if proc.stderr else "")  # type: ignore[union-attr]


def mcp_outcome(resp: dict | None) -> tuple[str, object]:
    """Normalise one tools/call answer → ("ok", payload) | ("error", (code, message)) |
    ("tool_error", text) | ("missing", None)."""
    if resp is None:
        return "missing", None
    if "error" in resp:
        return "error", (resp["error"].get("code"), resp["error"].get("message", ""))
    result = resp.get("result", {})
    text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    if result.get("isError"):
        return "tool_error", text
    structured = result.get("structuredContent")
    if structured is not None:
        return "ok", structured
    try:
        return "ok", json.loads(text)
    except ValueError:
        return "ok", text
