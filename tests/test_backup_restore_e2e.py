# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backup and restore, round trip, on a multi-user instance (JF-O-7.1, JF-O-7.2, SF-DB.1).

Runs ONLY in the real-auth lane (``APPLIRE_E2E_AUTH=1`` and ``APPLIRE_E2E_BACKUP=1``)
against ``docker-compose.ci-auth.yml``: production topology, login on, nginx on a
non-80 port, mock LLM (0 provider calls). CI: job ``real-auth-lane`` in test.yml,
step "Backup and restore round trip" — it DESTROYS the lane's volumes, so it runs
last and on its own.

Local run (own project name and port, never the dev stack's)::

    export APPLIRE_CI_AUTH_PORT=18796
    C="docker compose -p backup-roundtrip -f docker-compose.yml -f docker-compose.ci-auth.yml"
    $C up -d --build
    APPLIRE_E2E_AUTH=1 APPLIRE_E2E_BACKUP=1 APPLIRE_API_BASE=http://localhost:18796 \\
      APPLIRE_E2E_COMPOSE_ARGS="-p backup-roundtrip -f docker-compose.yml -f docker-compose.ci-auth.yml" \\
      pytest tests/test_backup_restore_e2e.py -v
    $C down -v

The scripts are driven exactly as SELF-HOSTING.md tells an operator to drive them:
``scripts/backup.sh`` then ``scripts/restore.sh`` from the folder that holds the
compose file, the stack named through ``COMPOSE_PROJECT_NAME`` / ``COMPOSE_FILE``.
Between the two, the stack and BOTH its volumes are deleted — the restore lands on
an empty host, which is the case a backup exists for.

What must come back, and why each is checked separately:

* every table, row for row (content hash per table) — the vault, applications,
  documents, accounts, tokens, sessions, audit;
* every uploaded file, byte for byte (sha256) — the second volume;
* the instance stays CLAIMED — a restored instance that asked for a setup code
  would hand the admin role to whoever reads the log first;
* each person signs in with their old password and sees only their own data —
  ownership (ADR-092) is in rows, and rows are what a dump carries;
* tokens issued before the backup still work, on REST and over MCP;
* a provider key the admin stored in the panel still decrypts — it is encrypted
  under ``auth.instance_secret`` (ADR-093 cl. 4), which lives in the database: a
  backup that lost it would restore an instance with every stored key unreadable.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_LANE_PATH = Path(__file__).resolve().parent / "e2e-auth" / "lane.py"
_spec = importlib.util.spec_from_file_location("applire_e2e_auth_lane", _LANE_PATH)
if "applire_e2e_auth_lane" in sys.modules:
    lane = sys.modules["applire_e2e_auth_lane"]
else:
    lane = importlib.util.module_from_spec(_spec)
    sys.modules["applire_e2e_auth_lane"] = lane
    _spec.loader.exec_module(lane)

if not (lane.REAL_AUTH_LANE and os.environ.get("APPLIRE_E2E_BACKUP") == "1"):
    pytest.skip(
        "backup round trip only (APPLIRE_E2E_AUTH=1 APPLIRE_E2E_BACKUP=1 against "
        "docker-compose.ci-auth.yml) — it deletes the lane's volumes",
        allow_module_level=True,
    )

ROOT = lane.PROJECT_ROOT
STORED_KEY = "MISTRAL_API_KEY"
STORED_KEY_VALUE = "backup-roundtrip-not-a-real-key-0123456789"
# instance_state keys that legitimately differ: written by backup.sh AFTER the dump
# (so the restored database cannot carry it), and consumed by the first boot of a
# claimed instance (auth/setup.py prepare_boot logs the claim notice once, then
# deletes it — the restored instance tells its operator who claimed it).
STATE_KEYS_NOT_COMPARED = {"last_backup_at", "auth.claim_notice"}
# Logs the restored instance appends to on its own (its retention pass, its provider
# probe): every row from before must still be there; new rows are allowed.
APPEND_ONLY = {"llm_usage", "retention_runs"}


def _operator_env() -> dict[str, str]:
    """``-p X -f a -f b`` as an operator's shell would say it to the scripts."""
    args, project, files = lane.COMPOSE_ARGS, None, []
    for flag, value in zip(args, args[1:]):
        if flag == "-p":
            project = value
        elif flag == "-f":
            files.append(value)
    assert project and files, f"APPLIRE_E2E_COMPOSE_ARGS needs -p and -f: {args}"
    return {**os.environ, "COMPOSE_PROJECT_NAME": project, "COMPOSE_FILE": ":".join(files)}


def _script(*args: str, timeout: float = 600) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(ROOT / "scripts" / args[0]), *args[1:]],
        cwd=ROOT, env=_operator_env(), capture_output=True, text=True, timeout=timeout,
    )


