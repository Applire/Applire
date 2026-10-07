# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""`scripts/backup.sh` finds the right stack, or says which ones exist (#739, F3).

The 0.43 upgrade by a blind operator (friction F3) ran the script from another
checkout against a compose folder whose `name:` differs from the folder name.
Before this change the script had two problems:
* A stack started with `docker compose -p <name>`, or a folder without a compose
  file, failed with "the postgres container is not running — start the stack
  first". That is false: the stack was running.
* The archive manifest recorded the FOLDER name as `compose_project`, not the
  project the backup came from.

These tests drive the script against a stubbed `docker` (no daemon needed). The
real-stack before/after runs are in the WP-S report.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKUP_SH = _REPO_ROOT / "scripts" / "backup.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# Stub docker. `compose config` prints `name: $STUB_PROJECT` when set, else fails
# (no compose file). `ps -q` with a project filter finds nothing, so every case
# here ends in the "not running" branch. `ps --format` lists $STUB_STACKS for both
# the postgres and the backend service.
_DOCKER_STUB = r"""#!/usr/bin/env bash
case "$1" in
  compose)
    if [ "$2" = config ] && [ -n "${STUB_PROJECT:-}" ]; then
      printf 'name: %s\nservices:\n  postgres:\n    environment:\n      POSTGRES_PASSWORD: secret-never-printed\n' "$STUB_PROJECT"
      exit 0
    fi
    echo "no configuration file provided: not found" >&2; exit 1;;
  ps)
    for a in "$@"; do
      if [ "$a" = "-q" ]; then exit 0; fi
    done
    for s in ${STUB_STACKS:-}; do echo "$s"; done
    exit 0;;
esac
exit 0
"""


def _run(tmp_path: Path, **env_extra: str) -> subprocess.CompletedProcess:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    docker = stubs / "docker"
    docker.write_text(_DOCKER_STUB, encoding="utf-8")
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    work = tmp_path / "folder"
    work.mkdir()
    env = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        **env_extra,
    }
    return subprocess.run(
        ["bash", str(_BACKUP_SH), str(tmp_path / "out")],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_a_folder_that_resolves_to_a_stopped_project_names_the_running_stacks(tmp_path):
    proc = _run(tmp_path, STUB_PROJECT="folder", STUB_STACKS="applire mine")
    assert proc.returncode != 0
    assert "no running postgres container in compose project 'folder'" in proc.stderr
    assert "Applire stacks running on this host: applire, mine." in proc.stderr
    assert "COMPOSE_PROJECT_NAME=<project>" in proc.stderr
    assert "start the stack first" not in proc.stderr
    assert "secret-never-printed" not in proc.stdout + proc.stderr


def test_no_compose_file_and_no_name_says_so_instead_of_not_running(tmp_path):
    proc = _run(tmp_path, STUB_STACKS="applire")
    assert proc.returncode != 0
    assert "no docker-compose.yml in" in proc.stderr
    assert "COMPOSE_PROJECT_NAME is not set" in proc.stderr
    assert "Applire stacks running on this host: applire." in proc.stderr


def test_compose_project_name_is_used_without_a_compose_file(tmp_path):
    proc = _run(tmp_path, COMPOSE_PROJECT_NAME="myapplire", STUB_STACKS="")
    assert proc.returncode != 0
    # No other stack running: the plain "start it" advice is the right one here.
    assert "the postgres container of compose project 'myapplire' is not running" in proc.stderr


def test_the_manifest_records_the_resolved_project_not_the_folder_name():
    text = _BACKUP_SH.read_text(encoding="utf-8")
    assert 'echo "compose_project=${PROJECT}"' in text
    assert "basename \"$PWD\"" not in text


def test_containers_are_addressed_by_id_so_a_separate_folder_works():
    """`docker compose exec` needs this folder to resolve to the stack. `docker exec
    <id>` only needs the id, which the project-label lookup already found."""
    text = _BACKUP_SH.read_text(encoding="utf-8")
    assert "docker compose exec" not in text
    assert "label=com.docker.compose.project=$PROJECT" in text
    assert "label=com.docker.compose.oneoff=False" in text
