"""
Iteration 11 — Master Profile Data Foundation (unit tests)

Done when:
  - GET /api/profile returns a fully structured MasterProfileResponse.
  - Uploading two conflicting CVs flags a conflict without data loss.
  - enrichment_history reflects every change.
  - All unit tests pass.

Covers:
  - Pydantic model defaults and validation
  - Legacy field migration (work_history, contact, skills list)
  - Completeness score weighting
  - SQLite persistence via ORM (JSONB.with_variant(JSON) — no raw DDL needed)
  - Conflict and ConflictResolutionRequest schemas

US184: the lexical merge_profiles / _merge_work_experience / _merge_skills /
date-helper tests that lived here were retired with the lexical engine — merge
behavior is now covered by the ADR-046 engine tests (test_reconcile_apply.py)
and the import-bridge tests (test_merge_changes.py, test_import_reconcile_bridge.py).

No Docker or real Postgres required.

Run:
    pytest tests/unit/ -v
"""
import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sqlite_session():
    """In-memory SQLite session.  JSONB.with_variant(JSON) lets create_all work directly."""
    import importlib
    import pkgutil

    from applire.db.session import Base
    import applire.models as _models_pkg

    # `create_all` builds every table registered on `Base`, and `applications`
    # carries FKs into `generated_cvs`/`generated_cover_letters`. Naming a
    # hand-picked subset left this fixture dependent on some OTHER test module
    # having imported the rest first — it passed inside the full suite and
    # failed standalone. Register the whole package instead, so the order the
    # suite happens to run in cannot decide whether this file works.
    for _module in pkgutil.iter_modules(_models_pkg.__path__):
        importlib.import_module(f"applire.models.{_module.name}")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _work_entry(**kwargs):
    from applire.schemas.profile import WorkEntry
    defaults = {"company": "Acme GmbH", "role": "Software Developer", "start_date": "2020-01"}
    return WorkEntry(**{**defaults, **kwargs})


def _profile(**kwargs):
    from applire.schemas.profile import MasterProfileData
    return MasterProfileData(**kwargs)


# ---------------------------------------------------------------------------
# TestSchemaDefaults
# ---------------------------------------------------------------------------


class TestSchemaDefaults:
    def test_work_entry_list_fields_default_to_empty(self):
        e = _work_entry()
        assert e.role_aliases == []
        assert e.responsibilities == []
        assert e.achievements == []
        assert e.technologies == []

    def test_work_entry_optional_fields_default_to_none(self):
        e = _work_entry()
        assert e.location is None
        assert e.end_date is None
        assert e.industry_context is None
        assert e.team_size is None
        assert e.budget_managed is None

    def test_skill_default_proficiency_and_category(self):
        from applire.schemas.profile import Skill
        s = Skill(name="Python")
        assert s.proficiency == "intermediate"
        assert s.category == "technical"

    def test_master_profile_data_list_sections_default_to_empty(self):
        p = _profile()
        assert p.work_experience == []
        assert p.education == []
        assert p.certifications == []
        assert p.skills == []
        assert p.languages == []
        assert p.publications == []
        assert p.volunteer_activities == []

    def test_personal_info_name_defaults_to_empty_string(self):
        from applire.schemas.profile import PersonalInfo
        pi = PersonalInfo()
        assert pi.name == ""
        assert pi.email is None
        assert pi.phone is None

    def test_conflict_resolved_defaults_to_false(self):
        from applire.schemas.profile import Conflict
        c = Conflict(section="work_experience", field="start_date",
                     existing_value="2020-01", incoming_value="2020-03", source="cv_upload")
        assert c.resolved is False

    def test_conflict_has_auto_generated_uuid(self):
        from applire.schemas.profile import Conflict
        c1 = Conflict(section="s", field="f", existing_value="a", incoming_value="b", source="x")
        c2 = Conflict(section="s", field="f", existing_value="a", incoming_value="b", source="x")
        assert c1.conflict_id != c2.conflict_id
        uuid.UUID(c1.conflict_id)  # must be valid UUID — raises if not

    def test_conflict_resolution_request_accepts_valid_literals(self):
        from applire.schemas.profile import ConflictResolutionRequest
        for resolution in ("existing", "incoming", "manual"):
            req = ConflictResolutionRequest(resolution=resolution)
            assert req.resolution == resolution

    def test_conflict_resolution_request_manual_value_can_be_none(self):
        from applire.schemas.profile import ConflictResolutionRequest
        req = ConflictResolutionRequest(resolution="manual", value=None)
        assert req.value is None

    def test_conflict_resolution_request_manual_value_can_be_set(self):
        from applire.schemas.profile import ConflictResolutionRequest
        req = ConflictResolutionRequest(resolution="manual", value="2020-02")
        assert req.value == "2020-02"

    def test_certification_coerces_partial_date_obtained(self):
        from datetime import date
        from applire.schemas.profile import Certification
        assert Certification(name="AWS SA", date_obtained="2023").date_obtained == date(2023, 1, 1)
        assert Certification(name="CKA", date_obtained="2023-06").date_obtained == date(2023, 6, 1)
        assert Certification(name="CKA", date_obtained="2023-06-15").date_obtained == date(2023, 6, 15)
        assert Certification(name="X", expiry_date="2025").expiry_date == date(2025, 1, 1)
        assert Certification(name="X", date_obtained=None).date_obtained is None
        # unparseable → None, never raises (the bug was that it raised)
        assert Certification(name="X", date_obtained="sometime in 2023").date_obtained is None
        # European format and YYYY-MM on expiry_date
        assert Certification(name="X", date_obtained="15.06.2023").date_obtained == date(2023, 6, 15)
        assert Certification(name="X", expiry_date="2025-06").expiry_date == date(2025, 6, 1)