def _table_rows() -> dict[str, set[str]]:
    """Every table in schema public as a set of row hashes (md5 of the row text)."""
    tables = lane.psql(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    ).split()
    assert tables, "no tables in schema public"
    out = {}
    for t in tables:
        where = ""
        if t == "instance_state":
            where = " WHERE key NOT IN (" + ",".join(f"'{k}'" for k in STATE_KEYS_NOT_COMPARED) + ")"
        rows = lane.psql(f'SELECT md5(t::text) FROM "{t}" t{where}')
        out[t] = set(rows.split())
    return out


def _upload_hashes() -> dict[str, str]:
    r = lane.compose(
        "exec", "-T", "backend", "sh", "-c",
        "cd /app/data/uploads && find . -type f -exec sha256sum {} +",
    )
    assert r.returncode == 0, f"listing the uploads volume failed: {r.stderr[-400:]}"
    files = {}
    for line in r.stdout.splitlines():
        digest, name = line.split(maxsplit=1)
        files[name] = digest
    return files


def _view(p) -> dict:
    """What a person sees through the API: their applications, uploads, profile."""
    apps = sorted(a["id"] for a in lane.applications(p))
    r = p.get("/api/profile/uploads")
    assert r.status_code == 200, f"{p.label} REST GET /api/profile/uploads -> {r.status_code}"
    uploads = sorted((u["id"], u["original_filename"], u["byte_size"]) for u in r.json())
    r = p.get("/api/profile")
    assert r.status_code == 200, f"{p.label} REST GET /api/profile -> {r.status_code}"
    profile = hashlib.sha256(r.content).hexdigest()
    return {"applications": apps, "uploads": uploads, "profile": profile}


def _stored_key(admin) -> dict:
    r = admin.get("/api/admin/settings")
    assert r.status_code == 200, f"admin REST GET /api/admin/settings -> {r.status_code}"
    return next(i for i in r.json()["items"] if i["key"] == STORED_KEY)


@pytest.fixture(scope="module")
def before(tmp_path_factory):
    """A two-person instance with content, tokens and an encrypted setting — then backed up."""
    admin = lane.require_admin()
    rid = lane.run_id()
    ada = lane.new_user(admin, "Ada", rid)
    ben = lane.new_user(admin, "Ben", rid)
    for p in (ada, ben):
        lane.import_cv(p)
        lane.analyze(p, lane.posting_text(rid, f"{p.label}'s Platform Engineer"))
    r = admin.s.put(
        f"{lane.API_BASE}/api/admin/settings",
        json={"changes": {STORED_KEY: STORED_KEY_VALUE}}, timeout=30,
    )
    assert r.status_code == 200, f"admin REST PUT /api/admin/settings -> {r.status_code} {r.text[:300]}"
    key = _stored_key(admin)
    assert key["source"] == "panel" and key["is_set"], f"stored key before the backup: {key}"

    w = {
        "admin": admin, "ada": ada, "ben": ben,
        "api_token": lane.make_token(ada, "api")["token"],
        "agent_token": lane.make_token(ada, "agent")["token"],
        "views": {p.label: _view(p) for p in (ada, ben)},
    }
    w["tables"] = _table_rows()
    w["uploads"] = _upload_hashes()
    assert w["uploads"], "the uploads volume is empty — the import stored no file, nothing to prove"

    w["backed_up_at"] = lane.psql("SELECT now()")
    out = tmp_path_factory.mktemp("backups")
    r = _script("backup.sh", str(out))
    assert r.returncode == 0, f"backup.sh exited {r.returncode}:\n{r.stderr[-2000:]}"
    archives = sorted(out.glob("applire-backup-*.tar.gz"))
    assert len(archives) == 1, f"backup.sh wrote {archives}"
    w["archive"] = archives[0]
    w["backup_log"] = r.stderr
    # The data must not have moved while it was being dumped.
    assert _table_rows() == w["tables"], "the database changed during the backup"
    return w


def test_backup_records_itself_and_verifies_its_archive(before):
    log = before["backup_log"]
    assert "recorded last_backup_at=" in log, log[-1500:]
    assert "looks like a usable backup" in log, log[-1500:]
    assert lane.psql("SELECT count(*) FROM instance_state WHERE key='last_backup_at'") == "1"
    r = _script("backup.sh", "--verify", str(before["archive"]))
    assert r.returncode == 0, f"backup.sh --verify exited {r.returncode}:\n{r.stderr[-1500:]}"


def test_restore_refuses_to_overwrite_an_instance_that_has_data(before):
    r = _script("restore.sh", str(before["archive"]))
    assert r.returncode != 0, "restore.sh overwrote a database that holds data without --force"
    assert "already contains data" in r.stderr, r.stderr[-1500:]
    assert _table_rows() == before["tables"], "a refused restore changed the database"


