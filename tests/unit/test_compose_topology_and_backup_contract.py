# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""Two contracts that live OUTSIDE Python and would otherwise be unguarded (ADR-087).

1. The **topology cue** (`JF-O-1.2`). `APPLIRE_TOPOLOGY=dev` is a claim made by
   `docker-compose.override.yml` and by nothing else — `production` is true
   precisely because the shipped compose file stays silent. If someone ever put
   the variable into `docker-compose.yml` "for clarity", every real install would
   start announcing a topology instead of deriving it, and no Python test would
   notice. Also the credential variables (`JF-O-4.3`): the three `${POSTGRES_...}`
   forms have to reach BOTH the postgres service and every `DATABASE_URL`, or an
   operator who sets a password gets a database they cannot connect to.

2. The **cross-language key pin** (`SF-CFG.5`). `scripts/backup.sh` writes
   `last_backup_at` into `instance_state` from shell — outside every type
   checker — and WP-O1's ops probe reads it back through the Python constant.
   Nothing else in the codebase joins those two spellings. A typo in the script
   would leave the probe reporting "last backup: never" forever, on an instance
   that is in fact backed up nightly: a silent failure of a control whose whole
   job is to make a silent failure visible.
"""

import re
from pathlib import Path

import pytest
import yaml

from applire.models.instance_state import KEY_LAST_BACKUP_AT

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_OVERRIDE = _REPO_ROOT / "docker-compose.override.yml"
_BACKUP_SH = _REPO_ROOT / "scripts" / "backup.sh"
_RESTORE_SH = _REPO_ROOT / "scripts" / "restore.sh"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def override() -> dict:
    return yaml.safe_load(_OVERRIDE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 1. The topology cue
# --------------------------------------------------------------------------


def test_the_dev_override_declares_the_dev_topology(override):
    env = override["services"]["backend"]["environment"]
    assert env.get("APPLIRE_TOPOLOGY") == "dev", (
        "docker-compose.override.yml must set APPLIRE_TOPOLOGY=dev on the backend "
        "service — it is the only thing that tells a source clone it is running a "
        "debugging topology with an unauthenticated :8001 and Postgres on :5433."
    )


def test_the_production_compose_file_never_names_the_topology(compose):
    # Not a YAML lookup but a raw text search: the variable must not appear in a
    # comment, a second service, or an x- anchor either. "production" has to be
    # the CODE default that nothing overrides, or the cue means nothing.
    raw = _COMPOSE.read_text(encoding="utf-8")
    assert "APPLIRE_TOPOLOGY" not in raw, (
        "docker-compose.yml must never mention APPLIRE_TOPOLOGY. The value "
        "'production' is true because the shipped file stays silent; naming it "
        "here would make the dev cue indistinguishable from a stale override."
    )


# --------------------------------------------------------------------------
# 2. The credential variables
# --------------------------------------------------------------------------


def test_postgres_service_takes_its_credentials_from_the_environment(compose):
    env = compose["services"]["postgres"]["environment"]
    assert env["POSTGRES_USER"] == "${POSTGRES_USER:-applire}"
    assert env["POSTGRES_PASSWORD"] == "${POSTGRES_PASSWORD:-applire}"
    assert env["POSTGRES_DB"] == "${POSTGRES_DB:-applire}"


def test_every_database_url_is_built_from_the_same_credential_variables(compose):
    # backend, retention and mcp each carry their own DATABASE_URL. One of them
    # left hard-coded is the failure an operator only meets after setting a real
    # password: two services connect and the third does not.
    expected = (
        "postgresql+asyncpg://${POSTGRES_USER:-applire}:${POSTGRES_PASSWORD:-applire}"
        "@postgres:5432/${POSTGRES_DB:-applire}"
    )
    urls = {
        name: svc.get("environment", {}).get("DATABASE_URL")
        for name, svc in compose["services"].items()
        if isinstance(svc.get("environment"), dict)
        and "DATABASE_URL" in svc["environment"]
    }
    assert urls, "no service declares DATABASE_URL — the extraction is broken"
    assert set(urls) == {"backend", "retention", "mcp"}, urls
    for name, url in urls.items():
        assert url == expected, f"{name}'s DATABASE_URL does not use the variables: {url}"


def test_the_defaults_are_todays_values_so_an_existing_install_is_unchanged(compose):
    # The whole point of `:-applire` is that upgrading and changing nothing
    # changes nothing. A different default would silently break every install.
    raw = _COMPOSE.read_text(encoding="utf-8")
    for var in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        assert f"${{{var}:-applire}}" in raw, var


# --------------------------------------------------------------------------
# 3. The cross-language key pin
# --------------------------------------------------------------------------


def test_backup_script_writes_the_same_instance_state_key_python_reads():
    """The ONLY thing joining `scripts/backup.sh` to `KEY_LAST_BACKUP_AT`.

    The script is shell; the reader is Python. No type checker, no import and no
    linter spans the two. If this assertion is deleted, a typo in the script is
    invisible until an operator wonders why their nightly backup never registers.
    """
    script = _BACKUP_SH.read_text(encoding="utf-8")
    match = re.search(r'^LAST_BACKUP_KEY="([^"]+)"$', script, re.M)
    assert match, "scripts/backup.sh no longer declares LAST_BACKUP_KEY"
    assert match.group(1) == KEY_LAST_BACKUP_AT, (
        f"scripts/backup.sh writes {match.group(1)!r} but Python reads "
        f"{KEY_LAST_BACKUP_AT!r} — the ops probe would report 'never' forever."
    )
    # And the literal is actually used in the SQL, not merely declared.
    assert "'${LAST_BACKUP_KEY}'" in script


def test_neither_script_ever_deletes_a_volume():
    """`down -v` may not appear in a recovery script (E060 4.2 boundary, JF-O-7.3).

    Deleting a volume is the operator's decision, made with the command in front
    of them. A script that takes it for them is how a restore becomes the thing
    that destroys the data it was called to recover.
    """
    for path in (_BACKUP_SH, _RESTORE_SH):
        text = path.read_text(encoding="utf-8")
        # Strip comments first — both scripts explain in prose WHY they do not do
        # this, and that explanation must not be what the test matches on.
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        assert "down -v" not in code, f"{path.name} contains `down -v` in executable code"
        assert "volume rm" not in code, f"{path.name} removes a volume"


def test_both_scripts_fail_loudly_rather_than_reporting_a_partial_backup():
    """`set -euo pipefail` is the difference between a backup and a hope."""
    for path in (_BACKUP_SH, _RESTORE_SH):
        assert "set -euo pipefail" in path.read_text(encoding="utf-8"), path.name
