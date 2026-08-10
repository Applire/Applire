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

"""Issue #505 — ``_meta.na_fields`` must survive a profile schema round-trip.

``_meta`` was never a declared field on ``MasterProfileData`` and the schema
sets no ``model_config``, so pydantic's default ``extra="ignore"`` dropped it on
validation.  Every writer that round-trips the profile through
``MasterProfileData.model_validate(...).model_dump()`` therefore wiped the
candidate's N/A suppressions: mark a completeness gap "not applicable", then
save any profile section → the gap came back.

The readers (``services/profile/completeness.py``, ``services/profile/health.py``,
``services/profile/__init__.py``) read ``_meta`` off the *raw* profile JSON, so
the dumped key must stay literally ``_meta`` — not ``meta``.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import MasterProfileData  # noqa: E402

from tests.support.profile_factory import make_master_profile, set_profile_json  # noqa: E402


NA_FIELD = "budget_managed: Senior Engineer @ Logivia"


def _raw_profile(**extra) -> dict:
    profile = {
        "personal_info": {"name": "Sven Hartmann", "email": "sven@example.de"},
        "professional_summary": {"en": "Experienced engineer"},
        "work_experience": [
            {
                "company": "Logivia",
                "role": "Senior Engineer",
                "start_date": "2020-01",
                # Role annotation: without it the reader never emits a
                # budget_managed gap at all and the suppression assertions
                # below would be vacuous.
                "expected_fields": ["budget_managed"],
            }
        ],
        "education": [{"institution": "TU", "degree": "MSc", "field": "CS"}],
        "skills": [
            {"name": "Python", "category": "technical", "proficiency": "advanced"}
        ],
    }
    profile.update(extra)
    return profile


# ─── Schema-level round-trip ─────────────────────────────────────────────────

class TestMetaSurvivesSchemaRoundTrip:
    def test_meta_na_fields_survive_model_dump(self):
        raw = _raw_profile(_meta={"na_fields": [NA_FIELD]})

        dumped = MasterProfileData.model_validate(raw).model_dump()

        assert "_meta" in dumped, "_meta was dropped by the schema round-trip"
        assert dumped["_meta"]["na_fields"] == [NA_FIELD]

    def test_meta_na_fields_survive_json_mode_dump(self):
        """Most writers persist with ``model_dump(mode="json")`` (JSONB column)."""
        raw = _raw_profile(_meta={"na_fields": [NA_FIELD]})

        dumped = MasterProfileData.model_validate(raw).model_dump(mode="json")

        assert dumped["_meta"] == {"na_fields": [NA_FIELD]}

    def test_dumped_key_is_literally_underscore_meta(self):
        """Readers index the raw JSON by ``"_meta"`` — never by ``"meta"``."""
        raw = _raw_profile(_meta={"na_fields": [NA_FIELD]})

        dumped = MasterProfileData.model_validate(raw).model_dump(mode="json")

        assert "meta" not in dumped

    def test_profile_without_meta_does_not_gain_the_key(self):
        """A profile that never had ``_meta`` must not grow a null ``_meta``."""
        dumped = MasterProfileData.model_validate(_raw_profile()).model_dump(mode="json")

        assert "_meta" not in dumped

    def test_unknown_meta_keys_survive(self):
        """``_meta`` is carried as-is — future keys must not be silently dropped."""
        raw = _raw_profile(
            _meta={"na_fields": [NA_FIELD], "some_future_marker": {"a": 1}}
        )

        dumped = MasterProfileData.model_validate(raw).model_dump(mode="json")

        assert dumped["_meta"]["some_future_marker"] == {"a": 1}

    def test_construction_by_python_field_name_is_not_silently_dropped(self):
        """``meta=`` (the python attribute) must populate, not vanish.

        Without ``populate_by_name`` the alias would be the only accepted key
        and ``MasterProfileData(meta=...)`` would silently drop the block —
        the same failure mode as #505 itself.
        """
        profile = MasterProfileData(meta={"na_fields": [NA_FIELD]})

        assert profile.meta is not None
        assert profile.model_dump(mode="json")["_meta"] == {"na_fields": [NA_FIELD]}

    def test_empty_na_fields_round_trip(self):
        raw = _raw_profile(_meta={"na_fields": []})

        dumped = MasterProfileData.model_validate(raw).model_dump(mode="json")

        assert dumped["_meta"] == {"na_fields": []}


class TestAssessHealthReadsCarriedMeta:
    def test_assess_health_honours_the_profiles_own_na_fields(self):
        """No explicit ``na_fields=`` argument: the parsed profile carries it."""
        from applire.services.profile.health import assess_health

        suppressed = MasterProfileData.model_validate(
            _raw_profile(_meta={"na_fields": [NA_FIELD]})
        )
        plain = MasterProfileData.model_validate(_raw_profile())

        assert NA_FIELD in assess_health(plain).completeness.field_gaps
        assert NA_FIELD not in assess_health(suppressed).completeness.field_gaps

    def test_explicit_na_fields_argument_still_overrides(self):
        """Existing callers thread the list in explicitly — keep that working."""
        from applire.services.profile.health import assess_health

        plain = MasterProfileData.model_validate(_raw_profile())

        health = assess_health(plain, na_fields=[NA_FIELD])

        assert NA_FIELD not in health.completeness.field_gaps


# ─── Writer → round-trip → reader (door level) ───────────────────────────────

@pytest_asyncio.fixture
async def sqlite_session():
    from applire.db.session import Base  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.uploads  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _mock_provider():
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="(should not be called)")
    provider.aparse_json = AsyncMock(return_value={})
    provider.__class__.__name__ = "MockProvider"
    return provider


@pytest.fixture
def _stub_gap_and_question(monkeypatch):
    """Deterministic Mode-C enrich launch — no LLM, fixed gap set + question."""
    import applire.routers.profile_enrich as pe

    monkeypatch.setattr(
        pe,
        "gap_detector_mode_c",
        lambda profile_data, scope=None: [
            NA_FIELD,
            "team_size: Senior Engineer @ Logivia",
        ],
    )

    async def _fake_question(state, profile_data, provider, lang="en"):
        gap = state["critical_gaps"][state["current_gap_index"]]
        return {"question": f"Tell me about {gap}"}

    monkeypatch.setattr(pe, "question_generator_with_profile", _fake_question)


class TestNaFieldsWriterToReaderPath:
    @pytest.mark.asyncio
    async def test_na_marker_survives_a_section_save(
        self, sqlite_session, _stub_gap_and_question
    ):
        """#505 door test: mark a gap N/A, then save a profile section.

        ``mark_gap_na`` writes ``_meta.na_fields`` with raw-dict surgery;
        ``patch_profile_section`` round-trips the profile through the schema.
        The suppression must still be there afterwards, and the completeness
        reader must still honour it.
        """
        from applire.models.profile import MasterProfile
        from applire.routers.profile_enrich import mark_gap_na, start_enrich_session
        from applire.schemas.enrich import EnrichStartRequest
        from applire.services.profile import patch_profile_section
        from applire.services.profile.completeness import field_gaps

        record = make_master_profile(profile_json=_raw_profile())
        sqlite_session.add(record)
        await sqlite_session.commit()
        provider = _mock_provider()

        # Non-vacuity: the gap is really emitted while unsuppressed.
        assert NA_FIELD in field_gaps(record.profile_json)

        started = await start_enrich_session(
            EnrichStartRequest(), sqlite_session, provider, None
        )
        assert isinstance(started.session_id, uuid.UUID)
        await mark_gap_na(started.session_id, sqlite_session, provider, None)

        await sqlite_session.refresh(record)
        assert record.profile_json["_meta"]["na_fields"] == [NA_FIELD]
        assert NA_FIELD not in field_gaps(record.profile_json)

        # A completely unrelated section save must not wipe the suppression.
        await patch_profile_section(
            "professional_summary",
            {"en": "Experienced engineer, logistics domain"},
            sqlite_session,
        )

        await sqlite_session.refresh(record)
        assert record.profile_json["_meta"]["na_fields"] == [NA_FIELD]
        assert NA_FIELD not in field_gaps(record.profile_json)

    @pytest.mark.asyncio
    async def test_health_read_still_suppresses_after_a_section_save(
        self, sqlite_session
    ):
        """``get_profile_health`` reads ``_meta.na_fields`` off the raw JSON."""
        from applire.models.profile import MasterProfile
        from applire.services.profile import get_profile_health, patch_profile_section

        record = make_master_profile(
            profile_json=_raw_profile(_meta={"na_fields": [NA_FIELD]})
        )
        sqlite_session.add(record)
        await sqlite_session.commit()

        # Non-vacuity: the same profile without the suppression reports the gap.
        unsuppressed = await get_profile_health(sqlite_session)
        assert NA_FIELD not in unsuppressed.completeness.field_gaps
        set_profile_json(record, _raw_profile())
        await sqlite_session.commit()
        assert NA_FIELD in (
            await get_profile_health(sqlite_session)
        ).completeness.field_gaps
        set_profile_json(record, _raw_profile(_meta={"na_fields": [NA_FIELD]}))
        await sqlite_session.commit()

        await patch_profile_section(
            "professional_summary",
            {"en": "Experienced engineer, logistics domain"},
            sqlite_session,
        )

        health = await get_profile_health(sqlite_session)
        assert NA_FIELD not in health.completeness.field_gaps