@pytest.fixture(scope="module")
def restored(before):
    """Delete the stack and both volumes, then restore onto the empty host."""
    env = _operator_env()
    project = env["COMPOSE_PROJECT_NAME"]
    r = lane.compose("down", "-v", timeout=300)
    assert r.returncode == 0, f"down -v failed: {r.stderr[-800:]}"
    left = subprocess.run(
        ["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert not left, f"volumes survived the teardown: {left}"

    r = _script("restore.sh", str(before["archive"]), timeout=900)
    assert r.returncode == 0, f"restore.sh exited {r.returncode}:\n{r.stderr[-3000:]}\n{r.stdout[-1500:]}"
    lane.compose("up", "-d", "--no-build", timeout=300)
    return {"log": r.stderr + r.stdout}


def test_every_table_comes_back_row_for_row(before, restored):
    after = _table_rows()
    tables = set(before["tables"]) | set(after)
    assert set(before["tables"]) == set(after), f"tables differ: {set(before['tables']) ^ set(after)}"
    lost = {t: len(before["tables"][t] - after[t]) for t in tables if before["tables"][t] - after[t]}
    added = {t: len(after[t] - before["tables"][t]) for t in tables if after[t] - before["tables"][t]}
    # The restored instance's own retention pass may purge invite/reset links that
    # were used or expired before the backup — and must say so in its run record.
    purged = sum(
        int(n or 0) for n in lane.psql(
            "SELECT report->>'auth_links_deleted' FROM retention_runs "
            f"WHERE run_at > '{before['backed_up_at']}'"
        ).split()
    )
    assert lost.get("auth_links", 0) <= purged, (
        f"auth_links lost {lost.get('auth_links')} rows, the retention runs report {purged} purged"
    )
    lost.pop("auth_links", None)
    added.pop("auth_links", None)
    assert not lost, f"rows lost in the restore (table: count): {lost}"
    unexpected = {t: n for t, n in added.items() if t not in APPEND_ONLY}
    assert not unexpected, f"rows appeared after the restore (table: count): {unexpected}"


def test_every_uploaded_file_comes_back_byte_for_byte(before, restored):
    assert _upload_hashes() == before["uploads"]


def test_the_restored_instance_stays_claimed(restored):
    import requests

    state = requests.get(f"{lane.API_BASE}/api/auth/state", timeout=30).json()
    assert state.get("setup_required") is False, f"the restored instance asks for setup again: {state}"
    logs = lane.compose("logs", "--no-color", "backend").stdout
    assert not lane._SETUP_CODE_RE.search(logs), "the restored backend printed a setup code"


def test_each_person_signs_in_with_the_old_password_and_sees_only_their_own(before, restored):
    admin = lane.sign_in("admin", before["admin"].email, before["admin"].password)
    assert admin.role == "admin", f"admin came back as {admin.role}"
    for label in ("ada", "ben"):
        old = before[label]
        p = lane.sign_in(old.label, old.email, old.password)
        assert p.id == old.id, f"{old.label} came back with another id"
        assert _view(p) == before["views"][old.label], f"{old.label} sees different data after the restore"
    ada_apps = set(before["views"]["Ada"]["applications"])
    ben_apps = set(before["views"]["Ben"]["applications"])
    assert ada_apps and ben_apps and not ada_apps & ben_apps


def test_tokens_issued_before_the_backup_still_work(before, restored):
    s = lane.bearer(before["api_token"])
    r = s.get(f"{lane.API_BASE}/api/applications", timeout=30)
    assert r.status_code == 200, f"Ada REST+api-bearer after the restore -> {r.status_code}"
    ids = {a["id"] for a in r.json()["items"]}
    assert ids == set(before["views"]["Ada"]["applications"]), ids

    run = lane.mcp(before["agent_token"], [("list_applications", {})])
    assert run.returncode == 0, f"Ada MCP stdio exited {run.returncode}: {run.stderr[-400:]}"
    kind, listed = lane.mcp_outcome(run.responses.get(1))
    assert kind == "ok", f"Ada MCP list_applications after the restore -> {kind} {listed!r}"
    items = listed.get("items", listed.get("result", listed)) if isinstance(listed, dict) else listed
    assert {a["id"] for a in items} == set(before["views"]["Ada"]["applications"])


def test_a_provider_key_stored_in_the_panel_still_decrypts(before, restored):
    admin = lane.sign_in("admin", before["admin"].email, before["admin"].password)
    key = _stored_key(admin)
    # An unreadable ciphertext falls back to the env source (instance_settings.build_response).
    assert key["source"] == "panel" and key["is_set"], f"{STORED_KEY} after the restore: {key}"