# ---------------------------------------------------------------------------
# TestLegacyMigration
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    def test_work_history_migrates_to_work_experience(self):
        from applire.schemas.profile import MasterProfileData
        data = {"work_history": [{"company": "Old Corp", "role": "Dev"}]}
        p = MasterProfileData(**data)
        assert len(p.work_experience) == 1
        assert p.work_experience[0].company == "Old Corp"

    def test_bullets_migrates_to_responsibilities(self):
        from applire.schemas.profile import MasterProfileData
        data = {"work_history": [{"company": "X", "role": "Y", "bullets": ["Task A", "Task B"]}]}
        p = MasterProfileData(**data)
        assert p.work_experience[0].responsibilities == ["Task A", "Task B"]

    def test_contact_migrates_to_personal_info(self):
        from applire.schemas.profile import MasterProfileData
        data = {"contact": {"name": "Alice", "email": "alice@example.com"}}
        p = MasterProfileData(**data)
        assert p.personal_info.name == "Alice"
        assert p.personal_info.email == "alice@example.com"

    def test_linkedin_key_migrates_to_linkedin_url(self):
        from applire.schemas.profile import MasterProfileData
        data = {"contact": {"name": "Bob", "linkedin": "https://linkedin.com/in/bob"}}
        p = MasterProfileData(**data)
        assert p.personal_info.linkedin_url == "https://linkedin.com/in/bob"

    def test_skills_str_list_migrates_to_skill_objects(self):
        from applire.schemas.profile import MasterProfileData, Skill
        data = {"skills": ["Python", "Docker"]}
        p = MasterProfileData(**data)
        assert len(p.skills) == 2
        assert all(isinstance(s, Skill) for s in p.skills)
        assert p.skills[0].name == "Python"
        assert p.skills[0].proficiency == "intermediate"
        assert p.skills[0].category == "technical"


# ---------------------------------------------------------------------------
# TestCompletenessScore
# ---------------------------------------------------------------------------


class TestCompletenessScore:
    def test_empty_profile_score_is_zero(self):
        p = _profile()
        assert p.calculate_completeness() == 0.0

    def test_profile_with_work_experience_only_scores_partial(self):
        # _work_entry() has start_date only (1/3 floor fields) — richness 0.33;
        # work_experience weight 0.30 → score = 0.30 * 0.33 = 0.10 (richness-based,
        # not the old presence-only 0.30).
        p = _profile(work_experience=[_work_entry()])
        score = p.calculate_completeness()
        assert 0.0 < score < 0.30

    def test_profile_with_full_work_entry_scores_0_30(self):
        # A work entry with all floor fields present earns the full work weight.
        p = _profile(work_experience=[_work_entry(
            start_date="2020-01", end_date="2023-12", achievements=["X"])])
        assert p.calculate_completeness() == 0.30

    def test_profile_with_work_and_education_scores_0_50(self):
        from applire.schemas.profile import EducationEntry
        # Use a fully-rich work entry so the work section contributes its full weight.
        p = _profile(
            work_experience=[_work_entry(
                start_date="2020-01", end_date="2023-12", achievements=["X"])],
            education=[EducationEntry(institution="TU Berlin", degree="B.Sc.")],
        )
        assert p.calculate_completeness() == 0.50

    def test_fully_populated_profile_score_is_near_1(self):
        from applire.schemas.profile import (
            Certification, EducationEntry, Language, MasterProfileData,
            PersonalInfo, ProfessionalSummary, Publication, Skill, VolunteerActivity,
        )
        # Use a rich work entry (all floor fields) so work section scores fully.
        p = MasterProfileData(
            personal_info=PersonalInfo(name="Ana"),
            professional_summary=ProfessionalSummary(en="Summary"),
            work_experience=[_work_entry(
                start_date="2020-01", end_date="2023-12", achievements=["Led team"])],
            education=[EducationEntry(institution="ETH", degree="M.Sc.")],
            skills=[Skill(name="Python")],
            languages=[Language(language="German", level="C2")],
            certifications=[Certification(name="AWS", issuing_organization="Amazon")],
            publications=[Publication(title="Paper")],
            volunteer_activities=[VolunteerActivity(role="Mentor", organization="Code4Good")],
        )
        score = p.calculate_completeness()
        assert 0.95 <= score <= 1.0

    def test_completeness_returns_float_rounded_to_two_decimals(self):
        from applire.schemas.profile import Skill
        p = _profile(skills=[Skill(name="Python")])
        score = p.calculate_completeness()
        assert isinstance(score, float)
        assert score == round(score, 2)


