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

"""Test that annotate_expected_fields is wired into the CV/LinkedIn import path (US179).

Verifies that after import_from_text, every work_experience entry on the stored
profile has its expected_fields populated (not None) — i.e. the annotation ran
at write time.

MockLLMProvider canned extraction response (_PROFILE_PARSE_RESPONSE) produces:
  - "Senior Software Engineer" at TechVision GmbH  → IC role → expected_fields == []
  - "Software Engineer" at StartupX AG              → IC role → expected_fields == []

Both are IC roles so MockLLMProvider routes them to expected=[], but the key
assertion is that the field is a list, not None.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# ---------------------------------------------------------------------------
# DB fixture (mirrors test_profile_service.py)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sqlite_session():
    """In-memory SQLite session for profile import tests.

    Uses a selective table list (mirrors test_cv_upload.py) to avoid bare JSONB
    columns in models that are not SQLite-safe (job_analyses, generated_cover_letters,
    flow_sessions, etc.).  import_from_text only touches MasterProfile and
    ProfileSnapshot (pre-merge snapshot on second import).
    """
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    MasterProfile.__table__,
                    ProfileSnapshot.__table__,
                ],
            )
        )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_from_text_annotates_expected_fields(sqlite_session):
    """import_from_text stores profiles with expected_fields populated on all work entries.

    MockLLMProvider._PROFILE_PARSE_RESPONSE has two IC work_experience entries:
      - "Senior Software Engineer"  (IC → expected_fields == [])
      - "Software Engineer"         (IC → expected_fields == [])

    The critical assertion is that expected_fields is a list (not None) — proving
    annotate_expected_fields was called at write time (US179 wiring).
    """
    from applire.providers.llm.mock import MockLLMProvider
    from applire.services.profile import import_from_text
    from applire.schemas.profile import MasterProfileData

    provider = MockLLMProvider()
    cv_text = "Senior Software Engineer at TechVision GmbH, 2021–present."

    response = await import_from_text(cv_text, sqlite_session, provider)

    # The stored profile must have work entries with expected_fields set (not None)
    entries = response.profile.work_experience
    assert len(entries) >= 1, "Expected at least one work entry from mock extraction"

    for entry in entries:
        assert entry.expected_fields is not None, (
            f"expected_fields is None on entry '{entry.role}' — "
            "annotate_expected_fields was NOT called during import"
        )
        assert isinstance(entry.expected_fields, list), (
            f"expected_fields must be a list, got {type(entry.expected_fields)}"
        )


@pytest.mark.asyncio
async def test_import_from_text_ic_roles_get_empty_expected_fields(sqlite_session):
    """IC roles ("Senior Software Engineer", "Software Engineer") get expected_fields == [].

    MockLLMProvider routes IC roles (no management keywords) to expected=[], so
    the stored annotation should be an empty list — not None (unannotated) and not
    a non-empty list (which would indicate a management false-positive).
    """
    from applire.providers.llm.mock import MockLLMProvider
    from applire.services.profile import import_from_text

    provider = MockLLMProvider()
    cv_text = "Software Engineer at StartupX AG, 2018–2021."

    response = await import_from_text(cv_text, sqlite_session, provider)
    entries = response.profile.work_experience

    for entry in entries:
        # All canned mock entries are IC roles — should get empty, not None
        assert entry.expected_fields == [], (
            f"IC role '{entry.role}' should have expected_fields=[] "
            f"(not None or management fields), got {entry.expected_fields!r}"
        )
