# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""`release.yml` attaches its own install assets (ADR-032 amended 2026-10-07, #739).

The README's install path downloads `docker-compose.yml` and `env.example` from
`releases/latest/download/`. A Release published without them 404s that path
for every new self-hoster. It happened twice: v0.40.0-beta (2026-08-30) and
v0.42.0-beta (2026-10-01, web-UI publish). The assets gate held `:latest`
correctly both times, but somebody had to upload the files by hand.

Two layers:
1. **Structure.** The attach job runs on release events from the tagged checkout.
   The assets gate waits for it, and the jobs that move tags still wait for the
   gate.
2. **A local reproduction of the asset step.** The job's own `run:` block, taken
   verbatim from the workflow, runs in bash against a stubbed `gh` and `curl`
   whose "Release" is a directory. These scenarios are the step's contract.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_YML = _REPO_ROOT / ".github" / "workflows" / "release.yml"
_README_FILES = (_REPO_ROOT / "README.md", _REPO_ROOT / "README.de.md")
_ASSETS = ("docker-compose.yml", "env.example")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(_RELEASE_YML.read_text(encoding="utf-8"))


def _attach_step(workflow: dict) -> dict:
    steps = workflow["jobs"]["attach-assets"]["steps"]
    matches = [s for s in steps if "gh release upload" in s.get("run", "")]
    assert len(matches) == 1, "attach-assets must have exactly one upload step"
    return matches[0]


# ---------------------------------------------------------------------------
# 1. Structure
# ---------------------------------------------------------------------------


def test_attach_job_runs_on_release_events_from_the_tag_with_write_scope(workflow):
    job = workflow["jobs"]["attach-assets"]
    assert job["if"].replace(" ", "") == "${{github.event_name=='release'}}"
    assert job["permissions"] == {"contents": "write"}
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"]["ref"] == "${{ github.event.release.tag_name }}"
    env = _attach_step(workflow)["env"]
    # Untrusted-input hygiene: the tag reaches the shell only through env.
    assert env["TAG"] == "${{ github.event.release.tag_name }}"
    assert "${{" not in _attach_step(workflow)["run"]


def test_no_other_job_holds_a_write_grant_on_contents(workflow):
    writers = [
        name
        for name, job in workflow["jobs"].items()
        if (job.get("permissions") or {}).get("contents") == "write"
    ]
    assert writers == ["attach-assets"]


def test_the_assets_gate_waits_for_the_attach_and_still_gates_every_tag_mover(workflow):
    jobs = workflow["jobs"]
    gate = jobs["assets-gate"]
    needs = gate["needs"] if isinstance(gate["needs"], list) else [gate["needs"]]
    assert "attach-assets" in needs
    cond = gate["if"].replace(" ", "")
    # Runs after a skipped attach (manual dispatch) ...
    assert "!cancelled()" in cond
    # ... but not after a FAILED attach on an event that moves :latest.
    assert "needs.attach-assets.result!='failure'" in cond
    assert "github.event.release.prerelease==false" in cond
    for mover in ("merge-backend", "merge-frontend", "build-nginx"):
        mover_needs = jobs[mover]["needs"]
        mover_needs = mover_needs if isinstance(mover_needs, list) else [mover_needs]
        assert "assets-gate" in mover_needs, f"{mover} no longer waits for the assets gate"
        assert "if" not in jobs[mover], f"{mover} must keep the default success() gate"


def test_the_attached_names_are_the_names_the_readme_downloads(workflow):
    run = _attach_step(workflow)["run"]
    for name in _ASSETS:
        assert f'"$staging/{name}"' in run
        for readme in _README_FILES:
            assert f"releases/latest/download/{name}" in readme.read_text(encoding="utf-8"), (
                f"{readme.name} no longer downloads {name}; update the attach step with it"
            )
    assert "cp .env.example \"$staging/env.example\"" in run


# ---------------------------------------------------------------------------
# 2. Local reproduction of the asset step
# ---------------------------------------------------------------------------