# ---------------------------------------------------------------------------
# TestSQLitePersistence
# ---------------------------------------------------------------------------


class TestSQLitePersistence:
    @pytest.mark.asyncio
    async def test_create_and_read_master_profile(self, sqlite_session):
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        from tests.support.profile_factory import make_master_profile

        profile_data = {"personal_info": {"name": "Alice"}, "work_experience": []}
        record = make_master_profile(profile_json=profile_data)
        sqlite_session.add(record)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == record.id)
        )).scalar_one()
        assert row.profile_json["personal_info"]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_profile_json_round_trips_role_aliases(self, sqlite_session):
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        from tests.support.profile_factory import make_master_profile

        data = {
            "work_experience": [{
                "company": "Acme",
                "role": "Team Lead",
                "role_aliases": ["2nd Level Support", "Senior Dev"],
            }]
        }
        record = make_master_profile(profile_json=data)
        sqlite_session.add(record)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == record.id)
        )).scalar_one()
        aliases = row.profile_json["work_experience"][0]["role_aliases"]
        assert "2nd Level Support" in aliases
        assert "Senior Dev" in aliases

    @pytest.mark.asyncio
    async def test_profile_json_round_trips_enrichment_history(self, sqlite_session):
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        from tests.support.profile_factory import make_master_profile

        data = {
            "metadata": {
                "enrichment_history": [{
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "cv_upload",
                    "changes": [],
                }]
            }
        }
        record = make_master_profile(profile_json=data)
        sqlite_session.add(record)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == record.id)
        )).scalar_one()
        history = row.profile_json["metadata"]["enrichment_history"]
        assert len(history) == 1
        assert history[0]["source"] == "cv_upload"

    @pytest.mark.asyncio
    async def test_profile_json_round_trips_pending_conflicts(self, sqlite_session):
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        from tests.support.profile_factory import make_master_profile

        conflict_id = str(uuid.uuid4())
        data = {
            "metadata": {
                "pending_conflicts": [{
                    "conflict_id": conflict_id,
                    "section": "work_experience",
                    "field": "start_date",
                    "existing_value": "2020-01",
                    "incoming_value": "2020-03",
                    "source": "cv_upload",
                    "resolved": False,
                }]
            }
        }
        record = make_master_profile(profile_json=data)
        sqlite_session.add(record)
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == record.id)
        )).scalar_one()
        pending = row.profile_json["metadata"]["pending_conflicts"]
        assert len(pending) == 1
        assert pending[0]["conflict_id"] == conflict_id

    @pytest.mark.asyncio
    async def test_deleted_at_defaults_to_null(self, sqlite_session):
        from applire.models.profile import MasterProfile

        from tests.support.profile_factory import make_master_profile

        record = make_master_profile(profile_json={})
        sqlite_session.add(record)
        await sqlite_session.commit()
        assert record.deleted_at is None

    @pytest.mark.asyncio
    async def test_soft_delete_sets_deleted_at(self, sqlite_session):
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        from tests.support.profile_factory import make_master_profile

        record = make_master_profile(profile_json={})
        sqlite_session.add(record)
        await sqlite_session.commit()

        now = datetime.now(timezone.utc)
        record.deleted_at = now
        await sqlite_session.commit()

        row = (await sqlite_session.execute(
            select(MasterProfile).where(MasterProfile.id == record.id)
        )).scalar_one()
        assert row.deleted_at is not None
