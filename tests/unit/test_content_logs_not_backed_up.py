# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Content logs live on their own volume and never in a backup (ADR-017
amended 2026-11-18; C-4).

The retention worker must reach them (shared volume), and the archive
``scripts/backup.sh`` writes must never contain them: it holds the database
dump, the uploads volume and a manifest, nothing else, and the log volume is
not inside the uploads volume.
"""

import re
from pathlib import PurePosixPath, Path

import pytest
import yaml

from applire.config import Settings

_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _ROOT / "docker-compose.yml"
_BACKUP = _ROOT / "scripts" / "backup.sh"
_WORKDIR = PurePosixPath("/app")  # backend/Dockerfile WORKDIR


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


def _mounts(service: dict) -> dict[str, str]:
    out = {}
    for spec in service.get("volumes") or []:
        source, target = str(spec).split(":")[:2]
        out[target] = source
    return out


@pytest.mark.parametrize("service", ["backend", "retention", "mcp"])
def test_the_log_volume_is_shared_by_writer_and_worker(compose, service):
    mounts = _mounts(compose["services"][service])
    assert mounts.get("/app/logs") == "applire_logs"
    assert "applire_logs" in compose["volumes"]


def test_both_content_log_directories_are_on_the_log_volume_outside_uploads(compose):
    defaults = Settings.model_fields
    uploads = PurePosixPath("/app/data/uploads")
    assert _mounts(compose["services"]["backend"])[str(uploads)] == "applire_uploads"
    for field in ("content_log_dir", "llm_debug_log_dir"):
        resolved = _WORKDIR / defaults[field].default
        assert PurePosixPath("/app/logs") in resolved.parents, field
        assert uploads not in resolved.parents and resolved != uploads, field


def test_the_backup_archive_holds_no_log_file():
    script = _BACKUP.read_text(encoding="utf-8")
    archive_lines = [line for line in script.splitlines() if re.search(r"\btar -czf \"\$ARCHIVE\"", line)]
    assert len(archive_lines) == 1
    members = archive_lines[0].split('-C "$TMP"', 1)[1].split()
    assert members == ["db.dump", "uploads.tar", "manifest.txt"]
    # The only volume it archives is the one mounted at the uploads path.
    assert re.search(r'UPLOADS_VOLUME="\$\(volume_at "\$BACKEND_CID" /app/data/uploads\)"', script)
    for forbidden in ("applire_logs", "/app/logs", "logs/llm", "logs/content"):
        assert forbidden not in script, forbidden
