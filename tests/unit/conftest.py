"""
Unit test configuration.

Overrides the session-scoped docker_environment autouse fixture from the
parent conftest.py so that unit tests run without starting Docker.
"""
import os
import sys
from pathlib import Path

import pytest

# Make the applire package importable when running pytest tests/ without PYTHONPATH.
_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

# Provide required settings so config.py can be imported without a real DB.
# ADR-091 cl. 3 (c): a Postgres harness database name must end in _test/_ci.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/unit_test")
# ADR-091 cl. 3: the unit tiers run on the NoAuth harness (AUTH_PROVIDER now
# defaults to `local`; CI's unit step sets neither) — 1a NEEDS-EDIT, accepted by main.
os.environ.setdefault("AUTH_HARNESS", "true")

# ADR-092 cl. 8 test bootstrap (Strawberry F12): the sync autouse owner context
# (harness user) and its opt-out marker ``no_owner_context``.
from tests.support.owners import configure_test_guard, harness_owner_context, register_markers  # noqa: E402,F401


def pytest_configure(config):
    register_markers(config)
    # MD-24 (1): the statement guard runs ON in the unit suites, on every engine
    # (``APPLIRE_TEST_OWNER_GUARD=off|report`` for the isolation suite's second arm
    # and diagnostics).
    configure_test_guard()


@pytest.fixture(scope="session", autouse=True)
def docker_environment():
    """Unit tests do not require Docker."""
    yield
