# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""ADR-039 ATS round-trip suite configuration.

These tests render real PDFs via Playwright Chromium and audit the extracted
text. Chromium is required; when it is unavailable the whole suite is skipped
(never failed) so the round-trip guarantee degrades gracefully off-CI.
"""
import os
import sys
from pathlib import Path

import pytest

# Make the applire package importable without PYTHONPATH (mirrors
# tests/unit/conftest.py; deliberately omits its DB fixtures).
_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# config.py needs a DATABASE_URL to import; no DB is touched by these tests.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")


@pytest.fixture(scope="session", autouse=True)
def docker_environment():
    """Override the parent conftest's Docker-stack fixture (mirrors tests/unit/).

    The round-trip suite renders PDFs in-process via Playwright — it never
    talks to the API, so it must not wait for (or start) the Docker stack.
    Without this override, environments without a running stack (CI's
    backend-unit-tests job) fail with "API did not become ready within 120s".
    """
    yield


def pytest_collection_modifyitems(config, items):
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
    except Exception:
        skip = pytest.mark.skip(
            reason="Playwright Chromium not available — ATS round-trip needs it"
        )
        for item in items:
            item.add_marker(skip)