_GH_STUB = r"""#!/usr/bin/env bash
# Stub of the three `gh release` calls the step makes. The Release is $RELEASE_DIR.
set -euo pipefail
echo "gh $*" >> "$CALL_LOG"
[ "$1" = "release" ] || exit 2
sub="$2"; shift 2
tag="$1"; shift
case "$sub" in
  download)
    pattern=""; dir=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --pattern) pattern="$2"; shift 2;;
        --dir) dir="$2"; shift 2;;
        *) shift;;
      esac
    done
    [ -f "$RELEASE_DIR/$pattern" ] || { echo "no assets match the file pattern" >&2; exit 1; }
    cp "$RELEASE_DIR/$pattern" "$dir/$pattern";;
  upload)
    file="$1"
    [ -n "${GH_UPLOAD_NOOP:-}" ] && exit 0
    cp "$file" "$RELEASE_DIR/$(basename "$file")";;
  view)
    ls -1 "$RELEASE_DIR";;
  *) exit 2;;
esac
"""

_CURL_STUB = r"""#!/usr/bin/env bash
# Stub of `curl -fsSL <url> -o <out>`: serves $RELEASE_DIR/<basename url>.
set -euo pipefail
url=""; out=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2;;
    -*) shift;;
    *) url="$1"; shift;;
  esac
done
echo "curl $url" >> "$CALL_LOG"
name="$(basename "$url")"
if [ -n "${CURL_SERVE_OVERRIDE:-}" ]; then printf '%s' "$CURL_SERVE_OVERRIDE" > "$out"; exit 0; fi
[ -f "$RELEASE_DIR/$name" ] || exit 22
cp "$RELEASE_DIR/$name" "$out"
"""


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_step(workflow: dict, tmp_path: Path, *, release: dict[str, str], extra_env=None):
    """Run the attach step's bash verbatim in a fake tagged checkout."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "docker-compose.yml").write_text("services: {tagged: compose}\n")
    (checkout / ".env.example").write_text("# tagged env template\n")
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    for name, content in release.items():
        (release_dir / name).write_text(content)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    _write_exec(stubs / "gh", _GH_STUB)
    _write_exec(stubs / "curl", _CURL_STUB)
    call_log = tmp_path / "calls.log"
    call_log.touch()

    step = _attach_step(workflow)
    env = {
        "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "RELEASE_DIR": str(release_dir),
        "CALL_LOG": str(call_log),
        "TAG": "v9.9.9-beta",
        "REPO": "Applire/Applire",
        "GH_TOKEN": "stub",
        "VERIFY_TRIES": step["env"]["VERIFY_TRIES"],
        "VERIFY_SLEEP": "0",
    }
    env.update(extra_env or {})
    proc = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    uploads = [line for line in call_log.read_text().splitlines() if "release upload" in line]
    served = {p.name: p.read_text() for p in release_dir.iterdir()}
    return proc, uploads, served


def test_a_release_published_without_assets_gets_both_from_the_tag(workflow, tmp_path):
    """The v0.42.0-beta shape: web-UI publish, no asset box used."""
    proc, uploads, served = _run_step(workflow, tmp_path, release={})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(uploads) == 2
    assert served == {
        "docker-compose.yml": "services: {tagged: compose}\n",
        "env.example": "# tagged env template\n",
    }


def test_assets_identical_to_the_tag_are_kept_without_an_upload(workflow, tmp_path):
    """The `gh release create … docker-compose.yml env.example` shape, or a promotion."""
    proc, uploads, _ = _run_step(
        workflow,
        tmp_path,
        release={
            "docker-compose.yml": "services: {tagged: compose}\n",
            "env.example": "# tagged env template\n",
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert uploads == []
    assert proc.stdout.count("Kept.") == 2


def test_an_asset_from_another_checkout_is_replaced_with_a_warning(workflow, tmp_path):
    proc, uploads, served = _run_step(
        workflow,
        tmp_path,
        release={"docker-compose.yml": "services: {stale: main}\n"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "::warning::docker-compose.yml on the Release differs" in proc.stdout
    assert len(uploads) == 2  # the replaced compose file + the missing env.example
    assert served["docker-compose.yml"] == "services: {tagged: compose}\n"


def test_an_upload_that_does_not_land_fails_the_job(workflow, tmp_path):
    proc, _, _ = _run_step(workflow, tmp_path, release={}, extra_env={"GH_UPLOAD_NOOP": "1"})
    assert proc.returncode != 0
    assert "still not listed on Release" in proc.stdout


def test_a_served_file_that_differs_from_the_tag_fails_the_job(workflow, tmp_path):
    proc, _, _ = _run_step(
        workflow, tmp_path, release={}, extra_env={"CURL_SERVE_OVERRIDE": "something else"}
    )
    assert proc.returncode != 0
    assert "serves a file that differs" in proc.stdout
