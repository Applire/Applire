# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""785:L71 — `nginx/16-applire-log-level.sh` ties nginx's error-log detail to the
existing LOG_LEVEL switch (founder ruling E12-785:L71, 2026-10-22): upstream error
lines carry the request's query string (`?sig=`, `?code=`), so they are written
only when the operator turns on LOG_LEVEL=DEBUG. Runs the real script with POSIX
`sh`, output directory as its argument."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "nginx" / "16-applire-log-level.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")


def _run(value: str | None, tmp_path: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k != "LOG_LEVEL"}
    if value is not None:
        env["LOG_LEVEL"] = value
    proc = subprocess.run(
        ["sh", str(_SCRIPT), str(tmp_path / "out")], env=env, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    return (tmp_path / "out" / "error-log.conf").read_text()


@pytest.mark.parametrize("value", ["DEBUG", "debug", " Debug "])
def test_debug_writes_full_upstream_error_lines(value, tmp_path):
    assert _run(value, tmp_path) == "error_log /var/log/nginx/error.log error;\n"


@pytest.mark.parametrize("value", [None, "", "INFO", "WARNING", "ERROR", "verbose"])
def test_anything_else_keeps_request_lines_out(value, tmp_path):
    assert _run(value, tmp_path) == "error_log /var/log/nginx/error.log crit;\n"


def test_image_and_compose_wire_the_script():
    dockerfile = (_ROOT / "nginx" / "Dockerfile").read_text()
    assert "16-applire-log-level.sh /docker-entrypoint.d/16-applire-log-level.sh" in dockerfile
    compose = (_ROOT / "docker-compose.yml").read_text()
    nginx_block = compose.split("\n  nginx:\n", 1)[1].split("\n\n", 1)[0]
    assert "LOG_LEVEL: ${LOG_LEVEL:-INFO}" in nginx_block
    assert "include /etc/nginx/applire/*.conf;" in (_ROOT / "nginx" / "self-hosted.conf").read_text()
