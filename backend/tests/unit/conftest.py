"""
Unit test conftest — overrides the session-scoped docker_environment fixture
from the parent conftest so unit tests can run without a live Docker environment.
Sets DATABASE_URL before any app module is imported to satisfy the Settings validator.
"""
import os
import uuid

# Must be set before app modules are imported (pydantic Settings validates at import time)
# ADR-091 cl. 3 (c): the harness proof for this tree is an IN-MEMORY SQLite URL
# (was a ./test.db file, which outlives the run and so is no proof).
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite://")
# ADR-091 cl. 3: the unit tiers run on the NoAuth harness (AUTH_PROVIDER now
# defaults to `local`; CI's unit step sets neither) — 1a NEEDS-EDIT, accepted by main.
os.environ.setdefault("AUTH_HARNESS", "true")

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from applire.db.session import Base, get_db
from applire.main import app
from applire.schemas.profile import MasterProfileData

# ADR-092 cl. 8 test bootstrap (Strawberry F12): the sync autouse owner context,
# the opt-out marker, and the two-user fixture.
from tests.support.owners import (  # noqa: E402,F401
    configure_test_guard,
    harness_owner_context,
    register_markers,
    two_users,
)


def pytest_configure(config):
    register_markers(config)
    # MD-24 (1): the statement guard runs ON in the unit suites, on every engine
    # (``APPLIRE_TEST_OWNER_GUARD=off|report`` for the isolation suite's second arm
    # and diagnostics).
    configure_test_guard()


@pytest.fixture(scope="session", autouse=True)
def docker_environment():
    """No-op override: unit tests use TestClient and need no running services."""
    yield


@pytest_asyncio.fixture
async def async_db(harness_owner_context):
    """Create an in-memory SQLite database for testing.

    Depends on ``harness_owner_context`` so the owner context is set BEFORE
    ``create_all`` (its PRAGMA statements name the owned tables — ADR-092 cl. 8).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Create session factory
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Create and yield a session
    async with async_session() as session:
        yield session

    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def async_client(async_db: AsyncSession):
    """Create an async HTTP client with database dependency override."""
    async def override_get_db():
        yield async_db

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.pop(get_db, None)


@pytest_asyncio.fixture
async def seed_profile(async_db: AsyncSession):
    """Fixture to seed a profile into the database."""
    from applire.models.profile import MasterProfile

    from tests.support.profile_factory import make_master_profile

    async def _seed(profile_data: MasterProfileData) -> MasterProfile:
        # ADR-063 clause 6 is strict since #480 PR 9: a fixture builds the vault
        # through the same authorised door production uses.
        record = make_master_profile(
            profile_json=profile_data.model_dump(mode="json")
        )
        async_db.add(record)
        await async_db.commit()
        await async_db.refresh(record)
        return record

    return _seed


@pytest_asyncio.fixture
async def seed_application(async_db: AsyncSession):
    """Insert a minimal Application row for testing.

    Inserts both a JobAnalysis (required FK target) and the Application.
    """
    from applire.models.application import Application, UserStatus
    from applire.models.job import JobAnalysis

    # The detail routes are scoped to the authenticated user (dFMEA SF-APP.1),
    # so seeded rows must belong to the NoAuthProvider stub user the test app
    # resolves — a random user_id now yields an (intended) 404.
    from applire.auth.no_auth import _STUB_USER_ID

    async def _seed(*, user_status: str = UserStatus.tracking.value) -> Application:
        # Insert a minimal JobAnalysis for the FK
        ja = JobAnalysis(
            raw_text_hash=f"hash-{uuid.uuid4()}",
            raw_text="Sample job description",
            role_title="Software Engineer",
            seniority_level="mid",
            language_requirement="English",
        )
        async_db.add(ja)
        await async_db.flush()  # populate ja.id

        app = Application(
            user_id=_STUB_USER_ID,
            job_analysis_id=ja.id,
            user_status=user_status,
            company_name="Acme",
            role_title="Engineer",
        )
        async_db.add(app)
        await async_db.commit()
        await async_db.refresh(app)
        return app

    return _seed
